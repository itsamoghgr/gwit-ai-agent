"""
models.py — SQLAlchemy ORM definitions for all pipeline tables.
app/ is the project root. Flat import style.

Requires pgvector PostgreSQL extension:
    sudo dnf install pgvector_17   # PostgreSQL 17
"""
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()

def _uuid():  return str(uuid.uuid4())
def _now():   return datetime.utcnow()


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    run_id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    started_at      = Column(DateTime, default=_now, nullable=False)
    finished_at     = Column(DateTime, nullable=True)
    status          = Column(String(50), default="running")
    config_snapshot = Column(JSONB, nullable=True)
    notes           = Column(Text, nullable=True)


class TicketEmbedding(Base):
    """Unified embedding table holding both incidents and work orders."""
    __tablename__ = "ticket_embeddings"
    __table_args__ = (UniqueConstraint("source", "source_id", "run_id", name="uq_ticket_embed_run"),)
    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    source         = Column(String(20),  nullable=False, index=True)   # 'incident' | 'workorder'
    source_id      = Column(UUID(as_uuid=False), nullable=False, index=True)  # FK to incidents_processed or workorders_processed
    run_id         = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    problem_vec    = Column(Vector(1536), nullable=True)
    res_vec        = Column(Vector(1536), nullable=True)
    quality_pass   = Column(Boolean, default=True)
    res_word_count = Column(Integer, nullable=True)
    evidence_tier  = Column(Integer, nullable=True)   # 1=best … 4=unusable
    created_at     = Column(DateTime, default=_now)


class Cluster(Base):
    __tablename__ = "clusters"
    __table_args__ = (UniqueConstraint("cluster_id", "run_id", name="uq_cluster_run"),)
    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    cluster_id       = Column(Integer, nullable=False, index=True)
    run_id           = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    cluster_label    = Column(String(300), nullable=True)
    top_terms        = Column(Text, nullable=True)
    size             = Column(Integer, nullable=True)
    centroid         = Column(Vector(1536), nullable=True)
    max_kb_sim       = Column(Float, nullable=True)
    threshold_p25    = Column(Float, nullable=True)
    threshold_p60    = Column(Float, nullable=True)
    gap_flag         = Column(String(20), nullable=True)
    silhouette_score = Column(Float, nullable=True)
    pca_x            = Column(Float, nullable=True)
    pca_y            = Column(Float, nullable=True)
    created_at       = Column(DateTime, default=_now)


class ClusterSweep(Base):
    """One row per K tested during the elbow sweep in Phase 3."""
    __tablename__  = "cluster_sweep"
    __table_args__ = (UniqueConstraint("run_id", "k", name="uq_sweep_run_k"),)
    id         = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id     = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    k          = Column(Integer,  nullable=False)
    inertia    = Column(Float,    nullable=True)
    silhouette = Column(Float,    nullable=True)
    is_best_k  = Column(Boolean,  default=False)
    created_at = Column(DateTime, default=_now)


class ClusterAssignment(Base):
    __tablename__ = "cluster_assignments"
    __table_args__ = (UniqueConstraint("source", "source_id", "run_id", name="uq_assign_run"),)
    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    source         = Column(String(20),  nullable=False, index=True, default="incident")  # 'incident' | 'workorder'
    source_id      = Column(UUID(as_uuid=False), nullable=False, index=True)  # FK to source table
    run_id         = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    cluster_id     = Column(Integer, nullable=False, index=True)
    cluster_label  = Column(String(300), nullable=True)
    service_type   = Column(String(100), nullable=True)
    assigned_group = Column(String(200), nullable=True)
    ticket_number  = Column(String(100), nullable=True)


