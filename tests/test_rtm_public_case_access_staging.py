import os
from pathlib import Path
from unittest import TestCase, mock
from uuid import uuid4

from fastapi import HTTPException

import public_case_access


class StagingPublicCaseAccessBoundaryTest(TestCase):
    OPERATOR_TOKEN = "staging-shared-operator-secret-" + ("x" * 32)
    PUBLIC_SECRET = "staging-public-case-secret-" + ("p" * 32)

    def _environment(self, environment: str) -> dict[str, str]:
        return {
            "RTM_ENV": environment,
            "OPERATOR_TOKEN": self.OPERATOR_TOKEN,
            "RTM_PUBLIC_CASE_ACCESS_SECRET": self.PUBLIC_SECRET,
        }

    def test_staging_shared_operator_token_cannot_bypass_public_capability(self):
        case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            self._environment("staging"),
            clear=True,
        ):
            with self.assertRaises(HTTPException) as denied:
                public_case_access.require_case_or_operator_access(
                    case_id,
                    None,
                    self.OPERATOR_TOKEN,
                )

        self.assertEqual(denied.exception.status_code, 401)
        self.assertEqual(
            denied.exception.detail,
            "Capacidad de expediente inválida",
        )

    def test_staging_public_capability_remains_valid(self):
        case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            self._environment(" StAgInG "),
            clear=True,
        ):
            case_token = public_case_access.issue_case_access_token(case_id)
            canonical = public_case_access.require_case_or_operator_access(
                case_id,
                case_token,
                self.OPERATOR_TOKEN,
            )

        self.assertEqual(canonical, case_id)

    def test_staging_receipt_upload_helper_rejects_shared_operator_token(self):
        case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            self._environment("staging"),
            clear=True,
        ):
            with self.assertRaises(HTTPException) as denied:
                public_case_access.require_operator_case_access(
                    case_id,
                    self.OPERATOR_TOKEN,
                )

        self.assertEqual(denied.exception.status_code, 401)
        self.assertEqual(
            denied.exception.detail,
            "Autenticación individual requerida",
        )

    def test_legacy_receipt_route_checks_boundary_before_reading_or_storage(self):
        source = Path("cases.py").read_text(encoding="utf-8")
        route = source.split(
            '@router.post("/{case_id}/upload-receipt")',
            maxsplit=1,
        )[1].split("@router.", maxsplit=1)[0]

        access_check = route.index("require_operator_case_access(")
        self.assertLess(access_check, route.index("await file.read()"))
        self.assertLess(access_check, route.index("upload_bytes("))

    def test_production_keeps_shared_operator_compatibility(self):
        case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            self._environment("production"),
            clear=True,
        ):
            billing_case_id = public_case_access.require_case_or_operator_access(
                case_id,
                None,
                self.OPERATOR_TOKEN,
            )
            receipt_case_id = public_case_access.require_operator_case_access(
                case_id,
                self.OPERATOR_TOKEN,
            )

        self.assertEqual(billing_case_id, case_id)
        self.assertEqual(receipt_case_id, case_id)

    def test_unconfigured_environment_keeps_existing_legacy_contract(self):
        case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            {
                "OPERATOR_TOKEN": self.OPERATOR_TOKEN,
                "RTM_PUBLIC_CASE_ACCESS_SECRET": self.PUBLIC_SECRET,
            },
            clear=True,
        ):
            canonical = public_case_access.require_case_or_operator_access(
                case_id,
                None,
                self.OPERATOR_TOKEN,
            )

        self.assertEqual(canonical, case_id)

    def test_feature_requested_outside_staging_never_accepts_shared_token(self):
        case_id = str(uuid4())
        environments = (
            {
                "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            },
            {
                "RTM_ENV": "production",
                "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            },
            {
                "RTM_ENV": "production",
                "RTM_ENABLE_OPERATOR_AUTH_V1": "invalid",
            },
            {
                "RTM_ENV": "stagin",
                "RTM_DATA_NAMESPACE": "rtm_staging",
            },
            {
                "RTM_ENV": "production",
                "RENDER_SERVICE_NAME": "recurretumulta-rtm-staging",
            },
        )
        for environment in environments:
            configured = {
                **environment,
                "OPERATOR_TOKEN": self.OPERATOR_TOKEN,
                "RTM_PUBLIC_CASE_ACCESS_SECRET": self.PUBLIC_SECRET,
            }
            with (
                self.subTest(environment=environment),
                mock.patch.dict(os.environ, configured, clear=True),
            ):
                with self.assertRaises(HTTPException) as public_denied:
                    public_case_access.require_case_or_operator_access(
                        case_id,
                        None,
                        self.OPERATOR_TOKEN,
                    )
                with self.assertRaises(HTTPException) as receipt_denied:
                    public_case_access.require_operator_case_access(
                        case_id,
                        self.OPERATOR_TOKEN,
                    )

            self.assertEqual(public_denied.exception.status_code, 401)
            self.assertEqual(receipt_denied.exception.status_code, 503)

    def test_misconfiguration_does_not_disable_valid_public_capability(self):
        case_id = str(uuid4())
        environment = {
            "RTM_ENV": "production",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            "OPERATOR_TOKEN": self.OPERATOR_TOKEN,
            "RTM_PUBLIC_CASE_ACCESS_SECRET": self.PUBLIC_SECRET,
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            case_token = public_case_access.issue_case_access_token(case_id)
            canonical = public_case_access.require_case_or_operator_access(
                case_id,
                case_token,
                self.OPERATOR_TOKEN,
            )

        self.assertEqual(canonical, case_id)


if __name__ == "__main__":
    import unittest

    unittest.main()
