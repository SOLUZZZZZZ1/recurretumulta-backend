from __future__ import annotations

import json
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException, Response

import partner


PARTNER_ID = "11111111-1111-4111-8111-111111111111"


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        return _Result(self.rows)


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def _partner_record():
    return {
        "id": PARTNER_ID,
        "name": "Asesoría sintética",
        "email": "private@example.test",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }


def _rows(count: int):
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    return [
        (
            str(uuid.UUID(int=index + 1)),
            f"Cliente {index}",
            f"cliente-{index}@example.test",
            "uploaded",
            "monthly",
            now - timedelta(seconds=index),
            1,
            True,
            True,
            False,
            False,
        )
        for index in range(count)
    ]


class PartnerPaginationSecurityTest(unittest.TestCase):
    def test_session_probe_returns_only_minimal_non_secret_contract(self):
        token = partner._make_token()
        response = Response()
        connection = _Connection([])
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(
                partner,
                "_get_partner_by_token",
                return_value=_partner_record(),
            ),
        ):
            result = partner.get_partner_session(
                response,
                authorization=None,
                rtm_partner_session=token,
            )

        self.assertEqual(
            set(result),
            {"ok", "authenticated", "partner_name", "expires_at"},
        )
        self.assertTrue(result["authenticated"])
        rendered = json.dumps(result).casefold()
        for forbidden in (
            "private@example.test",
            PARTNER_ID,
            token.casefold(),
            "csrf",
            "api_token",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")

    def test_case_page_is_hard_limited_and_emits_a_filter_bound_cursor(self):
        token = partner._make_token()
        connection = _Connection(_rows(251))
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(
                partner,
                "_get_partner_by_token",
                return_value=_partner_record(),
            ),
        ):
            result = partner.list_partner_cases(
                Response(),
                authorization=f"Bearer {token}",
                rtm_partner_session=None,
                q="100%_SAFE!",
                status="uploaded",
                limit=250,
                cursor=None,
            )

        self.assertEqual(len(result["items"]), 250)
        self.assertTrue(result["items"][0]["authorization_received"])
        self.assertFalse(result["items"][0]["authorization_verified"])
        self.assertEqual(
            result["items"][0]["authorization_evidence_status"],
            "pending_review",
        )
        self.assertIsInstance(result["next_cursor"], str)
        self.assertLessEqual(
            len(result["next_cursor"]),
            partner._PARTNER_CASE_CURSOR_MAX_CHARS,
        )
        sql, parameters = connection.calls[0]
        self.assertIn("ORDER BY c.updated_at DESC, c.id DESC", sql)
        self.assertIn("LIMIT :page_size", sql)
        self.assertEqual(parameters["page_size"], 251)
        self.assertEqual(parameters["q"], "%100!%!_SAFE!!%")

        decoded = partner._decode_partner_case_cursor(
            result["next_cursor"],
            credential_token=token,
            partner_id=PARTNER_ID,
            q="100%_SAFE!",
            status="uploaded",
        )
        self.assertEqual(decoded[1], result["items"][-1]["case_id"])

        for changed_binding in (
            {"partner_id": "22222222-2222-4222-8222-222222222222"},
            {"q": "other"},
            {"status": "closed"},
            {"credential_token": partner._make_token()},
        ):
            arguments = {
                "credential_token": token,
                "partner_id": PARTNER_ID,
                "q": "100%_SAFE!",
                "status": "uploaded",
            }
            arguments.update(changed_binding)
            with self.subTest(changed_binding=changed_binding), self.assertRaises(
                HTTPException
            ) as raised:
                partner._decode_partner_case_cursor(
                    result["next_cursor"],
                    **arguments,
                )
            self.assertEqual(raised.exception.status_code, 422)

    def test_second_page_uses_stable_seek_and_invalid_status_fails_before_query(self):
        token = partner._make_token()
        first_row = _rows(1)[0]
        cursor = partner._encode_partner_case_cursor(
            credential_token=token,
            partner_id=PARTNER_ID,
            q="",
            status="uploaded",
            updated_at=first_row[5],
            case_id=first_row[0],
        )
        connection = _Connection([])
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(
                partner,
                "_get_partner_by_token",
                return_value=_partner_record(),
            ),
        ):
            result = partner.list_partner_cases(
                Response(),
                authorization=f"Bearer {token}",
                rtm_partner_session=None,
                q=None,
                status="uploaded",
                limit=25,
                cursor=cursor,
            )

        self.assertEqual(result["items"], [])
        self.assertIsNone(result["next_cursor"])
        sql, parameters = connection.calls[0]
        self.assertIn("c.updated_at < :cursor_updated_at", sql)
        self.assertEqual(parameters["cursor_case_id"], first_row[0])
        self.assertEqual(parameters["page_size"], 26)

        denied_connection = _Connection([])
        with (
            patch.object(
                partner,
                "get_engine",
                return_value=_Engine(denied_connection),
            ),
            patch.object(
                partner,
                "_get_partner_by_token",
                return_value=_partner_record(),
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            partner.list_partner_cases(
                Response(),
                authorization=f"Bearer {token}",
                rtm_partner_session=None,
                q=None,
                status="attacker-selected-status",
                limit=25,
                cursor=None,
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(denied_connection.calls, [])


if __name__ == "__main__":
    unittest.main()
