from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from rtm_connect.connectors.assisted_legal import (
    ASSISTED_LEGAL_FIXED_CHECKLIST,
    ASSISTED_LEGAL_MANIFEST_SHA256,
    AssistedLegalConnector,
    AssistedLegalPackage,
    assisted_legal_manifest,
)


CONNECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "rtm_connect"
    / "connectors"
    / "assisted_legal.py"
)


class ConnectC7PackageContractTest(unittest.TestCase):
    def test_package_schema_exposes_only_frozen_machine_fields(self):
        self.assertEqual(
            tuple(AssistedLegalPackage.__dataclass_fields__),
            (
                "action_id",
                "attempt_id",
                "authorization_id",
                "request_sha256",
                "document_hashes",
                "due_at",
                "checklist",
                "human_final_gate",
                "human_gate_sha256",
                "manifest",
                "package_sha256",
            ),
        )
        self.assertIsInstance(ASSISTED_LEGAL_FIXED_CHECKLIST, tuple)
        self.assertEqual(len(set(ASSISTED_LEGAL_FIXED_CHECKLIST)), 5)

    def test_manifest_declares_no_network_routes_credentials_or_effects(self):
        manifest = assisted_legal_manifest()
        self.assertTrue(manifest["synthetic_only"])
        self.assertFalse(manifest["network_used"])
        self.assertFalse(manifest["routes_published"])
        self.assertIsNone(manifest["credential_ref"])
        self.assertFalse(manifest["external_effects_executed"])
        self.assertFalse(manifest["legal_submission_executed"])
        self.assertTrue(manifest["human_final_submit_required"])
        self.assertEqual(
            tuple(manifest["fixed_checklist"]),
            ASSISTED_LEGAL_FIXED_CHECKLIST,
        )

    def test_manifest_sha_is_a_literal_frozen_digest(self):
        source = CONNECTOR_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        value = assignments["ASSISTED_LEGAL_MANIFEST_SHA256"]
        self.assertIsInstance(value, ast.Constant)
        self.assertEqual(value.value, ASSISTED_LEGAL_MANIFEST_SHA256)
        self.assertEqual(len(ASSISTED_LEGAL_MANIFEST_SHA256), 64)

    def test_connector_has_no_network_or_web_framework_imports(self):
        tree = ast.parse(CONNECTOR_PATH.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "aiohttp",
                    "boto3",
                    "fastapi",
                    "httpx",
                    "requests",
                    "smtplib",
                    "socket",
                    "urllib",
                }
            )
        )

    def test_connector_publishes_no_submit_send_execute_or_reconcile_method(self):
        public_methods = {
            name
            for name, value in inspect.getmembers(
                AssistedLegalConnector,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }
        self.assertEqual(
            public_methods,
            {"build_package", "capture_receipt", "verify_receipt"},
        )

    def test_build_package_accepts_no_instruction_or_legal_text_parameter(self):
        parameters = set(inspect.signature(AssistedLegalConnector.build_package).parameters)
        self.assertEqual(
            parameters,
            {"self", "action", "grant", "attempt_id", "due_at"},
        )
        self.assertTrue(
            parameters.isdisjoint(
                {
                    "instructions",
                    "legal_text",
                    "document_body",
                    "recipient",
                    "endpoint",
                    "credential",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
