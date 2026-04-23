"""
services/kb_service.py
Business logic for Generated KB Articles and Existing KB Articles.
No HTTP concerns — only DB queries and data assembly.
"""
import json
import uuid
from typing import Dict, List, Optional

import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values
from sqlalchemy import text

from core.database import qdf, scalar, get_engine
from schemas.kb import (
    KBArticleOut, KBArticleStats, KBArticlesResponse,
    ExistingKBOut, ExistingKBStats, ExistingKBResponse,
    RunOut, KBArticleUpdate,
)
from core.config import get_settings


def update_kb_article(run_id: str, cluster_id: int, patch: KBArticleUpdate) -> bool:
    """Update editable fields of a generated KB article. Returns True if a row was updated."""
    fields: List[str] = []
    params: Dict = {"run_id": run_id, "cluster_id": cluster_id}

    data = patch.model_dump(exclude_none=True)
    if "title" in data:
        fields.append("title = :title")
        params["title"] = data["title"]
    if "problem_statement" in data:
        fields.append("problem_statement = :problem_statement")
        params["problem_statement"] = data["problem_statement"]
    if "symptoms" in data:
        fields.append("symptoms = :symptoms")
        params["symptoms"] = json.dumps(data["symptoms"])
    if "resolution_steps" in data:
        fields.append("resolution_steps = CAST(:resolution_steps AS jsonb)")
        params["resolution_steps"] = json.dumps(data["resolution_steps"])
    if "additional_notes" in data:
        fields.append("additional_notes = :additional_notes")
        params["additional_notes"] = data["additional_notes"]

    if not fields:
        return False

    sql = (
        f"UPDATE generated_kb_articles SET {', '.join(fields)} "
        "WHERE run_id = :run_id AND cluster_id = :cluster_id"
    )
    with get_engine().begin() as conn:
        result = conn.execute(text(sql), params)
        return result.rowcount > 0


def _psycopg2_conn():
    """Raw psycopg2 connection with pgvector adapters registered — required for
    reading/writing the `embedding` vector columns outside SQLAlchemy."""
    url = get_engine().url
    conn = psycopg2.connect(
        host=str(url.host),
        port=url.port or 5432,
        dbname=str(url.database),
        user=str(url.username),
        password=str(url.password),
    )
    register_vector(conn)
    return conn


def reindex_generated_articles(run_id: str) -> Dict[str, int]:
    """Rebuild kb_search_index entries (source='generated') for a run.

    Deletes the run's generated rows from kb_search_index, then re-inserts one
    row per article that has an article_embedding in generated_kb_articles.
    Leaves `generated_kb_articles` and `source='existing'` rows untouched.
    Returns counts so the caller can surface "reindexed N of M articles".
    """
    conn = _psycopg2_conn()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM generated_kb_articles WHERE run_id = %s",
            (run_id,),
        )
        total = cur.fetchone()[0]

        cur.execute(
            "DELETE FROM kb_search_index WHERE run_id = %s AND source = 'generated'",
            (run_id,),
        )
        deleted = cur.rowcount

        cur.execute("""
            SELECT cluster_id, title, category, problem_statement,
                   resolution_steps, article_embedding
            FROM   generated_kb_articles
            WHERE  run_id = %s AND article_embedding IS NOT NULL
        """, (run_id,))
        rows_src = cur.fetchall()

        rows_ins = []
        for cluster_id, title, category, problem, steps_json, embedding in rows_src:
            if isinstance(steps_json, str):
                try:
                    steps = json.loads(steps_json)
                except Exception:
                    steps = []
            else:
                steps = steps_json or []
            steps_flat = " ".join(steps) if isinstance(steps, list) else ""
            content = f"{problem or ''} {steps_flat}".strip()[:500]
            rows_ins.append((
                str(uuid.uuid4()), run_id,
                "generated", str(cluster_id),
                str(title or ""),
                str(category or "Other"),
                content,
                embedding,
                True,
            ))

        if rows_ins:
            execute_values(cur, """
                INSERT INTO kb_search_index
                    (entry_id, run_id, source, source_id, title, category,
                     content, embedding, is_generated)
                VALUES %s
                ON CONFLICT (source, source_id, run_id) DO UPDATE
                    SET embedding = EXCLUDED.embedding,
                        title     = EXCLUDED.title,
                        content   = EXCLUDED.content,
                        category  = EXCLUDED.category
            """, rows_ins, page_size=50)

        conn.commit()
        return {"total": total, "deleted": deleted, "inserted": len(rows_ins)}
    finally:
        conn.close()


