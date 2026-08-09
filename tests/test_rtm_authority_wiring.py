from pathlib import Path
import unittest


class AuthorityWiringGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Path("app.py").read_text(encoding="utf-8")
        cls.migration = Path("rtm_core/migration_router.py").read_text(encoding="utf-8")
        cls.preview = Path("rtm_core/preview_repository.py").read_text(encoding="utf-8")
        cls.authority = Path("rtm_core/authority_repository.py").read_text(encoding="utf-8")

    def test_authority_router_is_mounted(self):
        self.assertIn("rtm_core.authority_router", self.app)
        self.assertIn("app.include_router(rtm_core_authority_router)", self.app)

    def test_preview_has_hard_links_to_facts_and_family(self):
        self.assertIn("validated_facts_id", self.migration)
        self.assertIn("family_resolution_id", self.migration)
        self.assertIn("fk_rtm_preview_facts", self.migration)
        self.assertIn("fk_rtm_preview_family", self.migration)
        self.assertIn("_active_authority_chain", self.preview)

    def test_authority_has_single_active_versions(self):
        self.assertIn("uq_rtm_active_facts", self.migration)
        self.assertIn("uq_rtm_active_family", self.migration)
        self.assertIn("Ya existe una versión activa de hechos", self.authority)
        self.assertIn("Ya existe una resolución de familia activa", self.authority)

    def test_downstream_is_invalidated_when_authority_changes(self):
        self.assertIn("_invalidate_downstream_previews", self.authority)
        self.assertIn("rtm_generated_resources", self.authority)


if __name__ == "__main__":
    unittest.main()