class GapAnalysis(Base):
    __tablename__ = "gap_analysis"
    __table_args__ = (UniqueConstraint("cluster_id", "run_id", name="uq_gap_run"),)
    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    cluster_id     = Column(Integer, nullable=False, index=True)
    run_id         = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    cluster_label  = Column(String(300), nullable=True)
    size           = Column(Integer, nullable=True)
    max_kb_sim     = Column(Float, nullable=True)
    avg_top3_sim   = Column(Float, nullable=True)
    n_above_p75    = Column(Float, nullable=True)
    coverage_score = Column(Float, nullable=True)
    priority_score = Column(Float, nullable=True)
    gap_flag       = Column(String(20), nullable=True)
    recommendation = Column(Text, nullable=True)
    best_kb_title  = Column(Text, nullable=True)
    best_kb_idx    = Column(Integer, nullable=True)
    top_terms      = Column(Text, nullable=True)
    created_at     = Column(DateTime, default=_now)


class KBUtilization(Base):
    __tablename__ = "kb_utilization"
    __table_args__ = (UniqueConstraint("kb_idx", "run_id", name="uq_kbutil_run"),)
    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    kb_idx           = Column(Integer, nullable=False, index=True)
    run_id           = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    kb_article_id    = Column(UUID(as_uuid=False), nullable=True)
    title            = Column(Text, nullable=True)
    clusters_as_best = Column(Integer, default=0)
    max_cluster_sim  = Column(Float, nullable=True)
    mean_cluster_sim = Column(Float, nullable=True)
    breadth_count    = Column(Integer, nullable=True)
    status           = Column(String(20), nullable=True)
    created_at       = Column(DateTime, default=_now)


class GeneratedKBArticle(Base):
    __tablename__ = "generated_kb_articles"
    __table_args__ = (UniqueConstraint("cluster_id", "run_id", name="uq_article_run"),)
    article_id         = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id             = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    cluster_id         = Column(Integer, nullable=False, index=True)
    cluster_label      = Column(String(300), nullable=True)
    ticket_count       = Column(Integer, nullable=True)
    priority_score     = Column(Float, nullable=True)
    gap_flag           = Column(String(20), nullable=True)
    title              = Column(Text, nullable=True)
    category           = Column(String(100), nullable=True)
    keywords           = Column(JSONB, nullable=True)
    problem_statement  = Column(Text, nullable=True)
    symptoms           = Column(Text, nullable=True)
    affected_systems   = Column(JSONB, nullable=True)
    resolution_steps   = Column(JSONB, nullable=True)
    additional_notes   = Column(Text, nullable=True)
    escalation_trigger = Column(Text, nullable=True)
    full_article_json  = Column(JSONB, nullable=True)
    confidence         = Column(Float, nullable=True)
    quality_score      = Column(Integer, nullable=True)
    quality_issues     = Column(JSONB, nullable=True)
    needs_review       = Column(Boolean, default=False)
    model              = Column(String(100), nullable=True)
    evidence_count     = Column(Integer, nullable=True)
    article_embedding  = Column(Vector(1536), nullable=True)
    is_duplicate_of    = Column(UUID(as_uuid=False), nullable=True)   # points to article_id of canonical article
    generated_at       = Column(DateTime, default=_now)


class KBSearchIndex(Base):
    __tablename__ = "kb_search_index"
    __table_args__ = (UniqueConstraint("source", "source_id", "run_id", name="uq_search_source_run"),)
    entry_id     = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id       = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    source       = Column(String(50), nullable=False)
    source_id    = Column(String(100), nullable=False)
    title        = Column(Text, nullable=True)
    category     = Column(String(100), nullable=True)
    content      = Column(Text, nullable=True)
    embedding    = Column(Vector(1536), nullable=True)
    is_generated = Column(Boolean, default=False)
    indexed_at   = Column(DateTime, default=_now)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    eval_id     = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id      = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    metric      = Column(String(200), nullable=False)
    result      = Column(Text, nullable=True)
    target      = Column(Text, nullable=True)
    status      = Column(String(50), nullable=True)
    category    = Column(String(100), nullable=True)
    detail_json = Column(JSONB, nullable=True)
    created_at  = Column(DateTime, default=_now)