# Tables that carry a run_id column and should be wiped when a run is deleted.
# Ordered children-first so FK constraints (e.g. kb_validation_results →
# generated_kb_articles, pii_findings → generated_kb_articles) are satisfied.
_RUN_CHILD_TABLES: tuple[str, ...] = (
    "chat_feedback",
    "chat_messages",
    "chat_sessions",
    "kb_validation_results",
    "pii_findings",
    "bias_audit",
    "generated_kb_articles",
    "evaluation_results",
    "coverage_delta",
    "service_gap_distribution",
    "gap_analysis",
    "kb_utilization",
    "cluster_kb_matches",
    "cluster_kb_sim",
    "cluster_assignments",
    "cluster_sweep",
    "ticket_coverage",
    "clusters",
    "kb_search_index",
    "ticket_embeddings",
)


def delete_run(run_id: str) -> int:
    """Delete a pipeline run and all per-run rows. Returns rows deleted from pipeline_runs."""
    with get_engine().begin() as conn:
        for tbl in _RUN_CHILD_TABLES:
            conn.execute(text(f"DELETE FROM {tbl} WHERE run_id = :r"), {"r": run_id})
        result = conn.execute(
            text("DELETE FROM pipeline_runs WHERE run_id = :r"), {"r": run_id}
        )
        return result.rowcount


def fetch_runs(include_hidden: bool = False, require_clusters: bool = True) -> list[RunOut]:
    """Return pipeline runs for the sidebar selector.

    By default excludes runs in `hidden_run_list`. When `include_hidden=True`,
    returns every run and sets `hidden=True` on filtered IDs so the UI can flag them.
    When `require_clusters=False`, returns runs that haven't produced cluster rows yet
    (so the Manage modal can surface stale / aborted runs for deletion).
    """
    hidden = get_settings().hidden_run_list
    cluster_join = (
        "INNER JOIN (SELECT DISTINCT run_id FROM clusters) c USING (run_id)"
        if require_clusters else ""
    )
    if include_hidden:
        df = qdf(f"""
            SELECT pr.run_id, pr.started_at, pr.status
            FROM   pipeline_runs pr
            {cluster_join}
            ORDER  BY
                CASE pr.status WHEN 'complete' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
                pr.started_at DESC
            LIMIT 200
        """)
    else:
        df = qdf(f"""
            SELECT pr.run_id, pr.started_at, pr.status
            FROM   pipeline_runs pr
            {cluster_join}
            WHERE  pr.run_id::text <> ALL(:hidden)
            ORDER  BY
                CASE pr.status WHEN 'complete' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
                pr.started_at DESC
            LIMIT 20
        """, {"hidden": hidden})
    if df.empty:
        return []
    df["started_at"] = df["started_at"].astype(str)
    df["run_id"]     = df["run_id"].astype(str)
    hidden_set = set(hidden)
    return [
        RunOut(**row, hidden=row["run_id"] in hidden_set)
        for row in df.to_dict(orient="records")
    ]


