"""Directorio informativo DIR3/SIR para RTM Presenter.

El directorio identifica organismos y constancia registral en una fotografia
oficial. Nunca crea un perfil de procedimiento, decide el organo competente ni
habilita por si solo una presentacion. Los perfiles operativos siguen viviendo
en ``rtm_presenter_destination_profiles`` y conservan su doble verificacion.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


RTM_PRESENTER_DIRECTORY_VERSION = "rtm_presenter_directory_v1_0"
RTM_PRESENTER_DIRECTORY_SNAPSHOT_VERSION = (
    "rtm_presenter_directory_snapshot_v1_0"
)
DEFAULT_DIRECTORY_SNAPSHOT = (
    Path(__file__).resolve().parent
    / "staging"
    / "fixtures"
    / "rtm_presenter_directory_snapshot_2026-06-30.json.gz"
)

_DIRECTORY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{8}$")
_OFFICE_CODE_RE = re.compile(r"^O[0-9A-Z]{8}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ROOT_KEYS = frozenset(
    {
        "contract_version",
        "snapshot_id",
        "created_at",
        "source",
        "stats",
        "entries",
    }
)
_ALLOWED_SOURCE_KEYS = frozenset(
    {
        "official_source_url",
        "official_listing_modified_at",
        "files",
    }
)
_ALLOWED_ENTRY_KEYS = frozenset(
    {
        "directory_code",
        "display_name",
        "administration_level",
        "autonomous_community",
        "province",
        "locality_name",
        "entity_type_code",
        "sir_listed",
        "sir_offices",
        "aliases",
        "source_basis",
    }
)
_ALLOWED_OFFICE_KEYS = frozenset({"office_code", "office_name"})
_ALLOWED_STATS_KEYS = frozenset(
    {
        "entry_count",
        "sir_listed_count",
        "locality_verified_count",
        "locality_missing_eell_count",
        "duplicate_island_rows_collapsed",
    }
)
_EXPECTED_SOURCE_FILES = frozenset(
    {
        "Listado de oficinas SIR.xlsx",
        "Listado Unidades EELL.xlsx",
        "Catalogo de Localidades.xlsx",
        "Catalogo de Provincias.xlsx",
        "Catalogo-de-Comunidades-Autonomas.xlsx",
    }
)
_ENTRY_PROJECTION_KEYS = (
    "directory_code",
    "display_name",
    "administration_level",
    "autonomous_community",
    "province",
    "locality_name",
    "entity_type_code",
    "sir_listed",
    "sir_offices",
    "source_basis",
)


class PresenterDirectoryError(RuntimeError):
    """El snapshot no cumple el contrato cerrado del directorio."""


class PresenterDirectoryProvider(Protocol):
    def source_projection(self) -> dict[str, Any]: ...

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def directory_snapshot_sha256(payload_without_id: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload_without_id)).hexdigest()


def _clean_text(value: object, *, maximum: int = 240) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) > maximum:
        raise PresenterDirectoryError("directory_text_out_of_contract")
    return clean


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", _clean_text(value).casefold())
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )


def _validated_offices(
    value: object, *, sir_listed: bool
) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise PresenterDirectoryError("directory_offices_invalid")
    offices: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _ALLOWED_OFFICE_KEYS:
            raise PresenterDirectoryError("directory_office_invalid")
        code = _clean_text(raw.get("office_code"), maximum=16).upper()
        name = _clean_text(raw.get("office_name"))
        if not _OFFICE_CODE_RE.fullmatch(code) or not name:
            raise PresenterDirectoryError("directory_office_invalid")
        key = (code, name)
        if key not in seen:
            offices.append({"office_code": code, "office_name": name})
            seen.add(key)
    if sir_listed != bool(offices):
        raise PresenterDirectoryError("directory_sir_state_invalid")
    return tuple(offices)


def _validated_entry(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _ALLOWED_ENTRY_KEYS:
        raise PresenterDirectoryError("directory_entry_keys_invalid")
    code = _clean_text(raw.get("directory_code"), maximum=16).upper()
    display_name = _clean_text(raw.get("display_name"))
    if not _DIRECTORY_CODE_RE.fullmatch(code) or not display_name:
        raise PresenterDirectoryError("directory_entry_identity_invalid")
    sir_listed = raw.get("sir_listed")
    if type(sir_listed) is not bool:
        raise PresenterDirectoryError("directory_sir_state_invalid")
    source_basis = _clean_text(raw.get("source_basis"), maximum=32)
    if source_basis not in {"sir", "dir3_eell"}:
        raise PresenterDirectoryError("directory_source_basis_invalid")
    aliases_raw = raw.get("aliases")
    if not isinstance(aliases_raw, list) or len(aliases_raw) > 12:
        raise PresenterDirectoryError("directory_aliases_invalid")
    aliases = tuple(
        alias
        for alias in dict.fromkeys(
            _clean_text(value, maximum=160) for value in aliases_raw
        )
        if alias
    )
    offices = _validated_offices(raw.get("sir_offices"), sir_listed=sir_listed)
    return {
        "directory_code": code,
        "display_name": display_name,
        "administration_level": _clean_text(raw.get("administration_level")),
        "autonomous_community": _clean_text(raw.get("autonomous_community")),
        "province": _clean_text(raw.get("province")),
        "locality_name": _clean_text(raw.get("locality_name")),
        "entity_type_code": _clean_text(
            raw.get("entity_type_code"), maximum=8
        ).upper(),
        "sir_listed": sir_listed,
        "sir_offices": offices,
        "aliases": aliases,
        "source_basis": source_basis,
    }


@dataclass(frozen=True)
class PresenterDirectory:
    snapshot_id: str
    created_at: str
    official_source_url: str
    official_listing_modified_at: str
    source_files: Mapping[str, str]
    entries: Sequence[Mapping[str, Any]]
    search_rows: Sequence[tuple[str, str, str, tuple[str, ...], str]]

    @classmethod
    def from_path(cls, path: Path | str) -> "PresenterDirectory":
        exact_path = Path(path)
        try:
            with gzip.open(exact_path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
        except Exception as exc:
            raise PresenterDirectoryError("directory_snapshot_unreadable") from exc
        if not isinstance(payload, Mapping) or set(payload) != _ALLOWED_ROOT_KEYS:
            raise PresenterDirectoryError("directory_snapshot_keys_invalid")
        if payload.get("contract_version") != RTM_PRESENTER_DIRECTORY_SNAPSHOT_VERSION:
            raise PresenterDirectoryError("directory_snapshot_version_invalid")
        snapshot_id = _clean_text(payload.get("snapshot_id"), maximum=64).lower()
        if not _SHA256_RE.fullmatch(snapshot_id):
            raise PresenterDirectoryError("directory_snapshot_id_invalid")
        unsigned = {
            key: value for key, value in payload.items() if key != "snapshot_id"
        }
        if directory_snapshot_sha256(unsigned) != snapshot_id:
            raise PresenterDirectoryError("directory_snapshot_hash_mismatch")
        created_at = _clean_text(payload.get("created_at"), maximum=40)
        try:
            created_datetime = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise PresenterDirectoryError("directory_created_at_invalid") from exc
        if created_datetime.tzinfo is None:
            raise PresenterDirectoryError("directory_created_at_invalid")
        source = payload.get("source")
        if not isinstance(source, Mapping) or set(source) != _ALLOWED_SOURCE_KEYS:
            raise PresenterDirectoryError("directory_source_invalid")
        official_url = _clean_text(source.get("official_source_url"), maximum=500)
        if official_url != (
            "https://administracionelectronica.gob.es/ctt/dir3/descargas"
        ):
            raise PresenterDirectoryError("directory_source_url_invalid")
        listed_modified = _clean_text(
            source.get("official_listing_modified_at"), maximum=10
        )
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", listed_modified):
            raise PresenterDirectoryError("directory_source_date_invalid")
        try:
            listed_date = date.fromisoformat(listed_modified)
        except ValueError as exc:
            raise PresenterDirectoryError("directory_source_date_invalid") from exc
        if listed_date > created_datetime.date():
            raise PresenterDirectoryError("directory_source_date_invalid")
        files = source.get("files")
        if not isinstance(files, Mapping) or set(files) != _EXPECTED_SOURCE_FILES:
            raise PresenterDirectoryError("directory_source_files_invalid")
        source_files: dict[str, str] = {}
        for filename, digest in files.items():
            safe_name = _clean_text(filename, maximum=160)
            safe_digest = _clean_text(digest, maximum=64).lower()
            if not safe_name or "/" in safe_name or "\\" in safe_name:
                raise PresenterDirectoryError("directory_source_filename_invalid")
            if not _SHA256_RE.fullmatch(safe_digest):
                raise PresenterDirectoryError("directory_source_hash_invalid")
            source_files[safe_name] = safe_digest
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise PresenterDirectoryError("directory_entries_invalid")
        entries = tuple(_validated_entry(raw) for raw in raw_entries)
        codes = [str(entry["directory_code"]) for entry in entries]
        if len(codes) != len(set(codes)):
            raise PresenterDirectoryError("directory_code_collision")
        stats = payload.get("stats")
        if (
            not isinstance(stats, Mapping)
            or set(stats) != _ALLOWED_STATS_KEYS
            or any(type(stats.get(key)) is not int for key in _ALLOWED_STATS_KEYS)
            or any(stats[key] < 0 for key in _ALLOWED_STATS_KEYS)
            or stats.get("entry_count") != len(entries)
            or stats.get("sir_listed_count")
            != sum(bool(entry["sir_listed"]) for entry in entries)
        ):
            raise PresenterDirectoryError("directory_stats_invalid")
        search_rows = tuple(
            (
                str(entry["directory_code"]).casefold(),
                _fold(entry["display_name"]),
                _fold(entry["locality_name"]),
                tuple(_fold(value) for value in entry["aliases"]),
                " ".join(
                    _fold(value)
                    for value in (
                        entry["display_name"],
                        entry["directory_code"],
                        entry["administration_level"],
                        entry["autonomous_community"],
                        entry["province"],
                        entry["locality_name"],
                        entry["entity_type_code"],
                        *entry["aliases"],
                        *(
                            office["office_name"]
                            for office in entry["sir_offices"]
                        ),
                    )
                ),
            )
            for entry in entries
        )
        return cls(
            snapshot_id=snapshot_id,
            created_at=created_at,
            official_source_url=official_url,
            official_listing_modified_at=listed_modified,
            source_files=source_files,
            entries=entries,
            search_rows=search_rows,
        )

    def source_projection(self) -> dict[str, Any]:
        return {
            "available": True,
            "snapshot_id": self.snapshot_id,
            "official_source_url": self.official_source_url,
            "official_listing_modified_at": self.official_listing_modified_at,
            "reference_only": True,
            "real_public_directory_data": True,
        }

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        clean_query = _clean_text(query, maximum=100)
        folded_query = _fold(clean_query)
        if not folded_query or not 1 <= limit <= 50:
            return []
        tokens = tuple(token for token in folded_query.split() if token)
        ranked: list[tuple[tuple[Any, ...], Mapping[str, Any]]] = []
        for entry, search_row in zip(self.entries, self.search_rows):
            code = str(entry["directory_code"])
            folded_code, name, locality, aliases, haystack = search_row
            if folded_query == folded_code:
                quality = 0
            elif folded_query == name:
                quality = 1
            elif locality and folded_query == locality:
                quality = 2
            elif folded_query in aliases:
                quality = 3
            elif name.startswith(folded_query):
                quality = 4
            elif tokens and all(token in haystack for token in tokens):
                quality = 5
            elif folded_query in haystack:
                quality = 6
            else:
                continue
            ranked.append(
                (
                    (
                        quality,
                        0 if entry["sir_listed"] else 1,
                        len(str(entry["display_name"])),
                        name,
                        code,
                    ),
                    entry,
                )
            )
        ranked.sort(key=lambda item: item[0])
        results: list[dict[str, Any]] = []
        for _, entry in ranked[:limit]:
            results.append(
                {
                    **{key: entry[key] for key in _ENTRY_PROJECTION_KEYS},
                    "sir_offices": [dict(value) for value in entry["sir_offices"]],
                    "directory_snapshot_id": self.snapshot_id,
                    "source_listed_modified_at": self.official_listing_modified_at,
                    "reference_only": True,
                    "usable_as_destination": False,
                    "procedure_profile_available": False,
                    "routing_decision_available": False,
                }
            )
        return results


@dataclass(frozen=True)
class EmptyPresenterDirectory:
    reason: str = "directory_snapshot_unavailable"

    def source_projection(self) -> dict[str, Any]:
        return {
            "available": False,
            "reason": self.reason,
            "reference_only": True,
            "real_public_directory_data": False,
        }

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return []


@lru_cache(maxsize=1)
def default_presenter_directory() -> PresenterDirectory | EmptyPresenterDirectory:
    try:
        return PresenterDirectory.from_path(DEFAULT_DIRECTORY_SNAPSHOT)
    except PresenterDirectoryError:
        return EmptyPresenterDirectory()
