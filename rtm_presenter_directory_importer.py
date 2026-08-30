"""Compilador offline de listados publicos DIR3/SIR.

Solo lee XLSX y escribe un snapshot de referencia. No usa red, no toca la base
de datos y no crea perfiles de procedimiento utilizables por Presenter.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from xml.etree import ElementTree as ET

from rtm_presenter_directory import (
    RTM_PRESENTER_DIRECTORY_SNAPSHOT_VERSION,
    directory_snapshot_sha256,
)


OFFICIAL_DIR3_DOWNLOADS_URL = (
    "https://administracionelectronica.gob.es/ctt/dir3/descargas"
)
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _MAIN_NS, "r": _REL_NS, "p": _PACKAGE_REL_NS}
_DIRECTORY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{8}$")
_OFFICE_CODE_RE = re.compile(r"^O[A-Z0-9]{8}$")
_MAX_XLSX_BYTES = 25 * 1024 * 1024
_MAX_XLSX_MEMBERS = 256
_MAX_XLSX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
_MAX_XLSX_MEMBER_BYTES = 150 * 1024 * 1024
_MAX_XLSX_COLUMNS = 128
_MAX_XLSX_ROWS = 250_000


class PresenterDirectoryImportError(RuntimeError):
    pass


def _column_number(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference)
    if not match:
        raise PresenterDirectoryImportError("xlsx_cell_reference_invalid")
    number = 0
    for letter in match.group(0):
        number = number * 26 + ord(letter) - 64
    index = number - 1
    if index >= _MAX_XLSX_COLUMNS:
        raise PresenterDirectoryImportError("xlsx_column_limit_exceeded")
    return index


def _validate_archive(path: Path, archive: zipfile.ZipFile) -> None:
    if path.stat().st_size > _MAX_XLSX_BYTES:
        raise PresenterDirectoryImportError("xlsx_compressed_limit_exceeded")
    members = archive.infolist()
    if not members or len(members) > _MAX_XLSX_MEMBERS:
        raise PresenterDirectoryImportError("xlsx_member_limit_exceeded")
    if any(member.flag_bits & 0x1 for member in members):
        raise PresenterDirectoryImportError("xlsx_encrypted_member_denied")
    if any(member.file_size > _MAX_XLSX_MEMBER_BYTES for member in members):
        raise PresenterDirectoryImportError("xlsx_member_size_exceeded")
    if sum(member.file_size for member in members) > _MAX_XLSX_UNCOMPRESSED_BYTES:
        raise PresenterDirectoryImportError("xlsx_expansion_limit_exceeded")


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iterfind(".//m:t", _NS))
        for item in root.findall("m:si", _NS)
    ]


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall("p:Relationship", _NS)
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall("m:sheets/m:sheet", _NS):
        target = targets[sheet.attrib[f"{{{_REL_NS}}}id"]].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        result.append((sheet.attrib["name"], target))
    return result


def _cell_value(cell: ET.Element, strings: Sequence[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iterfind(".//m:t", _NS))
    value = cell.find("m:v", _NS)
    if value is None or value.text is None:
        return ""
    if kind == "s":
        return strings[int(value.text)]
    return value.text


def _iter_rows(
    archive: zipfile.ZipFile, target: str, strings: Sequence[str]
) -> Iterator[list[str]]:
    row_count = 0
    with archive.open(target) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != f"{{{_MAIN_NS}}}row":
                continue
            row_count += 1
            if row_count > _MAX_XLSX_ROWS:
                raise PresenterDirectoryImportError("xlsx_row_limit_exceeded")
            values: dict[int, str] = {}
            for cell in element.findall("m:c", _NS):
                values[_column_number(cell.attrib["r"])] = _cell_value(cell, strings)
            if values:
                row = [values.get(index, "") for index in range(max(values) + 1)]
                if any(str(value).strip() for value in row):
                    yield row
            element.clear()


def _read_largest_table(path: Path) -> tuple[list[str], list[list[str]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive(path, archive)
            strings = _shared_strings(archive)
            _, target = max(
                _sheet_targets(archive),
                key=lambda item: archive.getinfo(item[1]).file_size,
            )
            rows = _iter_rows(archive, target, strings)
            headers = [str(value or "").strip() for value in next(rows)]
            return headers, [
                [str(value or "").strip() for value in row]
                for row in rows
            ]
    except (KeyError, StopIteration, zipfile.BadZipFile, ET.ParseError) as exc:
        raise PresenterDirectoryImportError(
            f"xlsx_unreadable:{path.name}"
        ) from exc


def _index(headers: Sequence[str], label: str, *, prefix: bool = False) -> int:
    for index, value in enumerate(headers):
        if (value.startswith(label) if prefix else value == label):
            return index
    raise PresenterDirectoryImportError(f"xlsx_header_missing:{label}")


def _at(row: Sequence[str], index: int) -> str:
    return " ".join((row[index] if index < len(row) else "").split())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _catalog_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    headers, rows = _read_largest_table(path)
    if rows and any(re.fullmatch(r"[AN]\([0-9]+\)", value) for value in rows[0]):
        rows = rows[1:]
    return headers, rows


def _province_catalog(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    headers, rows = _catalog_rows(path)
    code_index = _index(headers, "Código", prefix=True)
    name_index = _index(headers, "Denominación Provincia")
    community_index = _index(headers, "Cod.CA")
    names: dict[str, str] = {}
    communities: dict[str, str] = {}
    for row in rows:
        code = _at(row, code_index)
        if code:
            names[code] = _at(row, name_index)
            communities[code] = _at(row, community_index)
    return names, communities


def _community_catalog(path: Path) -> dict[str, str]:
    headers, rows = _catalog_rows(path)
    code_index = _index(headers, "Código", prefix=True)
    name_index = _index(headers, "Comunidad Autónoma")
    return {
        _at(row, code_index): _at(row, name_index)
        for row in rows
        if _at(row, code_index)
    }


def _eell_units(path: Path) -> dict[str, dict[str, str]]:
    headers, rows = _read_largest_table(path)
    code_index = _index(headers, "C_ID_UD_ORGANICA")
    name_index = _index(headers, "C_DNM_UD_ORGANICA")
    type_index = _index(headers, "C_ID_TIPO_ENT_PUBLICA")
    province_code_index = _index(headers, "C_ID_AMB_PROVINCIA")
    province_name_index = _index(headers, "C_DESC_PROV")
    state_index = _index(headers, "C_ID_ESTADO")
    units: dict[str, dict[str, str]] = {}
    for row in rows:
        code = _at(row, code_index).upper()
        if not _DIRECTORY_CODE_RE.fullmatch(code) or _at(row, state_index) != "V":
            continue
        if code in units:
            raise PresenterDirectoryImportError(f"eell_unit_collision:{code}")
        units[code] = {
            "name": _at(row, name_index),
            "entity_type_code": _at(row, type_index).upper(),
            "province_code": _at(row, province_code_index),
            "province": _at(row, province_name_index),
        }
    return units


def _localities(path: Path) -> tuple[dict[str, str], int]:
    headers, rows = _catalog_rows(path)
    entity_index = _index(headers, "Cod. Entidad")
    province_index = _index(headers, "Cod. Provincia")
    locality_index = _index(headers, "Cod. Localidad", prefix=True)
    name_index = _index(headers, "Denominación de Localidad")
    by_code: dict[str, str] = {}
    duplicate_island_rows = 0
    for row in rows:
        entity_code = _at(row, entity_index)
        province_code = _at(row, province_index)
        locality_code = _at(row, locality_index)
        name = _at(row, name_index)
        directory_code = f"L{entity_code}{province_code}{locality_code}"
        if not _DIRECTORY_CODE_RE.fullmatch(directory_code) or not name:
            continue
        prior = by_code.get(directory_code)
        if prior and prior != name:
            raise PresenterDirectoryImportError(
                f"locality_name_collision:{directory_code}"
            )
        if prior:
            duplicate_island_rows += 1
        by_code[directory_code] = name
    return by_code, duplicate_island_rows


def _sir_rows(path: Path) -> list[dict[str, str]]:
    headers, rows = _read_largest_table(path)
    indexes = {
        "office_code": _index(headers, "CÓD. OFICINA"),
        "office_name": _index(headers, "DENOMINACIÓN DE OFICINA"),
        "directory_code": _index(headers, "CÓD. UNIDAD/ENTIDAD"),
        "display_name": _index(headers, "DENOMINACIÓN UNIDAD/ENTIDAD"),
        "administration_level": _index(headers, "NIVEL DE AMINISTRACIÓN"),
        "autonomous_community": _index(headers, "COMUNIDAD AUTÓNOMA"),
        "province": _index(headers, "PROVINCIA"),
    }
    result: list[dict[str, str]] = []
    for row in rows:
        item = {key: _at(row, index) for key, index in indexes.items()}
        item["directory_code"] = item["directory_code"].upper()
        item["office_code"] = item["office_code"].upper()
        if not _DIRECTORY_CODE_RE.fullmatch(item["directory_code"]):
            raise PresenterDirectoryImportError("sir_directory_code_invalid")
        if not _OFFICE_CODE_RE.fullmatch(item["office_code"]):
            raise PresenterDirectoryImportError("sir_office_code_invalid")
        if not item["display_name"] or not item["office_name"]:
            raise PresenterDirectoryImportError("sir_name_missing")
        result.append(item)
    return result


def _aliases(
    *, display_name: str, locality_name: str, directory_code: str
) -> list[str]:
    values: list[str] = []
    if locality_name:
        values.extend(
            (
                locality_name,
                f"Ayuntamiento {locality_name}",
                f"Municipio {locality_name}",
            )
        )
    folded = display_name.casefold()
    is_dgt_unit = (
        ("jefatura" in folded and ("tráfico" in folded or "trafico" in folded))
        or (
            "dirección general" in folded
            and ("tráfico" in folded or "trafico" in folded)
        )
    )
    if is_dgt_unit:
        values.extend(("DGT", "Dirección General de Tráfico", "Tráfico"))
    if directory_code == "E00130201":
        values.extend(("DGT central", "Jefatura Central de Tráfico"))
    return list(dict.fromkeys(value for value in values if value))


def build_directory_snapshot(
    *,
    sir_path: Path,
    eell_units_path: Path,
    localities_path: Path,
    provinces_path: Path,
    communities_path: Path,
    official_listing_modified_at: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", official_listing_modified_at):
        raise PresenterDirectoryImportError("official_listing_date_invalid")
    source_paths = (
        sir_path,
        eell_units_path,
        localities_path,
        provinces_path,
        communities_path,
    )
    if any(not path.is_file() for path in source_paths):
        raise PresenterDirectoryImportError("source_file_missing")

    province_names, province_communities = _province_catalog(provinces_path)
    community_names = _community_catalog(communities_path)
    units = _eell_units(eell_units_path)
    localities, duplicate_island_rows = _localities(localities_path)
    sir = _sir_rows(sir_path)

    entries: dict[str, dict[str, Any]] = {}
    offices_by_unit: dict[str, list[dict[str, str]]] = defaultdict(list)
    sir_identity: dict[str, dict[str, str]] = {}
    for row in sir:
        code = row["directory_code"]
        offices_by_unit[code].append(
            {
                "office_code": row["office_code"],
                "office_name": row["office_name"],
            }
        )
        prior = sir_identity.setdefault(
            code,
            {
                "display_name": row["display_name"],
                "administration_level": row["administration_level"],
                "autonomous_community": row["autonomous_community"],
                "province": row["province"],
            },
        )
        if prior != {
            "display_name": row["display_name"],
            "administration_level": row["administration_level"],
            "autonomous_community": row["autonomous_community"],
            "province": row["province"],
        }:
            raise PresenterDirectoryImportError(f"sir_identity_collision:{code}")

    for code, identity in sir_identity.items():
        unit = units.get(code, {})
        locality_name = localities.get(code, "")
        display_name = unit.get("name") or identity["display_name"]
        entries[code] = {
            "directory_code": code,
            "display_name": display_name,
            "administration_level": identity["administration_level"],
            "autonomous_community": identity["autonomous_community"],
            "province": identity["province"],
            "locality_name": locality_name,
            "entity_type_code": unit.get("entity_type_code", ""),
            "sir_listed": True,
            "sir_offices": sorted(
                {tuple(office.items()) for office in offices_by_unit[code]}
            ),
            "aliases": _aliases(
                display_name=display_name,
                locality_name=locality_name,
                directory_code=code,
            ),
            "source_basis": "sir",
        }
        entries[code]["sir_offices"] = [
            dict(value) for value in entries[code]["sir_offices"]
        ]

    locality_verified_count = 0
    locality_missing_eell_count = 0
    for code, locality_name in localities.items():
        unit = units.get(code)
        if not unit:
            locality_missing_eell_count += 1
            continue
        locality_verified_count += 1
        if code in entries:
            continue
        province_code = unit["province_code"]
        province = unit["province"] or province_names.get(province_code, "")
        community = community_names.get(
            province_communities.get(province_code, ""), ""
        )
        display_name = unit["name"] or locality_name
        entries[code] = {
            "directory_code": code,
            "display_name": display_name,
            "administration_level": "Administración Local",
            "autonomous_community": community,
            "province": province,
            "locality_name": locality_name,
            "entity_type_code": unit["entity_type_code"],
            "sir_listed": False,
            "sir_offices": [],
            "aliases": _aliases(
                display_name=display_name,
                locality_name=locality_name,
                directory_code=code,
            ),
            "source_basis": "dir3_eell",
        }

    normalized_entries = sorted(
        entries.values(), key=lambda item: item["directory_code"]
    )
    created = created_at or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    unsigned: dict[str, Any] = {
        "contract_version": RTM_PRESENTER_DIRECTORY_SNAPSHOT_VERSION,
        "created_at": created,
        "source": {
            "official_source_url": OFFICIAL_DIR3_DOWNLOADS_URL,
            "official_listing_modified_at": official_listing_modified_at,
            "files": {path.name: _sha256(path) for path in source_paths},
        },
        "stats": {
            "entry_count": len(normalized_entries),
            "sir_listed_count": sum(
                bool(entry["sir_listed"]) for entry in normalized_entries
            ),
            "locality_verified_count": locality_verified_count,
            "locality_missing_eell_count": locality_missing_eell_count,
            "duplicate_island_rows_collapsed": duplicate_island_rows,
        },
        "entries": normalized_entries,
    }
    return {
        "contract_version": unsigned["contract_version"],
        "snapshot_id": directory_snapshot_sha256(unsigned),
        **{key: value for key, value in unsigned.items() if key != "contract_version"},
    }


def write_directory_snapshot(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with path.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream:
            stream.write(encoded)
