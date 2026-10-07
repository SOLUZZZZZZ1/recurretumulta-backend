from __future__ import annotations
import json
import unittest
from unittest.mock import patch, MagicMock
import httpx
from fastapi import HTTPException
from openai import RateLimitError
import reanalysis

def rejected(code, kind="rate_limit_error"):
    response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return RateLimitError("PRIVATE response / key / document", response=response,
                          body={"code":code,"type":kind,"message":"PRIVATE"})

class ReanalysisProviderFailureTest(unittest.TestCase):
    def test_quota_and_rate_limits_have_different_actions(self):
        for code in reanalysis._PROVIDER_QUOTA_CODES:
            failure=reanalysis._provider_rejection(rejected(code))
            self.assertEqual(failure["error_code"],"reanalysis_provider_quota_unavailable")
            self.assertIn("saldo",failure["message"])
        for code in reanalysis._PROVIDER_RATE_CODES:
            failure=reanalysis._provider_rejection(rejected(code))
            self.assertEqual(failure["error_code"],"reanalysis_provider_rate_limited")
            self.assertIn("Espera",failure["message"])

    def test_unknown_provider_codes_and_body_are_never_exposed(self):
        exc=rejected("PRIVATE arbitrary response")
        with self.assertLogs("reanalysis",level="ERROR") as logs:
            reanalysis._log_reanalysis_failure(exc,"synthetic-run","page_extraction")
        self.assertNotIn("PRIVATE",str(logs.output))
        metadata=json.loads(logs.records[0].getMessage())
        self.assertEqual(metadata["provider_code"],"unclassified_429")
        self.assertEqual(reanalysis._provider_rejection(rejected(None,"insufficient_quota"))["provider_code"],"insufficient_quota")
        self.assertIsNone(reanalysis._provider_rejection(RuntimeError("PRIVATE")))

    def test_provider_rejection_is_safe_unavailability_not_internal_failure(self):
        for code in ("insufficient_quota","rate_limit_exceeded","unrecognized"):
            events=MagicMock()
            with patch.object(reanalysis,"require_capability"), \
                 patch.object(reanalysis,"_case_meta",return_value={"department":"traffic","case_type":"fine"}), \
                 patch.object(reanalysis,"_load_original_documents",return_value=[{"id":"doc","bucket":"fixture","key":"fixture.pdf","size_bytes":10}]), \
                 patch.object(reanalysis,"download_bytes_limited",side_effect=rejected(code)), \
                 patch.object(reanalysis,"_append_event",events), \
                 patch.object(reanalysis,"_persist_completed_reanalysis") as persist, \
                 self.assertLogs("reanalysis",level="ERROR"), \
                 self.assertRaises(HTTPException) as raised:
                reanalysis.reanalyze_traffic_fine_case("synthetic-case")
            self.assertEqual(raised.exception.status_code,503)
            self.assertNotIn("PRIVATE",raised.exception.detail)
            self.assertIn("pago del expediente sigue confirmado",raised.exception.detail)
            self.assertEqual(events.call_args.args[1],"case_reanalysis_failed")
            self.assertTrue(events.call_args.args[2]["error_code"].startswith("reanalysis_provider_"))
            persist.assert_not_called()

if __name__=="__main__":
    unittest.main()
