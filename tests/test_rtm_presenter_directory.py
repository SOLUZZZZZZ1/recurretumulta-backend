from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from rtm_presenter_directory import (
    DEFAULT_DIRECTORY_SNAPSHOT,
    PresenterDirectory,
    PresenterDirectoryError,
    directory_snapshot_sha256,
)


class RTMPresenterDirectoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = PresenterDirectory.from_path(DEFAULT_DIRECTORY_SNAPSHOT)

    def test_snapshot_is_real_public_reference_but_never_a_destination(self):
        source = self.directory.source_projection()
        self.assertTrue(source["available"])
        self.assertTrue(source["reference_only"])
        self.assertTrue(source["real_public_directory_data"])
        self.assertEqual(source["official_listing_modified_at"], "2026-06-30")
        self.assertEqual(len(self.directory.entries), 35841)
        self.assertEqual(
            sum(bool(item["sir_listed"]) for item in self.directory.entries),
            32705,
        )
        for result in self.directory.search("Manresa", limit=20):
            self.assertFalse(result["usable_as_destination"])
            self.assertFalse(result["procedure_profile_available"])
            self.assertFalse(result["routing_decision_available"])
            self.assertTrue(result["reference_only"])

    def test_municipal_search_prefers_the_exact_locality_and_preserves_codes(self):
        result = self.directory.search("Manresa", limit=5)[0]
        self.assertEqual(result["directory_code"], "L01081136")
        self.assertEqual(result["display_name"], "Ayuntamiento de Manresa")
        self.assertEqual(result["province"], "Barcelona")
        self.assertEqual(result["autonomous_community"], "Cataluña")
        self.assertTrue(result["sir_listed"])
        self.assertEqual(
            result["sir_offices"],
            [
                {
                    "office_code": "O00011794",
                    "office_name": "REGISTRO GENERAL DEL AYUNTAMIENTO DE MANRESA",
                }
            ],
        )
        self.assertEqual(
            self.directory.search("L01081136", limit=1)[0]["directory_code"],
            "L01081136",
        )

    def test_dgt_alias_and_provincial_jefaturas_resolve_without_routing(self):
        self.assertEqual(
            self.directory.search("DGT", limit=1)[0]["directory_code"],
            "E00130201",
        )
        expected = {
            "Jefatura tráfico Barcelona": ("E03099901", "O00010233"),
            "Jefatura tráfico Lleida": ("E03101601", "O00010248"),
            "Jefatura tráfico Badajoz": ("E03099701", "O00010231"),
        }
        for query, (directory_code, office_code) in expected.items():
            with self.subTest(query=query):
                result = self.directory.search(query, limit=1)[0]
                self.assertEqual(result["directory_code"], directory_code)
                self.assertEqual(result["sir_offices"][0]["office_code"], office_code)
                self.assertFalse(result["routing_decision_available"])

    def test_projection_contains_no_contacts_credentials_or_filing_urls(self):
        forbidden = {
            "contact",
            "contactos",
            "email",
            "nif",
            "cif",
            "portal_url",
            "portal_origin",
            "storage_key",
            "bucket",
        }
        for query in ("Manresa", "DGT", "Madrid"):
            for result in self.directory.search(query, limit=20):
                self.assertFalse(forbidden.intersection(result))
                encoded = json.dumps(result, ensure_ascii=False).casefold()
                self.assertNotIn("@", encoded)
                self.assertNotIn("https://", encoded)
        for entry in self.directory.entries:
            encoded = json.dumps(entry, ensure_ascii=False).casefold()
            self.assertNotIn("@", encoded)
            self.assertNotIn("http://", encoded)
            self.assertNotIn("https://", encoded)

    def test_snapshot_hash_rejects_any_post_build_change(self):
        with gzip.open(DEFAULT_DIRECTORY_SNAPSHOT, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["entries"][0]["display_name"] = "ALTERADO"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            with self.assertRaises(PresenterDirectoryError) as denied:
                PresenterDirectory.from_path(path)
        self.assertEqual(str(denied.exception), "directory_snapshot_hash_mismatch")

    def test_closed_contract_rejects_resigned_unexpected_stats(self):
        with gzip.open(DEFAULT_DIRECTORY_SNAPSHOT, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["stats"]["unexpected_count"] = 1
        unsigned = {
            key: value for key, value in payload.items() if key != "snapshot_id"
        }
        payload["snapshot_id"] = directory_snapshot_sha256(unsigned)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-contract.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            with self.assertRaises(PresenterDirectoryError) as denied:
                PresenterDirectory.from_path(path)
        self.assertEqual(str(denied.exception), "directory_stats_invalid")


if __name__ == "__main__":
    unittest.main()