# ── Phase 3 — Full cluster × KB similarity matrix ─────────────────────────────
class ClusterKBSim(Base):
    """One row per (cluster, KB article) pair — full similarity matrix from Phase 3."""
    __tablename__  = "cluster_kb_sim"
    __table_args__ = (UniqueConstraint("run_id", "cluster_id", "kb_idx", name="uq_cluster_kb_sim"),)
    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id      = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    cluster_id  = Column(Integer, nullable=False, index=True)
    kb_idx      = Column(Integer, nullable=False)
    kb_title    = Column(Text, nullable=True)
    similarity  = Column(Float, nullable=False)


# ── Phase 6 — Before/after gap flag counts ────────────────────────────────────
class CoverageDelta(Base):
    """3 rows per run: CRITICAL/PARTIAL/COVERED counts before and after article generation."""
    __tablename__  = "coverage_delta"
    __table_args__ = (UniqueConstraint("run_id", "flag", name="uq_coverage_delta_run_flag"),)
    id           = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id       = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    flag         = Column(String(20), nullable=False)   # CRITICAL / PARTIAL / COVERED
    count_before = Column(Integer, nullable=False)
    count_after  = Column(Integer, nullable=False)
    delta        = Column(Integer, nullable=False)       # count_after - count_before
    created_at   = Column(DateTime, default=_now)


# ── Phase 6 — Per-service gap distribution ────────────────────────────────────
class ServiceGapDistribution(Base):
    """One row per service_type: gap flag breakdown from the Phase 6 bias audit."""
    __tablename__  = "service_gap_distribution"
    __table_args__ = (UniqueConstraint("run_id", "service_type", name="uq_sgd_run_svc"),)
    id           = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id       = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    service_type = Column(String(200), nullable=False)
    critical     = Column(Integer, default=0)
    partial      = Column(Integer, default=0)
    covered      = Column(Integer, default=0)
    total        = Column(Integer, default=0)
    created_at   = Column(DateTime, default=_now)


# ── Phase 6 — Bias audit (chi-square) ────────────────────────────────────────
class BiasAudit(Base):
    """One row per run: chi-squared fairness test result from Phase 6."""
    __tablename__ = "bias_audit"
    id                  = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id              = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, unique=True, index=True)
    test_name           = Column(String(100), default="chi2_contingency")
    statistic           = Column(Float, nullable=True)
    degrees_of_freedom  = Column(Integer, nullable=True)
    p_value             = Column(Float, nullable=True)
    bias_detected       = Column(Boolean, nullable=True)
    most_affected       = Column(String(200), nullable=True)
    created_at          = Column(DateTime, default=_now)



# ── AI Chat — conversation persistence & feedback ────────────────────────────

class ChatSession(Base):
    """One row per browser session on the AI Chat page."""
    __tablename__ = "chat_sessions"
    session_id    = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id        = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=True, index=True)
    started_at    = Column(DateTime, default=_now)
    message_count = Column(Integer, default=0)


class ChatMessage(Base):
    """Every user + assistant turn, with optional embedding for user messages."""
    __tablename__ = "chat_messages"
    message_id  = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    session_id  = Column(UUID(as_uuid=False), ForeignKey("chat_sessions.session_id"), nullable=False, index=True)
    run_id      = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=True, index=True)
    role        = Column(String(20), nullable=False)        # 'user' | 'assistant'
    content     = Column(Text, nullable=False)
    sources     = Column(JSONB, nullable=True)              # KB chunks used
    q_embedding = Column(Vector(1536), nullable=True)       # user messages only
    created_at  = Column(DateTime, default=_now)


