from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C6_CONTROLLED_PROVIDER_SANDBOX.md"
ADR = ROOT / "docs" / "rtm_connect" / "adrs" / "0013-c6-controlled-http-provider-sandbox.md"


class ConnectC6DocsContractTest(unittest.TestCase):
    def test_docs_exist_and_do_not_claim_real_provider(self):
        for path in (DOC, ADR):
            self.assertTrue(path.exists())
            source = path.read_text(encoding="utf-8")
            self.assertIn("controlled.sandbox", source)
            self.assertIn("UNKNOWN", source.upper())
            self.assertIn("idempot", source.lower())
            self.assertIn("no", source.lower())
        source = DOC.read_text(encoding="utf-8")
        self.assertIn("no integra ni suplanta", source)
        self.assertIn("DGT, OEPM", source)
        self.assertIn("E2_external_reference", source)

    def test_docs_freeze_no_ddl_routes_seed_or_blind_retry(self):
        source = (DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")).lower()
        for required in (
            "cero ddl", "no se modifica `app.py`", "no se repite el post",
            "rollback", "loopback", "sin expediente", "efecto legal",
            "rtm.core.authorization/rtm_core_authority_v1",
            "core debe haber persistido", "rama runtime", "transporte loopback sellado",
        ):
            self.assertIn(required.lower(), source)


if __name__ == "__main__":
    unittest.main()
