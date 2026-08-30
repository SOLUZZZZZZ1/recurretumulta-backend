#!/usr/bin/env python3
"""Construye el snapshot informativo DIR3/SIR usado por Presenter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rtm_presenter_directory import PresenterDirectory
from rtm_presenter_directory_importer import (
    build_directory_snapshot,
    write_directory_snapshot,
)


RTM_PRESENTER_DIRECTORY_BUILD_VERSION = (
    "rtm_presenter_directory_build_v1_0"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sir", type=Path, required=True)
    parser.add_argument("--eell-units", type=Path, required=True)
    parser.add_argument("--localities", type=Path, required=True)
    parser.add_argument("--provinces", type=Path, required=True)
    parser.add_argument("--communities", type=Path, required=True)
    parser.add_argument("--official-listing-modified-at", required=True)
    parser.add_argument("--created-at")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    payload = build_directory_snapshot(
        sir_path=args.sir,
        eell_units_path=args.eell_units,
        localities_path=args.localities,
        provinces_path=args.provinces,
        communities_path=args.communities,
        official_listing_modified_at=args.official_listing_modified_at,
        created_at=args.created_at,
    )
    write_directory_snapshot(args.output, payload)
    verified = PresenterDirectory.from_path(args.output)
    result = {
        "ok": True,
        "authority": "rtm_presenter_directory_build",
        "version": RTM_PRESENTER_DIRECTORY_BUILD_VERSION,
        "read_only_sources": True,
        "database_used": False,
        "network_used": False,
        "external_effects_executed": False,
        "profiles_created": False,
        "destinations_activated": False,
        "reference_only": True,
        "snapshot_id": verified.snapshot_id,
        "source_listed_modified_at": verified.official_listing_modified_at,
        "entry_count": len(verified.entries),
        "sir_listed_count": sum(
            bool(entry["sir_listed"]) for entry in verified.entries
        ),
        "output": str(args.output),
    }
    print(
        json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if args.compact
        else json.dumps(result, ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
