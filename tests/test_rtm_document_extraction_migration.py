import unittest

from rtm_core.document_extraction_migration import (
    DOCUMENT_EXTRACTION_SCHEMA_VERSION,
    document_extraction_ddl,
)


class DocumentExtractionMigrationTest(unittest.TestCase):
    def test_version_is_explicit(self):
        self.assertEqual(
            DOCUMENT_EXTRACTION_SCHEMA_VERSION,
            "rtm_document_extraction_schema_v1_0",
        )

    def test_migration_is_additive_and_idempotent(self):
        sql = "\n".join(statement.lower() for _, statement in document_extraction_ddl())
        self.assertNotIn("drop table", sql)
        self.assertNotIn("truncate", sql)
        self.assertNotIn("delete from", sql)
        self.assertIn(
            "create table if not exists rtm_document_extractions",
            sql,
        )
        self.assertIn("add column if not exists source_extraction_id", sql)
        self.assertIn("fk_rtm_facts_source_extraction", sql)

    def test_one_active_extraction_per_case(self):
        sql = "\n".join(statement.lower() for _, statement in document_extraction_ddl())
        self.assertIn("uq_rtm_active_document_extraction", sql)
        self.assertIn("where invalidated_at is null", sql)

    def test_facts_keep_exact_extraction_link(self):
        sql = "\n".join(statement.lower() for _, statement in document_extraction_ddl())
        self.assertIn(
            "foreign key (source_extraction_id)",
            sql,
        )
        self.assertIn(
            "references rtm_document_extractions(id)",
            sql,
        )


if __name__ == "__main__":
    unittest.main()
