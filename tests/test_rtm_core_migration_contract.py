import unittest

from rtm_core.migration_router import (
    RTM_CORE_AUTHORITY_SCHEMA_VERSION,
    authority_v1_ddl,
)


class CoreMigrationContractTest(unittest.TestCase):
    def test_migration_is_versioned_and_non_destructive(self):
        self.assertEqual(
            RTM_CORE_AUTHORITY_SCHEMA_VERSION,
            "rtm_core_authority_schema_v1_1",
        )
        sql = "\n".join(statement.lower() for _, statement in authority_v1_ddl())
        self.assertNotIn("drop table", sql)
        self.assertNotIn("truncate", sql)
        self.assertNotIn("delete from", sql)

    def test_all_authority_tables_exist_in_contract(self):
        sql = "\n".join(statement.lower() for _, statement in authority_v1_ddl())
        for table in (
            "rtm_validated_facts",
            "rtm_family_resolutions",
            "rtm_legal_previews",
            "rtm_generated_resources",
        ):
            self.assertIn(f"create table if not exists {table}", sql)

    def test_preview_is_linked_to_exact_authorities(self):
        sql = "\n".join(statement.lower() for _, statement in authority_v1_ddl())
        self.assertIn("validated_facts_id", sql)
        self.assertIn("family_resolution_id", sql)
        self.assertIn("fk_rtm_preview_facts", sql)
        self.assertIn("fk_rtm_preview_family", sql)

    def test_one_active_version_per_authority(self):
        sql = "\n".join(statement.lower() for _, statement in authority_v1_ddl())
        self.assertIn("uq_rtm_active_facts", sql)
        self.assertIn("uq_rtm_active_family", sql)
        self.assertIn("uq_rtm_active_preview", sql)
        self.assertIn("'draft', 'ops_review', 'approved', 'frozen'", sql)


if __name__ == "__main__":
    unittest.main()
