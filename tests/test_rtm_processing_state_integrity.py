from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from rtm_core import authority_repository, preview_repository


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _AuthorityConnection:
    def __init__(self, status: str):
        self.status = status
        self.calls: list[str] = []

    def execute(self, statement, parameters):
        self.calls.append(str(statement))
        mapping = {
            "id": parameters["case_id"],
            "payment_status": "paid",
            "authorized": True,
            "status": self.status,
            "department": "traffic",
            "case_type": "fine",
            "category": "traffic",
        }
        return _Result(SimpleNamespace(_mapping=mapping))


class _PreviewConnection:
    def __init__(self, status: str):
        self.status = status
        self.calls: list[str] = []

    def execute(self, statement, parameters):
        self.calls.append(str(statement))
        return _Result((self.status,))


class ProcessingStateIntegrityTest(unittest.TestCase):
    def test_authority_invalidations_fail_before_child_mutation(self):
        for status in (
            "submitting",
            "reanalysis_in_progress",
            "document_extraction_in_progress",
            "submitted",
        ):
            with self.subTest(status=status):
                conn = _AuthorityConnection(status)
                with self.assertRaises(HTTPException) as caught:
                    authority_repository.invalidate_validated_facts(
                        conn, "case-1", "facts-1", "operator:test", "security"
                    )
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(len(conn.calls), 1)
                self.assertIn("FOR UPDATE", conn.calls[0])

    def test_preview_invalidation_locks_case_and_fails_before_child_mutation(self):
        for status in (
            "submitting",
            "reanalysis_in_progress",
            "document_extraction_in_progress",
            "submitted",
        ):
            with self.subTest(status=status):
                conn = _PreviewConnection(status)
                with self.assertRaises(HTTPException) as caught:
                    preview_repository.invalidate_preview(
                        conn, "case-1", "preview-1", "operator:test", "security"
                    )
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(len(conn.calls), 1)
                self.assertIn("FOR UPDATE", conn.calls[0])


if __name__ == "__main__":
    unittest.main()
