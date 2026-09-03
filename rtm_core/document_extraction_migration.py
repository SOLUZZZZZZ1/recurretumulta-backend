"""Definición DDL offline del extractor documental RTM.

La migración es aditiva y requiere que la capa de autoridad CORE ya exista. No
borra datos legacy ni modifica el contenido de expedientes existentes. La
ejecución pertenece exclusivamente a ``scripts/rtm_staging_core_schema.py``;
este módulo no publica ni conserva un router HTTP administrativo.
"""

from __future__ import annotations

DOCUMENT_EXTRACTION_SCHEMA_VERSION = "rtm_document_extraction_schema_v1_0"


def document_extraction_ddl() -> list[tuple[str, str]]:
    return [
        (
            "document_extractions",
            """
            CREATE TABLE IF NOT EXISTS rtm_document_extractions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                service TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed'
                    CHECK (status IN ('completed', 'invalidated')),
                extractor_version TEXT NOT NULL,
                provider_version TEXT NOT NULL,
                model TEXT NOT NULL,
                packet JSONB NOT NULL,
                packet_sha256 TEXT NOT NULL,
                diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                invalidated_by TEXT,
                invalidated_at TIMESTAMPTZ,
                invalidation_reason TEXT,
                UNIQUE(case_id, sequence)
            );
            """,
        ),
        (
            "validated_facts_source_extraction",
            """
            ALTER TABLE rtm_validated_facts
                ADD COLUMN IF NOT EXISTS source_extraction_id UUID;
            """,
        ),
        (
            "facts_source_extraction_constraint",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'fk_rtm_facts_source_extraction'
                ) THEN
                    ALTER TABLE rtm_validated_facts
                    ADD CONSTRAINT fk_rtm_facts_source_extraction
                    FOREIGN KEY (source_extraction_id)
                    REFERENCES rtm_document_extractions(id);
                END IF;
            END $$;
            """,
        ),
        (
            "idx_document_extractions_case",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_document_extractions_case
            ON rtm_document_extractions(case_id, sequence DESC);
            """,
        ),
        (
            "idx_document_extractions_status",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_document_extractions_status
            ON rtm_document_extractions(status, created_at DESC);
            """,
        ),
        (
            "uq_active_document_extraction",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_active_document_extraction
            ON rtm_document_extractions(case_id)
            WHERE invalidated_at IS NULL;
            """,
        ),
        (
            "idx_facts_source_extraction",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_facts_source_extraction
            ON rtm_validated_facts(source_extraction_id);
            """,
        ),
    ]
