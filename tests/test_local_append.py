import unittest
from unittest import mock

from fastapi import HTTPException
from rtm_core import intake_router, generic_authorization


class LocalAppendTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_route_validates_profile_without_requiring_b2(self):
        with (
            mock.patch.object(intake_router, "require_case_access_token", return_value="case"),
            mock.patch.object(intake_router, "local_operator_auth_requested", return_value=True),
            mock.patch.object(generic_authorization, "require_local_generic_profile") as profile,
            mock.patch.object(intake_router, "require_http_capability") as external,
        ):
            with self.assertRaises(HTTPException) as error:
                await intake_router.append_documents_core("case", [], "token")
            self.assertEqual(error.exception.status_code, 400)
            profile.assert_called_once()
            external.assert_not_called()

    def test_local_preflight_rejects_case_before_document_queries(self):
        conn = mock.MagicMock()
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = conn
        with (
            mock.patch.object(intake_router, "get_engine", return_value=engine),
            mock.patch.object(intake_router, "local_operator_auth_requested", return_value=True),
            mock.patch.object(intake_router, "lock_case_for_public_material_mutation"),
            mock.patch.object(generic_authorization, "require_local_generic_profile"),
            mock.patch("scripts.rtm_local_operator_setup.require_local_database") as database,
            mock.patch.object(generic_authorization, "load_snapshot", side_effect=HTTPException(409, "not synthetic")),
        ):
            with self.assertRaises(HTTPException):
                intake_router._existing_original_hashes("case", ["digest"])
            database.assert_called_once_with(conn)
            conn.execute.assert_not_called()

    def test_case_is_checked_again_before_commit(self):
        conn = mock.MagicMock()
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = conn
        with (
            mock.patch.object(intake_router, "get_engine", return_value=engine),
            mock.patch.object(intake_router, "lock_case_for_public_material_mutation"),
            mock.patch.object(intake_router, "_require_local_append_case", side_effect=HTTPException(409, "changed")),
        ):
            with self.assertRaises(HTTPException):
                intake_router._commit_appended_documents("case", [], {})
            conn.execute.assert_not_called()
