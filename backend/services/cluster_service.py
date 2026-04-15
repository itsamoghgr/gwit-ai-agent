"""
services/cluster_service.py
Business logic for all cluster-related data.
No HTTP concerns here — only DB queries and data transformations.
"""
import math
import re
from typing import Any, Dict, List, Optional, Set

from core.database import qdf, scalar, safe_val
from schemas.clusters import (
    ClusterOut, TicketOut, SweepRow,
    ServiceBreakdownRow, SourceMixOut,
    SourceTotalRow, SourcePerClusterRow, WoStats,
)

# Per-run clusters to hide (matches dashboard.py)
_EXCLUSIONS: Dict[str, Set[int]] = {
    "6e6de72b-c4b4-4a05-9ac5-61c030ef5b0d": {157, 112, 74, 58, 107},
}


def _extract_canonical(recommendation: Any) -> Optional[int]:
    if not isinstance(recommendation, str):
        return None
    m = re.search(r"cluster\s+(\d+)", recommendation, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _row_to_cluster(row: Any) -> ClusterOut:
    return ClusterOut(
        cluster_id=int(row["cluster_id"]),
        cluster_label=str(row["cluster_label"]),
        size=int(row["size"]),
        gap_flag=str(row["gap_flag"]),
        max_kb_sim=safe_val(row.get("max_kb_sim")),
        silhouette_score=safe_val(row.get("silhouette_score")),
        pca_x=safe_val(row.get("pca_x")),
        pca_y=safe_val(row.get("pca_y")),
        summary=str(row["summary"]) if row.get("summary") else None,
        llm_kb_match=str(row["llm_kb_match"]) if row.get("llm_kb_match") else None,
        llm_confidence=str(row["llm_confidence"]) if row.get("llm_confidence") else None,
        llm_kb_reasoning=str(row["llm_kb_reasoning"]) if row.get("llm_kb_reasoning") else None,
        canonical_cluster_id=(
            int(row["canonical_cluster_id"])
            if (
                row.get("canonical_cluster_id") is not None
                and not (isinstance(row["canonical_cluster_id"], float)
                         and math.isnan(row["canonical_cluster_id"]))
            ) else None
        ),
        wo_tickets=int(row.get("wo_tickets", 0) or 0),
        inc_tickets=int(row.get("inc_tickets", 0) or 0),
    )


def fetch_clusters(run_id: str, source: Optional[str] = None) -> List[ClusterOut]:
    """Return all clusters for a run, enriched with gap flags, LLM data, and source counts."""
    df = qdf(
        "SELECT cluster_id, cluster_label, size, gap_flag, max_kb_sim, "
        "silhouette_score, pca_x, pca_y, summary, "
        "llm_kb_match, llm_confidence, llm_kb_reasoning "
        "FROM clusters WHERE run_id=:r ORDER BY size DESC",
        {"r": run_id},
    )
    if df.empty:
        return []

    # Apply per-run exclusions
    excluded = _EXCLUSIONS.get(run_id, set())
    if excluded:
        df = df[~df["cluster_id"].isin(excluded)].reset_index(drop=True)

    # --- Enrich: gap_analysis resolved flags ---
    ga = qdf(
        "SELECT cluster_id, gap_flag AS ga_gap_flag, recommendation AS ga_rec "
        "FROM gap_analysis WHERE run_id=:r",
        {"r": run_id},
    )
    if not ga.empty:
        df = df.merge(ga, on="cluster_id", how="left")
        df["gap_flag"] = df["ga_gap_flag"].combine_first(df["gap_flag"])
        df["canonical_cluster_id"] = df.apply(
            lambda r: _extract_canonical(r.get("ga_rec"))
            if str(r.get("gap_flag")) == "DUPLICATE" else None,
            axis=1,
        )
        df = df.drop(columns=["ga_gap_flag", "ga_rec"])
    else:
        df["canonical_cluster_id"] = None

    # --- Enrich: cluster_kb_matches (PARTIAL/COVERED coverage) ---
    ckm = qdf(
        "SELECT cluster_id, matched_kb_title AS ckm_match, "
        "confidence AS ckm_conf, reasoning AS ckm_reason "
        "FROM cluster_kb_matches WHERE run_id=:r",
        {"r": run_id},
    )
    if not ckm.empty:
        df = df.merge(ckm, on="cluster_id", how="left")
        empty = df["llm_kb_match"].isna() | (df["llm_kb_match"] == "")
        df.loc[empty, "llm_kb_match"]     = df.loc[empty, "ckm_match"]
        df.loc[empty, "llm_confidence"]   = df.loc[empty, "ckm_conf"]
        df.loc[empty, "llm_kb_reasoning"] = df.loc[empty, "ckm_reason"]
        df = df.drop(columns=["ckm_match", "ckm_conf", "ckm_reason"])

    # --- Override: CRITICAL + HIGH LLM confidence → PARTIAL ---
    mask = (
        (df["gap_flag"] == "CRITICAL")
        & (df["llm_confidence"] == "HIGH")
        & df["llm_kb_match"].notna()
        & (df["llm_kb_match"] != "")
    )
    df.loc[mask, "gap_flag"] = "PARTIAL"

    # --- Enrich: per-cluster source ticket counts ---
    src = qdf(
        "SELECT cluster_id, "
        "SUM(CASE WHEN source='workorder' THEN 1 ELSE 0 END) AS wo_tickets, "
        "SUM(CASE WHEN source='incident'  THEN 1 ELSE 0 END) AS inc_tickets "
        "FROM cluster_assignments WHERE run_id=:r GROUP BY cluster_id",
        {"r": run_id},
    )
    if not src.empty:
        df = df.merge(src, on="cluster_id", how="left")
        df["wo_tickets"]  = df["wo_tickets"].fillna(0).astype(int)
        df["inc_tickets"] = df["inc_tickets"].fillna(0).astype(int)
    else:
        df["wo_tickets"] = 0
        df["inc_tickets"] = 0

    # --- Filter by source type if requested ---
    if source in ("incident", "workorder"):
        src_ids = qdf(
            "SELECT DISTINCT cluster_id FROM cluster_assignments "
            "WHERE source=:src AND run_id=:r",
            {"src": source, "r": run_id},
        )
        if not src_ids.empty:
            df = df[df["cluster_id"].isin(src_ids["cluster_id"])].reset_index(drop=True)

    return [_row_to_cluster(row) for _, row in df.iterrows()]


def fetch_tickets(run_id: str, cluster_ids: List[int]) -> List[TicketOut]:
    """Fetch ticket detail for one or more cluster IDs (handles merged duplicates)."""
    df = qdf("""
        SELECT ca.cluster_id, ca.source, ca.ticket_number, ca.service_type,
               ca.assigned_group,
               COALESCE(ip.detailed_description, ip.description, '') AS problem_text,
               COALESCE(ip.resolution_summary, '')                   AS resolution_text
        FROM   cluster_assignments ca
        JOIN   incidents_processed ip ON ca.source_id::text = ip.id::text
        WHERE  ca.cluster_id = ANY(:cids) AND ca.run_id = :r AND ca.source = 'incident'

        UNION ALL

        SELECT ca.cluster_id, ca.source, ca.ticket_number, ca.service_type,
               ca.assigned_group,
               COALESCE(wp.detailed_description, wp.description, '') AS problem_text,
               COALESCE(wp.activity_logs_text, '')                   AS resolution_text
        FROM   cluster_assignments ca
        JOIN   workorders_processed wp ON ca.source_id::text = wp.id::text
        WHERE  ca.cluster_id = ANY(:cids) AND ca.run_id = :r AND ca.source = 'workorder'

        ORDER BY cluster_id, source, ticket_number
    """, {"cids": cluster_ids, "r": run_id})

    if df.empty:
        return []
    df = df.fillna("")
    return [
        TicketOut(
            cluster_id=int(r["cluster_id"]),
            source=str(r["source"]),
            ticket_number=str(r["ticket_number"]),
            service_type=str(r["service_type"]),
            assigned_group=str(r["assigned_group"]),
            problem_text=str(r["problem_text"]),
            resolution_text=str(r["resolution_text"]),
        )
        for _, r in df.iterrows()
    ]


def fetch_sweep(run_id: str) -> List[SweepRow]:
    """Elbow / silhouette sweep data for the Elbow tab chart."""
    df = qdf(
        "SELECT k, inertia, silhouette, is_best_k "
        "FROM cluster_sweep WHERE run_id=:r ORDER BY k",
        {"r": run_id},
    )
    if df.empty:
        return []
    return [
        SweepRow(
            k=int(r["k"]),
            inertia=float(r["inertia"]),
            silhouette=float(r["silhouette"]),
            is_best_k=bool(r["is_best_k"]),
        )
        for _, r in df.iterrows()
    ]


def fetch_service_breakdown(run_id: str, source: Optional[str] = None) -> List[ServiceBreakdownRow]:
    """Stacked bar data: tickets × service_type × gap_flag."""
    clause = "AND ca.source=:src" if source else ""
    params: dict = {"r": run_id}
    if source:
        params["src"] = source
    df = qdf(f"""
        SELECT ca.service_type, ga.gap_flag, COUNT(*) AS tickets
        FROM   cluster_assignments ca
        JOIN   gap_analysis ga ON ca.cluster_id = ga.cluster_id AND ca.run_id = ga.run_id
        WHERE  ca.run_id=:r {clause}
        GROUP  BY ca.service_type, ga.gap_flag
        ORDER  BY tickets DESC
    """, params)
    if df.empty:
        return []
    return [
        ServiceBreakdownRow(
            service_type=str(r["service_type"]),
            gap_flag=str(r["gap_flag"]),
            tickets=int(r["tickets"]),
        )
        for _, r in df.iterrows()
    ]


def fetch_source_mix(run_id: str) -> SourceMixOut:
    """Source Mix tab: overall donut + per-cluster stacked bar + WO cluster stats."""
    overall_df = qdf(
        "SELECT source, COUNT(*) AS tickets FROM cluster_assignments "
        "WHERE run_id=:r GROUP BY source",
        {"r": run_id},
    )
    per_cluster_df = qdf(
        "SELECT ca.cluster_id, c.cluster_label, ca.source, COUNT(*) AS tickets "
        "FROM cluster_assignments ca "
        "JOIN clusters c ON ca.cluster_id = c.cluster_id AND ca.run_id = c.run_id "
        "WHERE ca.run_id=:r GROUP BY ca.cluster_id, c.cluster_label, ca.source",
        {"r": run_id},
    )
    clusters_with_wo = scalar(
        "SELECT COUNT(DISTINCT cluster_id) FROM cluster_assignments "
        "WHERE source='workorder' AND run_id=:r",
        {"r": run_id},
    )
    wo_dominant = scalar("""
        SELECT COUNT(*) FROM (
            SELECT cluster_id,
                SUM(CASE WHEN source='workorder' THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0) AS ratio
            FROM cluster_assignments WHERE run_id=:r
            GROUP BY cluster_id
            HAVING SUM(CASE WHEN source='workorder' THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0) > 0.5
        ) t
    """, {"r": run_id})
    total_clusters = scalar(
        "SELECT COUNT(*) FROM clusters WHERE run_id=:r", {"r": run_id}
    )

    return SourceMixOut(
        overall=[
            SourceTotalRow(source=str(r["source"]), tickets=int(r["tickets"]))
            for _, r in overall_df.iterrows()
        ] if not overall_df.empty else [],
        per_cluster=[
            SourcePerClusterRow(
                cluster_id=int(r["cluster_id"]),
                cluster_label=str(r["cluster_label"]),
                source=str(r["source"]),
                tickets=int(r["tickets"]),
            )
            for _, r in per_cluster_df.iterrows()
        ] if not per_cluster_df.empty else [],
        wo_stats=WoStats(
            clusters_with_wo=clusters_with_wo,
            wo_dominant=wo_dominant,
            total_clusters=total_clusters,
        ),
    )
