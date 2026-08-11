from __future__ import annotations

import unittest

from rtm_core.claims_banking_regime import (
    BANKING_TRANSPARENCY_EFFECTIVE_ON,
    CLAIMS_BANKING_REGIME_VERSION,
    CURRENT_RULESET_SAFE_THROUGH,
    CUSTOMER_SERVICE_FULL_ADAPTATION_ON,
    INSTANT_PAYMENT_CHARGE_PARITY_EFFECTIVE_ON,
    PAYMENT_SERVICES_RULES_EFFECTIVE_ON,
    PAYMENT_TRANSPARENCY_ORDER_EFFECTIVE_ON,
    VERIFICATION_OF_PAYEE_EFFECTIVE_ON,
    resolve_claims_banking_regime,
)


class ClaimsBankingRegimeTest(unittest.TestCase):
    def _resolve(self, **updates):
        payload = {
            "incident_date": "2026-08-10",
            "contract_date": "2025-10-01",
            "complaint_date": None,
            "bank_country": "España",
            "user_type": "Consumidor",
            "incident_type": "Cargo no reconocido en tarjeta",
            "issue_text": "Cargo no reconocido en tarjeta bancaria.",
            "operation_authorized": False,
            "payer_initiated_under_deception": False,
            "instant_transfer": False,
            "loan_involved": False,
            "investment_involved": False,
            "crypto_involved": False,
        }
        payload.update(updates)
        return resolve_claims_banking_regime(**payload)

    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            CLAIMS_BANKING_REGIME_VERSION,
            "rtm_claims_banking_regime_v1_0",
        )
        self.assertEqual(BANKING_TRANSPARENCY_EFFECTIVE_ON.isoformat(), "2012-04-29")
        self.assertEqual(PAYMENT_SERVICES_RULES_EFFECTIVE_ON.isoformat(), "2019-02-25")
        self.assertEqual(PAYMENT_TRANSPARENCY_ORDER_EFFECTIVE_ON.isoformat(), "2020-01-01")
        self.assertEqual(INSTANT_PAYMENT_CHARGE_PARITY_EFFECTIVE_ON.isoformat(), "2025-01-09")
        self.assertEqual(VERIFICATION_OF_PAYEE_EFFECTIVE_ON.isoformat(), "2025-10-09")
        self.assertEqual(CUSTOMER_SERVICE_FULL_ADAPTATION_ON.isoformat(), "2026-12-28")
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH.isoformat(), "2027-12-31")

    def test_current_unauthorized_payment_selects_refund_and_notice_rules(self):
        decision = self._resolve()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.incident_type, "unauthorized_payment")
        self.assertTrue(decision.payment_service)
        self.assertTrue(decision.unauthorized_refund_rule)
        self.assertEqual(decision.notification_months, 13)
        self.assertEqual(decision.immediate_refund_business_days, 1)
        self.assertEqual(decision.payer_loss_limit_eur, 50)
        self.assertEqual(decision.complaint_response_business_days, 15)
        rendered = " ".join(decision.legal_basis)
        self.assertIn("artículos 36 y 41 a 46", rendered)
        self.assertIn("artículos 69 y 70", rendered)

    def test_authorized_scam_is_not_automatic_unauthorized_refund(self):
        decision = self._resolve(
            incident_type="Transferencia ordenada bajo engaño",
            issue_text="Vishing de un falso empleado del banco.",
            operation_authorized=True,
            payer_initiated_under_deception=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.incident_type, "authorized_scam")
        self.assertFalse(decision.unauthorized_refund_rule)
        self.assertTrue(decision.authorization_requires_review)
        self.assertTrue(any("no recibe automáticamente" in item for item in decision.warnings))

    def test_direct_debit_selects_eight_week_and_ten_day_rules(self):
        decision = self._resolve(
            incident_type="Devolución de adeudo domiciliado",
            issue_text="Solicitud de devolución de recibo domiciliado.",
            operation_authorized=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.incident_type, "direct_debit_refund")
        self.assertEqual(decision.direct_debit_request_weeks, 8)
        self.assertEqual(decision.direct_debit_response_business_days, 10)
        self.assertIn("artículos 48 y 49", " ".join(decision.legal_basis))

    def test_verification_of_payee_is_temporally_versioned(self):
        current = self._resolve(
            incident_type="Verificación del beneficiario en transferencia instantánea",
            issue_text="No se verificó la coincidencia entre IBAN y nombre.",
            operation_authorized=True,
            instant_transfer=True,
        )
        self.assertEqual(current.status, "current")
        self.assertEqual(current.incident_type, "instant_transfer_verification")
        self.assertTrue(current.verification_of_payee_active)
        self.assertIn("Reglamento (UE) 2024/886", " ".join(current.legal_basis))

        historic = self._resolve(
            incident_date="2025-09-01",
            incident_type="Verificación del beneficiario en transferencia instantánea",
            issue_text="Transferencia instantánea sin verificación del beneficiario.",
            operation_authorized=True,
            instant_transfer=True,
        )
        self.assertEqual(historic.status, "operator_review")
        self.assertFalse(historic.legal_basis)

    def test_non_payment_complaint_period_depends_on_customer_type(self):
        consumer = self._resolve(
            incident_type="Comisión bancaria no informada",
            issue_text="Comisión bancaria en una cuenta.",
            operation_authorized=None,
        )
        self.assertEqual(consumer.incident_type, "fees_or_exchange")
        self.assertFalse(consumer.payment_service)
        self.assertEqual(consumer.complaint_response_months, 1)

        business = self._resolve(
            incident_type="Comisión bancaria no informada",
            issue_text="Comisión bancaria en una cuenta de empresa.",
            operation_authorized=None,
            user_type="Empresa",
        )
        self.assertEqual(business.complaint_response_months, 2)

    def test_foreign_future_loan_and_investment_cases_fail_closed(self):
        foreign = self._resolve(bank_country="Portugal")
        self.assertEqual(foreign.status, "operator_review")
        self.assertFalse(foreign.legal_basis)

        future = self._resolve(incident_date="2028-01-01")
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)

        loan = self._resolve(
            incident_type="Préstamo hipotecario",
            issue_text="Controversia sobre una hipoteca.",
            operation_authorized=None,
            loan_involved=True,
        )
        self.assertEqual(loan.status, "operator_review")
        self.assertFalse(loan.legal_basis)

        investment = self._resolve(
            incident_type="Fondo de inversión",
            issue_text="Pérdidas en un producto de inversión.",
            operation_authorized=None,
            investment_involved=True,
        )
        self.assertEqual(investment.status, "operator_review")
        self.assertFalse(investment.legal_basis)


if __name__ == "__main__":
    unittest.main()
