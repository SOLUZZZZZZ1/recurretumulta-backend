from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from scripts import rtm_local_operator_setup as setup


class LocalOperatorSetupTests(unittest.TestCase):
    def identity(self):
        return dict(database_name="rtm_local", user_name="rtm_local_app",
                    session_name="rtm_local_app", server_address="127.0.0.1",
                    client_address="127.0.0.1", server_port=5432, owner_name="rtm_local_app",
                    version=170011, rolsuper=False, rolcreatedb=False,
                    rolcreaterole=False, rolreplication=False)

    def test_refuses_remote_wrong_database_owner_and_elevated_roles(self):
        for field, value in (("server_address", "192.0.2.1"), ("client_address", "192.0.2.1"),
                             ("database_name", "rtm_staging"), ("owner_name", "postgres"),
                             ("session_name", "postgres"), ("server_port", 5433),
                             ("version", 160010), ("rolsuper", True),
                             ("rolcreatedb", True), ("rolcreaterole", True), ("rolreplication", True)):
            with self.subTest(field=field):
                conn = Mock()
                row = self.identity()
                row[field] = value
                conn.execute.return_value.mappings.return_value.one.return_value = row
                with self.assertRaises(RuntimeError):
                    setup.require_local_database(conn)
                self.assertEqual(conn.execute.call_count, 1)
                self.assertTrue(str(conn.execute.call_args.args[0]).strip().startswith("SELECT"))

    def test_existing_operator_is_not_given_a_new_password(self):
        conn = Mock()
        with patch.object(setup, "assert_local_operator_auth_ready"), \
             patch.object(setup, "require_local_database"), \
             patch.object(setup, "existing_local_operator", return_value="operator-id"), \
             patch.object(setup, "hash_operator_password") as hash_password:
            self.assertEqual(setup.create_local_operator(conn, "long-new-unused-password"), "operator-id")
        hash_password.assert_not_called()
        self.assertEqual(conn.execute.call_count, 1)
        self.assertTrue(str(conn.execute.call_args.args[0]).startswith("LOCK TABLE"))

    def test_first_setup_refuses_existing_operators_roles_or_customer_cases(self):
        for counts in ([1], [0, 1], [0, 0, 1]):
            conn = Mock()
            results = [Mock()]
            for count in counts:
                result = Mock()
                result.scalar_one.return_value = count
                results.append(result)
            conn.execute.side_effect = results
            with self.subTest(counts=counts), \
                 patch.object(setup, "assert_local_operator_auth_ready"), \
                 patch.object(setup, "require_local_database"), \
                 patch.object(setup, "existing_local_operator", return_value=None), \
                 patch.object(setup, "hash_operator_password") as hash_password:
                with self.assertRaises(RuntimeError):
                    setup.create_local_operator(conn, "valid-local-test-password")
            hash_password.assert_not_called()
            self.assertFalse(any("INSERT INTO" in str(call.args[0]) for call in conn.execute.call_args_list))

    def test_short_password_is_rejected_before_mutation(self):
        conn = Mock()
        with patch.object(setup, "assert_local_operator_auth_ready"), \
             patch.object(setup, "require_local_database"):
            with self.assertRaises(ValueError):
                setup.create_local_operator(conn, "short")
        conn.execute.assert_not_called()

    def test_unrecognized_profile_is_never_repaired(self):
        conn = Mock()
        row = {"id": "id", "profile": {"environment": "staging", "synthetic": True}}
        conn.execute.return_value.mappings.return_value.one_or_none.return_value = row
        with patch.object(setup, "require_local_database"):
            with self.assertRaises(RuntimeError):
                setup.existing_local_operator(conn)
        self.assertEqual(conn.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
