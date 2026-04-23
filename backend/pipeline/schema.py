"""
pipeline/schema.py — Database bootstrap for the pipeline tables.

Ported from app_gw-it/db.py init_db(). Idempotent:
  1. Enable pgvector extension.
  2. CREATE TABLE IF NOT EXISTS for every ORM model in pipeline.models.
  3. Run ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS migrations.
"""
import logging
from sqlalchemy import text
from sqlalchemy.engine import Engine

from pipeline.models import Base

log = logging.getLogger(__name__)


_MIGRATIONS = [
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS pca_x DOUBLE PRECISION",
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS pca_y DOUBLE PRECISION",
    "ALTER TABLE cluster_assignments ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'incident'",
    "ALTER TABLE cluster_assignments ADD COLUMN IF NOT EXISTS source_id UUID",
    """DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='cluster_assignments' AND column_name='incident_id'
  ) THEN
    UPDATE cluster_assignments
    SET source_id = incident_id::uuid
    WHERE source_id IS NULL AND incident_id IS NOT NULL;
  END IF;
END $$""",
    "CREATE INDEX IF NOT EXISTS ix_chat_msg_embedding ON chat_messages USING ivfflat (q_embedding vector_cosine_ops) WITH (lists = 10)",
    "CREATE INDEX IF NOT EXISTS ix_chat_fb_embedding  ON chat_feedback  USING ivfflat (q_embedding vector_cosine_ops) WITH (lists = 10)",
    "ALTER TABLE ticket_embeddings ADD COLUMN IF NOT EXISTS evidence_tier INTEGER",
    """CREATE TABLE IF NOT EXISTS ticket_coverage (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id        UUID REFERENCES pipeline_runs(run_id),
        source        VARCHAR(20) NOT NULL,
        source_id     UUID NOT NULL,
        max_kb_sim    DOUBLE PRECISION,
        best_kb_id    VARCHAR(100),
        best_kb_title TEXT,
        coverage      VARCHAR(20) NOT NULL,
        created_at    TIMESTAMP DEFAULT NOW(),
        CONSTRAINT uq_ticket_coverage_run UNIQUE (source, source_id, run_id)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_ticket_coverage_run ON ticket_coverage(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_coverage_cov ON ticket_coverage(coverage)",
    "ALTER TABLE generated_kb_articles ADD COLUMN IF NOT EXISTS is_duplicate_of UUID",
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS summary TEXT",
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS llm_kb_match TEXT",
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS llm_confidence VARCHAR(10)",
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS llm_kb_reasoning TEXT",
    "ALTER TABLE clusters ADD COLUMN IF NOT EXISTS intra_sim DOUBLE PRECISION",
    "ALTER TABLE generated_kb_articles ADD COLUMN IF NOT EXISTS symptoms TEXT",
]


def init_pipeline_schema(engine: Engine) -> None:
    log.info("Initialising pipeline schema (CREATE TABLE / ALTER / INDEX IF NOT EXISTS)...")
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            conn.execute(text(stmt))
        conn.commit()
    log.info("Pipeline schema ready.")
