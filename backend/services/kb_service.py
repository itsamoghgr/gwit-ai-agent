"""
services/kb_service.py
Business logic for Generated KB Articles and Existing KB Articles.
No HTTP concerns — only DB queries and data assembly.
"""
import json
from typing import Dict, List, Optional
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


def fetch_runs() -> list[RunOut]:
    """Return pipeline runs for the sidebar selector, excluding hidden IDs."""
    hidden = get_settings().hidden_run_list
    df = qdf("""
        SELECT pr.run_id, pr.started_at, pr.status
        FROM   pipeline_runs pr
        INNER JOIN (SELECT DISTINCT run_id FROM clusters) c USING (run_id)
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
    return [RunOut(**row) for row in df.to_dict(orient="records")]


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
