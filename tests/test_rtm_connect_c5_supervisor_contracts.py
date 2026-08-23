from __future__ import annotations

import unittest

from rtm_connect.supervisor_contracts import (
    ConnectSupervisorProjectionError,
    assert_sanitized_supervisor_projection,
)


class ConnectC5SupervisorContractsTest(unittest.TestCase):
    def test_sanitized_nested_projection_is_accepted(self):
        assert_sanitized_supervisor_projection(
            {
                "ok": True,
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "external_reference_present": False,
                        "dead_letter_reason_code": "exact_match_not_found",
                    }
                ],
            }
        )

    def test_raw_operational_keys_fail_closed_at_any_depth(self):
        for key in (
            "payload",
            "target_ref",
            "reason_detail",
            "claimed_action_id",
            "failure_class",
            "verification_method",
            "audit_event_id",
        ):
            with self.subTest(key=key):
                with self.assertRaises(ConnectSupervisorProjectionError):
                    assert_sanitized_supervisor_projection(
                        {"items": [{key: "not-public"}]}
                    )

    def test_hashes_fail_closed(self):
        for key in ("payload_sha256", "document_hash"):
            with self.subTest(key=key):
                with self.assertRaises(ConnectSupervisorProjectionError):
                    assert_sanitized_supervisor_projection({key: "a" * 64})

    def test_redaction_declarations_must_remain_false(self):
        assert_sanitized_supervisor_projection(
            {"raw_action_material_exposed": False}
        )
        with self.assertRaises(ConnectSupervisorProjectionError):
            assert_sanitized_supervisor_projection(
                {"raw_action_material_exposed": True}
            )
        with self.assertRaises(ConnectSupervisorProjectionError):
            assert_sanitized_supervisor_projection(
                {"future_metadata_field": {}}
            )


if __name__ == "__main__":
    unittest.main()
