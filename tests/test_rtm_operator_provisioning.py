from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rtm_core import operator_provisioning
from rtm_core.operator_provisioning import (
    DEFAULT_SYNTHETIC_EMAIL,
    ROLE_DEFINITIONS,
    generate_temporary_password,
    normalize_synthetic_operator_email,
    role_definition,
)


class OperatorProvisioningTest(unittest.TestCase):
    def test_default_email_is_unambiguously_synthetic(self):
        self.assertEqual(
            normalize_synthetic_operator_email(DEFAULT_SYNTHETIC_EMAIL),
            DEFAULT_SYNTHETIC_EMAIL,
        )

    def test_real_or_unmarked_email_is_rejected(self):
        for value in (
            "ramon@recurretumulta.eu",
            "supervisor@example.com",
            "rtm-staging-supervisor@gmail.com",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_synthetic_operator_email(value)

    def test_minimum_roles_are_explicit_and_small(self):
        self.assertEqual(set(ROLE_DEFINITIONS), {"operator", "supervisor"})
        self.assertEqual(
            ROLE_DEFINITIONS["operator"].permissions,
            (
                "ops.view",
                "presenter.documents.ingest",
                "presenter.documents.read",
                "presenter.package.freeze",
            ),
        )
        self.assertEqual(
            ROLE_DEFINITIONS["supervisor"].permissions,
            (
                "ops.view",
                "ops.supervise",
                "presenter.documents.ingest",
                "presenter.documents.read",
                "presenter.package.freeze",
            ),
        )

    def test_role_lookup_fails_closed(self):
        self.assertEqual(role_definition("supervisor").code, "rtm.supervisor")
        with self.assertRaises(ValueError):
            role_definition("administrator")

    def test_generated_password_meets_minimum_and_is_not_constant(self):
        first = generate_temporary_password()
        second = generate_temporary_password()
        self.assertGreaterEqual(len(first), 12)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("RTM-"))

    def test_manual_provisioning_never_echoes_entered_password(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "rtm_staging_operator_provision.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "if result.password_issued and args.generate_password:",
            source,
        )
        self.assertNotIn("if result.password_issued:\\n", source)

    def test_existing_synthetic_operator_refreshes_roles_and_assignment(self):
        operator_id = "00000000-0000-4000-8000-000000000001"
        role_id = "00000000-0000-4000-8000-000000000002"
        existing = {
            "id": operator_id,
            "email": DEFAULT_SYNTHETIC_EMAIL,
            "display_name": "Existing synthetic operator",
            "role_code": "rtm.operator",
            "profile": {"synthetic": True},
            "must_change_password": False,
        }
        conn = Mock()
        revoke_result = Mock()
        revoke_result.rowcount = 2
        assignment_result = Mock()
        assignment_result.rowcount = 1
        conn.execute.side_effect = [revoke_result, assignment_result]

        with patch.object(
            operator_provisioning,
            "find_operator_by_email",
            return_value=existing,
        ), patch.object(
            operator_provisioning,
            "ensure_minimum_roles",
            return_value={
                "operator": role_id,
                "supervisor": "00000000-0000-4000-8000-000000000009",
            },
        ) as ensure_roles:
            result = operator_provisioning.provision_synthetic_operator(
                conn,
                email=DEFAULT_SYNTHETIC_EMAIL,
                display_name="Ignored existing name",
                role_key="supervisor",
                password="unused-existing-password",
            )

        ensure_roles.assert_called_once_with(conn)
        self.assertEqual(conn.execute.call_count, 2)
        revoke_statement = str(conn.execute.call_args_list[0].args[0])
        self.assertIn("UPDATE rtm_operator_sessions", revoke_statement)
        statement, parameters = conn.execute.call_args_list[1].args
        self.assertIn("UPDATE rtm_operators", str(statement))
        self.assertEqual(parameters["operator_id"], operator_id)
        self.assertEqual(
            parameters["role_id"],
            "00000000-0000-4000-8000-000000000009",
        )
        self.assertIn("auth_epoch=auth_epoch+1", str(statement))
        self.assertIn("profile @>", str(statement))
        self.assertFalse(result.created)
        self.assertFalse(result.password_issued)
        self.assertEqual(result.role_code, "rtm.supervisor")

    def test_shared_roles_are_not_mutated_with_real_operators_present(self):
        conn = Mock()

        with patch.object(
            operator_provisioning,
            "count_non_synthetic_operators",
            return_value=1,
        ) as count_real:
            with self.assertRaisesRegex(
                RuntimeError,
                "roles compartidos",
            ):
                operator_provisioning.ensure_minimum_roles(conn)

        count_real.assert_called_once_with(conn)
        conn.execute.assert_called_once()
        lock_statement = str(conn.execute.call_args.args[0])
        self.assertIn("LOCK TABLE rtm_operators", lock_statement)
        self.assertIn("rtm_operator_roles", lock_statement)
        self.assertIn("IN SHARE ROW EXCLUSIVE MODE", lock_statement)
        self.assertNotIn("INSERT INTO rtm_operator_roles", lock_statement)

    def test_non_synthetic_count_uses_typed_json_boolean(self):
        conn = Mock()
        result = Mock()
        result.scalar_one.return_value = 3
        conn.execute.return_value = result

        self.assertEqual(
            operator_provisioning.count_non_synthetic_operators(conn),
            3,
        )

        statement = str(conn.execute.call_args.args[0])
        self.assertIn("profile @>", statement)
        self.assertIn("{\"synthetic\": true}", statement)
        self.assertNotIn("profile->>'synthetic'", statement)

    def test_role_permission_refresh_is_additive(self):
        conn = Mock()
        operator_role_result = Mock()
        operator_role_result.fetchone.return_value = (
            "00000000-0000-4000-8000-000000000010",
        )
        supervisor_role_result = Mock()
        supervisor_role_result.fetchone.return_value = (
            "00000000-0000-4000-8000-000000000011",
        )
        conn.execute.side_effect = [
            Mock(),
            operator_role_result,
            supervisor_role_result,
            Mock(),
            Mock(),
        ]

        with patch.object(
            operator_provisioning,
            "count_non_synthetic_operators",
            return_value=0,
        ):
            role_ids = operator_provisioning.ensure_minimum_roles(conn)

        self.assertEqual(
            role_ids,
            {
                "operator": "00000000-0000-4000-8000-000000000010",
                "supervisor": "00000000-0000-4000-8000-000000000011",
            },
        )
        for call in conn.execute.call_args_list[1:3]:
            statement = "".join(str(call.args[0]).split())
            self.assertIn(
                "INSERTINTO", statement,
            )
            self.assertIn("ASrole_row(", statement)
            self.assertIn("role_row.permissions", statement)
            self.assertNotIn("AScurrent_role(", statement)
            self.assertNotIn("current_role.permissions", statement)
            self.assertIn("UNION", statement)
            self.assertIn("EXCLUDED.permissions", statement)
            self.assertNotIn(
                "permissions=EXCLUDED.permissions",
                statement,
            )
        invalidation_statements = [
            str(call.args[0]) for call in conn.execute.call_args_list[3:]
        ]
        self.assertIn(
            "UPDATE rtm_operator_sessions",
            invalidation_statements[0],
        )
        self.assertIn("auth_epoch=auth_epoch+1", invalidation_statements[1])

    def test_idempotent_role_refresh_does_not_revoke_sessions(self):
        conn = Mock()
        operator_upsert = Mock()
        operator_upsert.fetchone.return_value = None
        operator_lookup = Mock()
        operator_lookup.fetchone.return_value = (
            "00000000-0000-4000-8000-000000000020",
        )
        supervisor_upsert = Mock()
        supervisor_upsert.fetchone.return_value = None
        supervisor_lookup = Mock()
        supervisor_lookup.fetchone.return_value = (
            "00000000-0000-4000-8000-000000000021",
        )
        conn.execute.side_effect = [
            Mock(),
            operator_upsert,
            operator_lookup,
            supervisor_upsert,
            supervisor_lookup,
        ]

        with patch.object(
            operator_provisioning,
            "count_non_synthetic_operators",
            return_value=0,
        ):
            role_ids = operator_provisioning.ensure_minimum_roles(conn)

        self.assertEqual(
            role_ids["operator"],
            "00000000-0000-4000-8000-000000000020",
        )
        self.assertEqual(
            role_ids["supervisor"],
            "00000000-0000-4000-8000-000000000021",
        )
        statements = [
            str(call.args[0]) for call in conn.execute.call_args_list
        ]
        self.assertEqual(len(statements), 5)
        self.assertFalse(
            any("UPDATE rtm_operator_sessions" in sql for sql in statements)
        )
        self.assertFalse(
            any("auth_epoch=auth_epoch+1" in sql for sql in statements)
        )

    def test_inactive_or_malformed_shared_role_fails_closed(self):
        conn = Mock()
        operator_upsert = Mock()
        operator_upsert.fetchone.return_value = None
        invalid_lookup = Mock()
        invalid_lookup.fetchone.return_value = None
        conn.execute.side_effect = [Mock(), operator_upsert, invalid_lookup]

        with patch.object(
            operator_provisioning,
            "count_non_synthetic_operators",
            return_value=0,
        ):
            with self.assertRaisesRegex(RuntimeError, "no cumple el mínimo"):
                operator_provisioning.ensure_minimum_roles(conn)

        statements = [
            str(call.args[0]) for call in conn.execute.call_args_list
        ]
        self.assertEqual(len(statements), 3)
        self.assertNotIn("active=TRUE,", statements[1])
        self.assertFalse(
            any("UPDATE rtm_operator_sessions" in sql for sql in statements)
        )

    def test_existing_synthetic_operator_cannot_refresh_roles_in_mixed_population(self):
        conn = Mock()
        existing = {
            "id": "00000000-0000-4000-8000-000000000012",
            "email": DEFAULT_SYNTHETIC_EMAIL,
            "display_name": "Synthetic operator",
            "role_code": "rtm.operator",
            "profile": {"synthetic": True},
            "must_change_password": False,
        }

        with patch.object(
            operator_provisioning,
            "find_operator_by_email",
            return_value=existing,
        ), patch.object(
            operator_provisioning,
            "count_non_synthetic_operators",
            return_value=1,
        ):
            with self.assertRaises(RuntimeError):
                operator_provisioning.provision_synthetic_operator(
                    conn,
                    email=DEFAULT_SYNTHETIC_EMAIL,
                    display_name="Synthetic operator",
                    role_key="operator",
                    password="unused-existing-password",
                )

        statements = [
            str(call.args[0]) for call in conn.execute.call_args_list
        ]
        self.assertEqual(len(statements), 1)
        self.assertIn("LOCK TABLE rtm_operators", statements[0])
        self.assertNotIn("UPDATE rtm_operators", statements[0])

    def test_existing_non_synthetic_collision_performs_no_role_write(self):
        conn = Mock()
        existing = {
            "id": "00000000-0000-4000-8000-000000000003",
            "email": DEFAULT_SYNTHETIC_EMAIL,
            "display_name": "Real operator collision",
            "role_code": "rtm.operator",
            "profile": {"synthetic": False},
        }

        with patch.object(
            operator_provisioning,
            "find_operator_by_email",
            return_value=existing,
        ), patch.object(
            operator_provisioning,
            "ensure_minimum_roles",
        ) as ensure_roles:
            with self.assertRaises(RuntimeError):
                operator_provisioning.provision_synthetic_operator(
                    conn,
                    email=DEFAULT_SYNTHETIC_EMAIL,
                    display_name="Collision",
                    role_key="operator",
                    password="unused-existing-password",
                )

        ensure_roles.assert_not_called()
        conn.execute.assert_not_called()

    def test_existing_synthetic_assignment_race_fails_closed(self):
        conn = Mock()
        conn.execute.return_value.rowcount = 0
        existing = {
            "id": "00000000-0000-4000-8000-000000000004",
            "email": DEFAULT_SYNTHETIC_EMAIL,
            "display_name": "Synthetic before guarded update",
            "role_code": "rtm.operator",
            "profile": {"synthetic": True},
            "must_change_password": False,
        }

        with patch.object(
            operator_provisioning,
            "find_operator_by_email",
            return_value=existing,
        ), patch.object(
            operator_provisioning,
            "ensure_minimum_roles",
            return_value={
                "operator": "00000000-0000-4000-8000-000000000005",
                "supervisor": "00000000-0000-4000-8000-000000000006",
            },
        ):
            with self.assertRaises(RuntimeError):
                operator_provisioning.provision_synthetic_operator(
                    conn,
                    email=DEFAULT_SYNTHETIC_EMAIL,
                    display_name="Synthetic before guarded update",
                    role_key="supervisor",
                    password="unused-existing-password",
                )


if __name__ == "__main__":
    unittest.main()
