#!/usr/bin/env python3
"""Fail when a repository text file contains an unequivocal secret signature.

The scanner is deliberately dependency-free. Findings contain only a repository
path, a line number and a credential type; matched values are never retained in a
finding or written to stdout.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Pattern


MAX_LINE_BYTES = 2 * 1024 * 1024
_BINARY_SAMPLE_BYTES = 8192
_MAX_HISTORY_BLOB_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: Pattern[str]
    value_group: int = 1
    validator: Callable[[str], bool] | None = None


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _is_signed_jwt(value: str) -> bool:
    try:
        header_part, payload_part, signature_part = value.split(".")
        header = json.loads(_b64url_decode(header_part))
        payload = json.loads(_b64url_decode(payload_part))
        signature = _b64url_decode(signature_part)
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return False
    algorithm = str(header.get("alg") or "").strip().lower()
    return bool(algorithm and algorithm != "none" and len(signature) >= 16)


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
    )


def _is_opaque_bearer(value: str) -> bool:
    if _is_signed_jwt(value):
        return False
    lowered = value.lower()
    if any(
        marker in lowered
        for marker in ("example", "placeholder", "synthetic", "dummy", "redacted")
    ):
        return False
    return len(value) >= 32 and _entropy(value) >= 3.5


def _is_assigned_secret(value: str) -> bool:
    """Recognize opaque first-party credentials without flagging fixtures."""

    lowered = value.lower()
    if any(
        marker in lowered
        for marker in (
            "example",
            "placeholder",
            "synthetic",
            "dummy",
            "redacted",
            "changeme",
            "replace-me",
            "ci-",
            "staging-",
        )
    ):
        return False
    if value.startswith(("${", "{{", "$", "%")):
        return False
    return len(value) >= 20 and _entropy(value) >= 3.5


def _is_basic_credential(value: str) -> bool:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return b":" in decoded and len(decoded) >= 10


_RULES = (
    _Rule(
        "pem_private_key",
        re.compile(
            r"(-----BEGIN "
            r"(?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
            r"|-----BEGIN PGP "
            r"PRIVATE KEY BLOCK-----)"
        ),
    ),
    _Rule(
        "github_token",
        re.compile(
            r"\b((?:gh[pousr]_[A-Za-z0-9_]{20,255}"
            r"|github_pat_[A-Za-z0-9_]{22,255}))\b"
        ),
    ),
    _Rule(
        "openai_api_key",
        re.compile(r"\b(sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{20,})\b"),
    ),
    _Rule(
        "stripe_secret_key",
        re.compile(r"\b((?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,})\b"),
    ),
    _Rule(
        "aws_access_key_id",
        re.compile(
            r"\b((?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|A3T[A-Z0-9])"
            r"[A-Z0-9]{16})\b"
        ),
    ),
    _Rule(
        "aws_secret_access_key",
        re.compile(
            r"(?i)\b(?:aws_secret_access_key|aws_secret_key)\b\s*[:=]\s*"
            r"[\"']?([A-Za-z0-9/+=]{40})(?=$|[^A-Za-z0-9/+=])"
        ),
    ),
    _Rule(
        "slack_token",
        re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    ),
    _Rule(
        "sendgrid_api_key",
        re.compile(r"\b(SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})\b"),
    ),
    _Rule(
        "google_api_key",
        re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b"),
    ),
    _Rule(
        "assigned_application_secret",
        re.compile(
            r"(?i)\b("
            r"(?:RTM_[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|KEY))"
            r"|OPERATOR_TOKEN|ADMIN_TOKEN|SMTP_PASSWORD|B2_APPLICATION_KEY"
            r"|REG_PROVIDER_TOKEN|STRIPE_WEBHOOK_SECRET"
            r")\b\s*[:=]\s*[\"']?([A-Za-z0-9_~+/=.-]{20,})"
        ),
        value_group=2,
        validator=_is_assigned_secret,
    ),
    _Rule(
        "signed_jwt",
        re.compile(
            r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{16,})\b"
        ),
        validator=_is_signed_jwt,
    ),
    _Rule(
        "bearer_credential",
        re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{32,})"),
        validator=_is_opaque_bearer,
    ),
    _Rule(
        "basic_auth_credential",
        re.compile(r"(?i)\bBasic\s+([A-Za-z0-9+/]{16,}={0,2})"),
        validator=_is_basic_credential,
    ),
    _Rule(
        "url_with_userinfo",
        re.compile(
            r"(?i)\b([A-Za-z][A-Za-z0-9+.-]*://"
            r"[^/\s:@<>\"']+:[^/@\s<>\"']+@"
            r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?)"
        ),
    ),
)


# These fingerprints identify existing, deliberately fake URL credentials used
# by tests (and the ephemeral PostgreSQL CI service). The value and the file
# must both match, so a different credential in the same fixture is still found.
_SYNTHETIC_FIXTURE_FINGERPRINTS: dict[str, frozenset[str]] = {
    ".github/workflows/rtm-core-ci.yml": frozenset(
        {"4da19b2e3e05f7ec90eb6f74245c030e34c7b623367c4b7e83b63aa62e2bee4e"}
    ),
    "tests/test_rtm_presenter_service.py": frozenset(
        {"79a2adc70b4f63416775ba28ee0290184ec17c4ddab87a6af9569e74c545c0c7"}
    ),
    "tests/test_rtm_connect_c7_assisted_policy.py": frozenset(
        {"3443baaa0cbb77e81cc774ef6a78022f88289f4d82bc05912aaa9cb6b61e8a95"}
    ),
    "tests/test_rtm_connect_c5_supervisor_policy.py": frozenset(
        {"3443baaa0cbb77e81cc774ef6a78022f88289f4d82bc05912aaa9cb6b61e8a95"}
    ),
    "tests/test_rtm_connect_c6_provider_policy.py": frozenset(
        {"3443baaa0cbb77e81cc774ef6a78022f88289f4d82bc05912aaa9cb6b61e8a95"}
    ),
    "tests/test_rtm_connect_c8_production_policy.py": frozenset(
        {"3443baaa0cbb77e81cc774ef6a78022f88289f4d82bc05912aaa9cb6b61e8a95"}
    ),
    "tests/test_rtm_environment_contract.py": frozenset(
        {"3443baaa0cbb77e81cc774ef6a78022f88289f4d82bc05912aaa9cb6b61e8a95"}
    ),
    "tests/test_partner_input_security.py": frozenset(
        {"aa33a9bf9426110a807b336790af7b206b76cbda97bb1d7a5c13dd65889f37e2"}
    ),
    "tests/test_database_engine_lifecycle.py": frozenset(
        {"c7d5e251996c65cb1e22886056bfb655f8e5422f31402387d5997f6d7188e468"}
    ),
    "tests/test_rtm_http_security.py": frozenset(
        {"d617d72dd3975866cf204605e6f845f2eeae33b7ad1c2b4d6c1c22f148580d5e"}
    ),
}


def _is_allowlisted(path: str, kind: str, value: str) -> bool:
    if kind != "url_with_userinfo":
        return False
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest in _SYNTHETIC_FIXTURE_FINGERPRINTS.get(path, ())


def scan_text(text: str, *, path: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule in _RULES:
            for match in rule.pattern.finditer(line):
                value = match.group(rule.value_group)
                if rule.validator is not None and not rule.validator(value):
                    continue
                if _is_allowlisted(path, rule.kind, value):
                    continue
                identity = (line_number, rule.kind)
                if identity not in seen:
                    findings.append(Finding(path=path, line=line_number, kind=rule.kind))
                    seen.add(identity)
    return findings


def _looks_binary(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control_bytes = sum(byte < 9 or (13 < byte < 32) for byte in sample)
    return control_bytes / len(sample) > 0.20


def scan_file(file_path: Path, *, display_path: str) -> list[Finding]:
    if file_path.is_symlink():
        return []
    try:
        with file_path.open("rb") as handle:
            sample = handle.read(_BINARY_SAMPLE_BYTES)
            if _looks_binary(sample):
                return []
            handle.seek(0)
            findings: list[Finding] = []
            for line_number, raw_line in enumerate(handle, start=1):
                if len(raw_line) > MAX_LINE_BYTES:
                    findings.append(
                        Finding(
                            path=display_path,
                            line=line_number,
                            kind="oversized_text_line",
                        )
                    )
                    continue
                line = raw_line.decode("utf-8", errors="replace")
                for finding in scan_text(line, path=display_path):
                    findings.append(
                        Finding(
                            path=finding.path,
                            line=line_number,
                            kind=finding.kind,
                        )
                    )
            return findings
    except FileNotFoundError:
        # A tracked file can legitimately be deleted in the working tree.
        return []
    except OSError:
        return [Finding(path=display_path, line=0, kind="unreadable_file")]


def _scan_blob(
    content: bytes,
    *,
    display_path: str,
    policy_path: str | None = None,
) -> list[Finding]:
    if len(content) > _MAX_HISTORY_BLOB_BYTES:
        return [Finding(path=display_path, line=0, kind="oversized_history_blob")]
    if _looks_binary(content[:_BINARY_SAMPLE_BYTES]):
        return []

    findings: list[Finding] = []
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if len(raw_line) > MAX_LINE_BYTES:
            findings.append(
                Finding(
                    path=display_path,
                    line=line_number,
                    kind="oversized_text_line",
                )
            )
            continue
        line = raw_line.decode("utf-8", errors="replace")
        for finding in scan_text(line, path=policy_path or display_path):
            findings.append(
                Finding(
                    path=display_path,
                    line=line_number,
                    kind=finding.kind,
                )
            )
    return findings


def _repository_paths(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield tracked plus non-ignored untracked files from the working tree."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not enumerate repository files") from exc

    for raw_path in result.stdout.split(b"\x00"):
        if not raw_path:
            continue
        relative_path = raw_path.decode("utf-8", errors="surrogateescape")
        yield relative_path, root / relative_path


def scan_repository(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for relative_path, file_path in _repository_paths(root.resolve()):
        findings.extend(scan_file(file_path, display_path=relative_path))
    return findings


def _history_blobs(root: Path) -> Iterator[tuple[str, str, int]]:
    """Yield each reachable Git blob once, without materialising its content."""

    try:
        objects = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--objects", "--all", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        candidates: list[tuple[str, str]] = []
        pending_object_id = ""
        for record in objects.stdout.split(b"\x00"):
            if record.startswith(b"path=") and pending_object_id:
                raw_path = record[len(b"path=") :]
                if raw_path:
                    path = raw_path.decode("utf-8", errors="surrogateescape")
                    candidates.append((pending_object_id, path))
                pending_object_id = ""
                continue
            raw_object_id, separator, raw_path = record.partition(b" ")
            object_id = raw_object_id.decode("ascii", errors="ignore")
            if not re.fullmatch(r"[0-9a-f]{40,64}", object_id):
                pending_object_id = ""
                continue
            if separator and raw_path:
                path = raw_path.decode("utf-8", errors="surrogateescape")
                candidates.append((object_id, path))
                pending_object_id = ""
            else:
                # With ``-z``, current Git versions emit the object id and a
                # following ``path=...`` record separately.
                pending_object_id = object_id

        if not candidates:
            return
        checked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ],
            input="".join(f"{object_id}\n" for object_id, _ in candidates).encode(
                "ascii"
            ),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not enumerate repository history") from exc

    metadata = checked.stdout.decode("ascii", errors="replace").splitlines()
    if len(metadata) != len(candidates):
        raise RuntimeError("could not verify repository history objects")
    for (object_id, path), line in zip(candidates, metadata):
        fields = line.split()
        if len(fields) != 3 or fields[0] != object_id or fields[1] != "blob":
            continue
        try:
            size = int(fields[2], 10)
        except ValueError as exc:
            raise RuntimeError("invalid repository history object size") from exc
        yield object_id, path, size


def _history_message_objects(root: Path) -> Iterator[tuple[str, str, int]]:
    """Yield reachable commits and annotated tags whose messages are public."""

    try:
        commits = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--all"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        tags = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "for-each-ref",
                "--format=%(objectname) %(objecttype)",
                "refs/tags",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )

        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_object_id in commits.stdout.splitlines():
            object_id = raw_object_id.decode("ascii", errors="ignore").strip()
            if re.fullmatch(r"[0-9a-f]{40,64}", object_id) and object_id not in seen:
                candidates.append((object_id, "commit"))
                seen.add(object_id)
        for raw_line in tags.stdout.splitlines():
            raw_object_id, separator, raw_type = raw_line.partition(b" ")
            object_id = raw_object_id.decode("ascii", errors="ignore")
            object_type = raw_type.decode("ascii", errors="ignore") if separator else ""
            if (
                object_type == "tag"
                and re.fullmatch(r"[0-9a-f]{40,64}", object_id)
                and object_id not in seen
            ):
                candidates.append((object_id, "tag"))
                seen.add(object_id)

        if not candidates:
            return
        checked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ],
            input="".join(f"{object_id}\n" for object_id, _ in candidates).encode(
                "ascii"
            ),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not enumerate repository history messages") from exc

    metadata = checked.stdout.decode("ascii", errors="replace").splitlines()
    if len(metadata) != len(candidates):
        raise RuntimeError("could not verify repository history messages")
    for (object_id, expected_type), line in zip(candidates, metadata):
        fields = line.split()
        if (
            len(fields) != 3
            or fields[0] != object_id
            or fields[1] != expected_type
        ):
            raise RuntimeError("repository history message object changed")
        try:
            size = int(fields[2], 10)
        except ValueError as exc:
            raise RuntimeError("invalid repository history object size") from exc
        yield object_id, expected_type, size


def _read_history_object(root: Path, object_id: str, object_type: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", object_type, object_id],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not read repository history object") from exc
    return result.stdout


def scan_history(root: Path) -> list[Finding]:
    """Scan reachable files plus commit/tag messages, reporting metadata only."""

    findings: list[Finding] = []
    for object_id, path, size in _history_blobs(root.resolve()):
        display_path = f"history/{object_id[:12]}/{path}"
        if size > _MAX_HISTORY_BLOB_BYTES:
            findings.append(
                Finding(
                    path=display_path,
                    line=0,
                    kind="oversized_history_blob",
                )
            )
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", object_id],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("could not read repository history blob") from exc
        if len(result.stdout) != size:
            raise RuntimeError("repository history blob size changed")
        findings.extend(
            _scan_blob(
                result.stdout,
                display_path=display_path,
                policy_path=path,
            )
        )
    for object_id, object_type, size in _history_message_objects(root.resolve()):
        display_path = f"history/{object_id[:12]}/{object_type}-message"
        if size > _MAX_HISTORY_BLOB_BYTES:
            findings.append(
                Finding(
                    path=display_path,
                    line=0,
                    kind="oversized_history_message",
                )
            )
            continue
        content = _read_history_object(root.resolve(), object_id, object_type)
        if len(content) != size:
            raise RuntimeError("repository history message size changed")
        _headers, separator, message = content.partition(b"\n\n")
        if not separator:
            message = b""
        if _looks_binary(message[:_BINARY_SAMPLE_BYTES]):
            findings.append(
                Finding(
                    path=display_path,
                    line=0,
                    kind="binary_repository_history_message",
                )
            )
            continue
        try:
            message.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            findings.append(
                Finding(
                    path=display_path,
                    line=0,
                    kind="invalid_utf8_repository_history_message",
                )
            )
            continue
        findings.extend(_scan_blob(message, display_path=display_path))
    return findings


def _safe_path(path: str) -> str:
    return "".join(
        character if character.isprintable() and character not in "\r\n\t" else "?"
        for character in path
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan tracked and non-ignored untracked repository text files "
            "for credential signatures."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of scripts/)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            "also scan every blob and commit/annotated-tag message reachable "
            "from local Git refs"
        ),
    )
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    try:
        findings = scan_repository(arguments.root)
        if arguments.history:
            findings.extend(scan_history(arguments.root))
    except RuntimeError:
        print("Secret scan failed: repository files could not be enumerated.", file=sys.stderr)
        return 2

    if findings:
        print("Secret scan failed: credential signatures were found.", file=sys.stderr)
        for finding in findings:
            print(
                f"{_safe_path(finding.path)}:{finding.line}:{finding.kind}",
                file=sys.stderr,
            )
        print(f"Findings: {len(findings)}", file=sys.stderr)
        return 1

    print(
        "Secret scan passed: no credential signatures found in tracked or "
        "non-ignored untracked text files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
