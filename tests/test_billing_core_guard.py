from pathlib import Path
import unittest


class BillingCoreGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("billing.py").read_text(encoding="utf-8")

    def test_billing_does_not_import_legacy_expediente_engine(self):
        self.assertNotIn("ai.expediente_engine", self.source)
        self.assertNotIn("run_expediente_ai", self.source)

    def test_billing_does_not_call_generate(self):
        self.assertNotIn("generate_resource", self.source)
        self.assertNotIn("/generate/", self.source)

    def test_review_price_comes_from_core_quote(self):
        self.assertIn("build_case_review_readiness", self.source)
        self.assertIn("readiness.quote", self.source)
        self.assertIn("quote.stripe_price_env", self.source)

    def test_post_payment_only_queues_core_review(self):
        self.assertIn("rtm_core_review_queued", self.source)
        self.assertIn("classification_deferred", self.source)
        self.assertIn("generation_deferred", self.source)


if __name__ == "__main__":
    unittest.main()