class ChatFeedback(Base):
    """User corrections — both implicit (detected from chat) and explicit (👎 button)."""
    __tablename__ = "chat_feedback"
    feedback_id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id      = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=True, index=True)
    session_id  = Column(UUID(as_uuid=False), ForeignKey("chat_sessions.session_id"), nullable=True, index=True)
    message_id  = Column(UUID(as_uuid=False), ForeignKey("chat_messages.message_id"), nullable=True, index=True)
    question    = Column(Text, nullable=False)
    bad_answer  = Column(Text, nullable=True)
    correction  = Column(Text, nullable=False)
    q_embedding = Column(Vector(1536), nullable=True)       # for correction retrieval
    thumbs      = Column(Integer, default=0)                # +1 / -1 / 0
    source      = Column(String(20), default="implicit")    # 'implicit' | 'thumbs_down'
    verified    = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=_now)
class TicketCoverage(Base):
    """Per-ticket KB coverage result from Phase 3 ANN matching."""
    __tablename__ = "ticket_coverage"
    __table_args__ = (UniqueConstraint("source", "source_id", "run_id", name="uq_ticket_coverage_run"),)
    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id        = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    source        = Column(String(20), nullable=False, index=True)   # 'incident' | 'workorder'
    source_id     = Column(UUID(as_uuid=False), nullable=False, index=True)
    max_kb_sim    = Column(Float, nullable=True)
    best_kb_id    = Column(String(100), nullable=True)
    best_kb_title = Column(Text, nullable=True)
    coverage      = Column(String(20), nullable=False, index=True)   # COVERED | PARTIAL | UNCOVERED
    created_at    = Column(DateTime, default=_now)


class KBValidationResult(Base):
    """One row per generated article — Phase 7 LLM critique + embedding validation."""
    __tablename__ = "kb_validation_results"
    __table_args__ = (UniqueConstraint("article_id", "run_id", name="uq_kbval_run"),)
    id                      = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id                  = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    article_id              = Column(UUID(as_uuid=False), ForeignKey("generated_kb_articles.article_id"), nullable=False, index=True)
    cluster_id              = Column(Integer, nullable=False)
    gap_flag                = Column(String(20), nullable=True)          # CRITICAL | PARTIAL (from generation)
    # LLM critique (Signal 1)
    llm_coverage_score      = Column(Float, nullable=True)               # 0-10
    llm_gap_confirmed       = Column(Boolean, nullable=True)
    llm_verdict             = Column(String(20), nullable=True)          # VALIDATED | PARTIAL | INSUFFICIENT
    llm_critique            = Column(Text, nullable=True)                # 1-2 sentence summary
    llm_missing_elements    = Column(JSONB, nullable=True)               # list of missing items
    llm_fallback            = Column(Boolean, default=False)             # True if embedding-only (LLM failed)
    # Embedding checks (Signal 2)
    max_existing_kb_sim     = Column(Float, nullable=True)               # similarity to closest existing KB
    closest_existing_kb     = Column(Text, nullable=True)                # title of closest existing KB article
    cluster_alignment_score = Column(Float, nullable=True)               # similarity to cluster centroid
    novelty_pass            = Column(Boolean, nullable=True)             # max_existing_kb_sim < 0.87
    alignment_pass          = Column(Boolean, nullable=True)             # cluster_alignment_score >= 0.75
    # Final
    validation_status       = Column(String(20), nullable=True)          # VALIDATED | PARTIAL | FAIL
    created_at              = Column(DateTime, default=_now)


class PIIFinding(Base):
    """One row per PII match found in a generated KB article during Phase 6 audit."""
    __tablename__ = "pii_findings"
    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    run_id      = Column(UUID(as_uuid=False), ForeignKey("pipeline_runs.run_id"), nullable=False, index=True)
    article_id  = Column(UUID(as_uuid=False), ForeignKey("generated_kb_articles.article_id"), nullable=True, index=True)
    cluster_id  = Column(Integer, nullable=True)
    field       = Column(String(100), nullable=True)    # e.g. 'resolution_steps'
    pii_type    = Column(String(100), nullable=True)    # e.g. 'Email address'
    severity    = Column(String(20), nullable=True)     # HIGH / MEDIUM / LOW
    created_at  = Column(DateTime, default=_now)