def fetch_kb_articles(run_id: str) -> KBArticlesResponse:
    """Return generated KB articles with stats summary for a pipeline run."""
    df = qdf("""
        SELECT cluster_id, title, category, quality_score, confidence,
               needs_review, problem_statement, symptoms,
               resolution_steps, additional_notes, is_duplicate_of
        FROM generated_kb_articles WHERE run_id=:r ORDER BY quality_score DESC
    """, {"r": run_id})

    if df.empty:
        return KBArticlesResponse(
            stats=KBArticleStats(canonical=0, duplicates=0, avg_quality=0,
                                 needs_review=0, validated=0),
            articles=[],
        )

    # Enrich with workorder ticket count per cluster
    wo = qdf(
        "SELECT cluster_id, COUNT(*) AS wo_in_cluster "
        "FROM cluster_assignments WHERE source='workorder' AND run_id=:r "
        "GROUP BY cluster_id",
        {"r": run_id},
    )
    if not wo.empty:
        df = df.merge(wo, on="cluster_id", how="left")
        df["wo_in_cluster"] = df["wo_in_cluster"].fillna(0).astype(int)
    else:
        df["wo_in_cluster"] = 0

    # Compute stats
    n_dups = int(
        (df["is_duplicate_of"].notna() & (df["is_duplicate_of"] != "None")).sum()
    ) if "is_duplicate_of" in df.columns else 0
    n_validated = scalar(
        "SELECT COUNT(*) FROM kb_validation_results "
        "WHERE run_id=:r AND validation_status='VALIDATED'",
        {"r": run_id},
    )
    stats = KBArticleStats(
        canonical=len(df) - n_dups,
        duplicates=n_dups,
        avg_quality=round(float(df["quality_score"].mean()), 1),
        needs_review=int(df["needs_review"].sum()),
        validated=n_validated,
    )

    def _confidence_label(val) -> str:
        """Map numeric confidence (0–1) to HIGH / MEDIUM / LOW label."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            # Already a label string e.g. 'HIGH'
            s = str(val).upper()
            return s if s in ("HIGH", "MEDIUM", "LOW") else "LOW"
        if v >= 0.85:
            return "HIGH"
        if v >= 0.60:
            return "MEDIUM"
        return "LOW"

    articles = []
    for _, r in df.fillna("").iterrows():
        # resolution_steps: JSONB → already a Python list from psycopg2.
        # Never call str() on it — that produces a Python repr with mixed quotes
        # which breaks JSON parsing on the frontend when steps contain apostrophes.
        raw_steps = r.get("resolution_steps") or None
        if isinstance(raw_steps, list):
            steps_list: List[str] | None = [str(s) for s in raw_steps]
        elif isinstance(raw_steps, str) and raw_steps:
            try:
                parsed = json.loads(raw_steps)
                steps_list = [str(s) for s in parsed] if isinstance(parsed, list) else [raw_steps]
            except (json.JSONDecodeError, ValueError):
                steps_list = [raw_steps]
        else:
            steps_list = None

        # symptoms: TEXT column storing a JSON-array string (from LLM output).
        raw_symptoms = r.get("symptoms") or None
        if isinstance(raw_symptoms, list):
            sym_list: List[str] | None = [str(s) for s in raw_symptoms]
        elif isinstance(raw_symptoms, str) and raw_symptoms:
            try:
                parsed_sym = json.loads(raw_symptoms)
                sym_list = [str(s) for s in parsed_sym] if isinstance(parsed_sym, list) else [raw_symptoms]
            except (json.JSONDecodeError, ValueError):
                sym_list = [s.strip() for s in raw_symptoms.split("\n") if s.strip()]
        else:
            sym_list = None

        articles.append(KBArticleOut(
            cluster_id=int(r["cluster_id"]),
            title=str(r["title"]),
            category=str(r["category"]),
            quality_score=float(r["quality_score"]),
            confidence=_confidence_label(r["confidence"]),
            needs_review=bool(r["needs_review"]),
            problem_statement=str(r["problem_statement"]),
            symptoms=sym_list,
            resolution_steps=steps_list,
            additional_notes=str(r["additional_notes"]) if r.get("additional_notes") else None,
            is_duplicate_of=str(r["is_duplicate_of"]) if r.get("is_duplicate_of") and r["is_duplicate_of"] != "None" else None,
            wo_in_cluster=int(r["wo_in_cluster"]),
        ))

    return KBArticlesResponse(stats=stats, articles=articles)


def fetch_existing_kb(run_id: Optional[str] = None) -> ExistingKBResponse:
    """Return all existing KB articles, optionally enriched with utilization data."""
    df = qdf("""
        SELECT id, title,
               COALESCE(issue, '')    AS issue,
               COALESCE(solution, '') AS solution
        FROM knowledge_base_articles ORDER BY title
    """)
    if df.empty:
        return ExistingKBResponse(
            stats=ExistingKBStats(total=0, active=0, orphaned=0, over_relied=0),
            articles=[],
        )

    # Optionally enrich with utilization from the selected run
    util_map: dict[str, dict] = {}
    if run_id:
        util_df = qdf(
            "SELECT kb_article_id, status, clusters_as_best "
            "FROM kb_utilization WHERE run_id=:r",
            {"r": run_id},
        )
        if not util_df.empty:
            for _, r in util_df.iterrows():
                util_map[str(r["kb_article_id"])] = {
                    "status": str(r["status"]),
                    "count":  int(r["clusters_as_best"]),
                }

    stats = ExistingKBStats(
        total=len(df),
        active=sum(1 for v in util_map.values() if v["status"] == "ACTIVE"),
        orphaned=sum(1 for v in util_map.values() if v["status"] == "ORPHAN"),
        over_relied=sum(1 for v in util_map.values() if v["status"] == "OVER-RELIED"),
    )

    articles = []
    for _, row in df.iterrows():
        kid  = str(row["id"])
        util = util_map.get(kid)
        articles.append(ExistingKBOut(
            id=kid,
            title=str(row["title"]),
            issue=str(row["issue"]),
            solution=str(row["solution"]),
            util_status=util["status"] if util else None,
            util_count=util["count"]   if util else None,
        ))

    return ExistingKBResponse(stats=stats, articles=articles)
