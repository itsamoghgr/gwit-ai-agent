"""
phase4_gap_analysis.py — Phase 4: KB Gap Analysis

Reads from:  clusters table, cluster_assignments table,
             ticket_embeddings (for evidence_tier signal),
             knowledge_base_articles table (all DB — no .npy / CSV)
Writes to:   gap_analysis table, kb_utilization table, kb_search_index table

Changes:
  - Absolute calibrated thresholds (0.87 / 0.90) instead of p25/p60
  - Evidence quality signal in priority score
  - Pre-generation centroid deduplication (merge clusters with centroid_sim > 0.88)
"""
import os
import re as _re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import MinMaxScaler, normalize

from dotenv import load_dotenv
from openai import AzureOpenAI

from pipeline.embedding_utils import parse_jsonb_vector, combine_embeddings
from core.pipeline_logger import get_phase_logger as get_logger
from sqlalchemy import text as _text

AZURE_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY    = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VER    = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

SUMMARY_SAMPLE_SIZE = 20   # max tickets sampled per cluster for the summary prompt

log = get_logger(__name__)

# Absolute calibrated gap thresholds (same as Phase 3)
SIM_COVERED = 0.90
SIM_PARTIAL = 0.87

# Evidence tier weights for priority scoring
_TIER_WEIGHT = {1: 3.0, 2: 1.5, 3: 0.5, 4: 0.0}

CENTROID_PREFILTER_SIM = 0.88   # pre-filter: only pairs above this go to GPT


def _get_db_conn(engine):
    url  = engine.url
    conn = psycopg2.connect(
        host=str(url.host), port=url.port or 5432,
        dbname=str(url.database), user=str(url.username),
        password=str(url.password),
    )
    register_vector(conn)
    return conn


def _load_clusters_from_db(engine, run_id: str):
    """Load cluster centroids and summary from clusters table."""
    log.info("Loading clusters from DB...")
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    cur.execute("""
        SELECT cluster_id, cluster_label, size, top_terms,
               centroid, max_kb_sim, threshold_p25, threshold_p60, gap_flag
        FROM   clusters
        WHERE  run_id = %s
        ORDER  BY cluster_id
    """, (run_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    cols = ["cluster_id", "cluster_label", "size", "top_terms",
            "centroid", "max_kb_sim", "threshold_p25", "threshold_p60", "gap_flag"]
    df   = pd.DataFrame(rows, columns=cols)
    centroids = np.vstack(df["centroid"].values).astype(np.float32)
    log.info(f"  Loaded {len(df)} clusters, centroids={centroids.shape}")
    return df, centroids


def _load_kb_embeddings(engine):
    """Load KB embeddings from knowledge_base_articles."""
    df_kb = pd.read_sql(
        "SELECT id, title, issue, solution, e_title, e_solution FROM knowledge_base_articles", engine
    )
    DIM = 1536
    kb_n = len(df_kb)
    kb_m = np.full((kb_n, DIM), np.nan, dtype=np.float32)
    kb_v = np.zeros(kb_n, dtype=bool)
    for i, (_, row) in enumerate(df_kb.iterrows()):
        vec = combine_embeddings(parse_jsonb_vector(row["e_title"]),
                                  parse_jsonb_vector(row["e_solution"]), 0.5, 0.5)
        if vec is not None:
            kb_m[i] = vec; kb_v[i] = True
    kb_clean   = normalize(kb_m[kb_v], norm="l2").astype(np.float32)
    df_kb_meta = df_kb[kb_v].reset_index(drop=True)
    log.info(f"  KB matrix: {kb_clean.shape}")
    return kb_clean, df_kb_meta


def _load_evidence_quality(engine, run_id: str, cluster_ids_list: list) -> dict:
    """Compute evidence_quality score per cluster from evidence_tier distribution.
    Returns {cluster_id: quality_score} where quality_score is 0..3 (higher = better).
    """
    if not cluster_ids_list:
        return {}
    log.info("Loading evidence quality tiers per cluster...")
    try:
        df = pd.read_sql("""
            SELECT ca.cluster_id,
                   COALESCE(te.evidence_tier, 4) AS evidence_tier
            FROM   cluster_assignments ca
            LEFT JOIN ticket_embeddings te
                   ON ca.source_id::text = te.source_id::text
                  AND ca.source = te.source
                  AND te.run_id = %(r)s
            WHERE  ca.run_id = %(r)s
        """, engine, params={"r": run_id})
    except Exception as e:
        log.warning(f"  Could not load evidence tiers: {e} — using default quality=0")
        return {cid: 0.0 for cid in cluster_ids_list}

    quality = {}
    for cid, grp in df.groupby("cluster_id"):
        tiers = grp["evidence_tier"].fillna(4).astype(int)
        score = sum(_TIER_WEIGHT.get(int(t), 0.0) for t in tiers) / max(len(tiers), 1)
        quality[int(cid)] = round(score, 4)
    return quality


def _llm_deduplicate_clusters(
    centroids_norm: np.ndarray,
    summary_rows: list,
    client,
    deployment: str,
) -> list:
    """Two-stage LLM duplicate detection for CRITICAL clusters.

    Stage 1: Cosine pre-filter (centroid_sim > CENTROID_PREFILTER_SIM) to get
             candidate pairs — avoids calling GPT for obviously dissimilar clusters.
    Stage 2: GPT-4.1 decides whether each candidate pair would need the SAME KB
             article. Only marks DUPLICATE if confidence == 'HIGH'.

    Returns updated summary_rows with merged clusters marked via 'merged_into' and
    'llm_dup_reason' keys.
    """
    import time as _time
    import json as _json

    critical_rows = [r for r in summary_rows if r["gap_flag"] == "CRITICAL"]
    if len(critical_rows) < 2:
        log.info("  LLM dedup: fewer than 2 CRITICAL clusters — nothing to check.")
        return summary_rows

    crit_ids  = [r["cluster_id"] for r in critical_rows]
    all_ids   = [r["cluster_id"] for r in summary_rows]
    id_to_idx = {cid: i for i, cid in enumerate(all_ids)}
    crit_idxs = [id_to_idx[cid] for cid in crit_ids]

    crit_c  = centroids_norm[crit_idxs]
    sim_mat = (crit_c @ crit_c.T).astype(float)

    # ── Stage 1: collect candidate pairs above pre-filter threshold ────────────
    candidates = []
    for i in range(len(crit_ids)):
        for j in range(i + 1, len(crit_ids)):
            if float(sim_mat[i, j]) > CENTROID_PREFILTER_SIM:
                candidates.append((i, j, float(sim_mat[i, j])))

    log.info(
        f"  LLM dedup: {len(critical_rows)} CRITICAL clusters, "
        f"{len(candidates)} candidate pairs above {CENTROID_PREFILTER_SIM} cosine threshold."
    )
    if not candidates:
        log.info("  LLM dedup: no candidate pairs — all clusters are distinct.")
        return summary_rows

    # ── Stage 2: ask GPT-4.1 for each candidate pair ──────────────────────────
    SYSTEM_PROMPT = (
        "You are an IT Knowledge Base architect at George Washington University. "
        "Determine whether two IT support ticket clusters describe the SAME underlying IT problem "
        "that would require the SAME knowledge base article to resolve.\n\n"
        'Respond ONLY with valid JSON: {"is_duplicate": true|false, '
        '"confidence": "HIGH"|"MEDIUM"|"LOW", "reason": "one concise sentence"}\n\n'
        "TRUE duplicate = both resolved by the EXACT SAME KB article. "
        "FALSE = they need different KB articles, even if topics overlap."
    )

    merged_into    = {}   # cluster_id → canonical_cluster_id
    merged_reasons = {}   # cluster_id → GPT reasoning
    already_merged = set()

    for i, j, sim_score in sorted(candidates, key=lambda x: -x[2]):
        ci = crit_ids[i]
        cj = crit_ids[j]
        if ci in already_merged or cj in already_merged:
            continue

        ri = next(r for r in critical_rows if r["cluster_id"] == ci)
        rj = next(r for r in critical_rows if r["cluster_id"] == cj)

        user_msg = (
            f"Are these two IT support clusters TRUE DUPLICATES "
            f"(same KB article would resolve both)?\n\n"
            f"CLUSTER A (ID={ci}, {ri['size']} tickets)\n"
            f"Label: {ri['cluster_label']}\nTop Terms: {ri.get('top_terms','')}\n"
            f"Summary: {str(ri.get('summary','') or '')[:600]}\n\n"
            f"CLUSTER B (ID={cj}, {rj['size']} tickets)\n"
            f"Label: {rj['cluster_label']}\nTop Terms: {rj.get('top_terms','')}\n"
            f"Summary: {str(rj.get('summary','') or '')[:600]}\n\n"
            f"Cosine similarity: {sim_score:.4f}\nRespond with JSON only."
        )

        try:
            resp       = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=200,
            )
            result     = _json.loads(resp.choices[0].message.content)
            is_dup     = bool(result.get("is_duplicate", False))
            confidence = str(result.get("confidence", "LOW")).upper()
            reason     = str(result.get("reason", ""))

            log.info(
                f"    [{ci}] '{ri['cluster_label']}' vs [{cj}] '{rj['cluster_label']}' "
                f"sim={sim_score:.4f} → {'DUPLICATE' if is_dup else 'DISTINCT'} [{confidence}]"
            )

            if is_dup and confidence == "HIGH":
                canonical, duplicate = (ci, cj) if ri["size"] >= rj["size"] else (cj, ci)
                merged_into[duplicate]    = canonical
                merged_reasons[duplicate] = reason
                already_merged.add(duplicate)
            elif is_dup and confidence == "MEDIUM":
                log.info("    MEDIUM confidence — leaving both as CRITICAL (human review recommended)")

        except Exception as exc:
            log.warning(f"  LLM dedup: call failed for pair ({ci},{cj}): {exc} — treating as distinct")

        _time.sleep(0.2)

    # ── Mark merged clusters in summary_rows ──────────────────────────────────
    if merged_into:
        log.info(f"  LLM dedup: {len(merged_into)} true duplicates confirmed.")
        for r in summary_rows:
            if r["cluster_id"] in merged_into:
                r["merged_into"]    = merged_into[r["cluster_id"]]
                r["llm_dup_reason"] = merged_reasons.get(r["cluster_id"], "")
    else:
        log.info("  LLM dedup: no true duplicates — all CRITICAL clusters are distinct.")

    return summary_rows


# ─────────────────────────────────────────────────────────────────────────────
# Physical merge of DUPLICATE clusters into their canonical clusters
# ─────────────────────────────────────────────────────────────────────────────

def _merge_duplicate_clusters(engine, run_id: str, merged_into: dict) -> None:
    """Physically reassign duplicate cluster tickets to canonical cluster,
    then update size in clusters and gap_analysis tables.

    merged_into: {dup_cluster_id: canonical_cluster_id}
    """
    if not merged_into:
        return

    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    try:
        for dup_id, canonical_id in merged_into.items():
            log.info(f"  Merging cluster {dup_id} → {canonical_id}")

            # Step 1: Reassign tickets in cluster_assignments
            cur.execute("""
                UPDATE cluster_assignments
                SET    cluster_id    = %s,
                       cluster_label = (
                           SELECT cluster_label FROM clusters
                           WHERE  run_id = %s AND cluster_id = %s
                       )
                WHERE  run_id     = %s
                  AND  cluster_id = %s
            """, (canonical_id, run_id, canonical_id, run_id, dup_id))
            moved = cur.rowcount
            log.info(f"    Moved {moved} tickets from cluster {dup_id} → {canonical_id}")

            # Step 2: Get new canonical size
            cur.execute("""
                SELECT COUNT(*) FROM cluster_assignments
                WHERE run_id = %s AND cluster_id = %s
            """, (run_id, canonical_id))
            new_size = cur.fetchone()[0]

            # Step 3: Update clusters table
            cur.execute("""
                UPDATE clusters SET size = %s
                WHERE run_id = %s AND cluster_id = %s
            """, (new_size, run_id, canonical_id))
            cur.execute("""
                UPDATE clusters SET size = 0, gap_flag = 'DUPLICATE'
                WHERE run_id = %s AND cluster_id = %s
            """, (run_id, dup_id))

            # Step 4: Recalculate priority score for canonical using updated sizes
            # Fetch all gap_analysis rows for volume normalization
            cur.execute("""
                SELECT cluster_id, size, priority_score
                FROM gap_analysis WHERE run_id = %s
            """, (run_id,))
            ga_rows = cur.fetchall()

            if ga_rows:
                all_ids    = [r[0] for r in ga_rows]
                all_sizes  = np.array([float(r[1]) for r in ga_rows])
                all_scores = [float(r[2]) for r in ga_rows]

                # Replace canonical's size with the new merged size
                if canonical_id in all_ids:
                    idx = all_ids.index(canonical_id)
                    old_vol_norm = (MinMaxScaler()
                                   .fit_transform(all_sizes.reshape(-1, 1))
                                   .flatten()[idx])
                    all_sizes[idx] = float(new_size)
                    new_vol_norm = (MinMaxScaler()
                                   .fit_transform(all_sizes.reshape(-1, 1))
                                   .flatten()[idx])
                    # Adjust priority: swap out old volume component for new one
                    new_priority = float(round(
                        all_scores[idx] - 0.35 * float(old_vol_norm) + 0.35 * float(new_vol_norm), 4
                    ))
                    cur.execute("""
                        UPDATE gap_analysis
                        SET    size = %s, priority_score = %s
                        WHERE  run_id = %s AND cluster_id = %s
                    """, (int(new_size), new_priority, run_id, canonical_id))

            # Step 5: Zero out duplicate in gap_analysis
            rec = f"DUPLICATE: Merged into cluster {canonical_id}. Tickets physically reassigned."
            cur.execute("""
                UPDATE gap_analysis
                SET    size = 0, gap_flag = 'DUPLICATE', recommendation = %s
                WHERE  run_id = %s AND cluster_id = %s
            """, (rec, run_id, dup_id))

        conn.commit()
        log.info(f"  Physical merge complete: {len(merged_into)} duplicate(s) merged.")
    except Exception as exc:
        conn.rollback()
        log.error(f"  Physical merge failed, rolling back: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Step 10: Cluster Summarization (GPT-4.1)
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_ticket_text(text: str) -> str:
    """Strip PII headers, unredacted emails/phones, JSON blobs, and *** noise
    before sending ticket text to Azure OpenAI to avoid content filter false positives."""
    lines = text.split('\n')
    clean = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Skip PII header lines (Name:, Phone:, Email:, Username:, etc.)
        if _re.match(r'^(Name|Phone|Email|Username|GWU\s*Email|Personal.*Email|GWID)\s*:', s, _re.I):
            continue
        # Skip lines that are mostly *** (redacted noise — more than half the tokens are ***)
        tokens = s.split()
        if tokens and sum(1 for t in tokens if _re.fullmatch(r'\*+[-.\*]*', t)) / len(tokens) > 0.5:
            continue
        # Skip JSON blobs and raw HTTP error lines
        if s.startswith('{') or s.startswith('"error"') or _re.match(r'^\d{3}\s+\*+', s):
            continue
        # Replace inline unredacted emails
        s = _re.sub(r'[\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[email]', s)
        # Replace inline phone numbers
        s = _re.sub(r'\b[\d]{3}[-.\s][\d]{3}[-.\s][\d]{4}\b', '[phone]', s)
        # Replace standalone G-numbers (GW IDs like G12345678)
        s = _re.sub(r'\bG\d{6,}\b', '[gwid]', s)
        clean.append(s)
    return ' '.join(clean).strip()


def _summarize_clusters(engine, run_id: str, client: AzureOpenAI) -> int:
    """Generate a 2-3 sentence plain-English summary for every cluster in this run.

    Samples up to SUMMARY_SAMPLE_SIZE tickets per cluster, calls GPT-4.1,
    and writes the result back to clusters.summary.
    Returns the number of summaries successfully generated.
    """
    import time as _time

    log.info("Summarising clusters with GPT-4.1...")

    # Load all clusters for this run
    with engine.connect() as conn:
        rows = conn.execute(_text("""
            SELECT cluster_id, cluster_label, size, gap_flag, top_terms
            FROM   clusters
            WHERE  run_id = :rid
            ORDER  BY cluster_id
        """), {"rid": run_id}).fetchall()

    n_ok = 0
    for (cluster_id, cluster_label, size, gap_flag, top_terms) in rows:
        # ── Sample up to SUMMARY_SAMPLE_SIZE ticket descriptions ──────────────
        try:
            with engine.connect() as conn:
                ticket_rows = conn.execute(_text("""
                    SELECT COALESCE(ip.detailed_description, ip.description,
                                   wp.detailed_description, wp.description, '') AS txt
                    FROM   cluster_assignments ca
                    LEFT JOIN incidents_processed  ip ON ca.source = 'incident'  AND ca.source_id = ip.id
                    LEFT JOIN workorders_processed wp ON ca.source = 'workorder' AND ca.source_id = wp.id
                    WHERE  ca.run_id     = :rid
                      AND  ca.cluster_id = :cid
                      AND  LENGTH(COALESCE(ip.detailed_description, ip.description,
                                          wp.detailed_description, wp.description, '')) > 20
                    ORDER  BY RANDOM()
                    LIMIT  :lim
                """), {"rid": run_id, "cid": int(cluster_id), "lim": SUMMARY_SAMPLE_SIZE}).fetchall()
        except Exception as e:
            log.warning(f"  [cluster {cluster_id}] ticket sample failed: {e}")
            continue

        snippets = [_sanitize_ticket_text(r[0])[:300] for r in ticket_rows if r[0].strip()]
        snippets = [s for s in snippets if len(s) > 20]
        if not snippets:
            log.warning(f"  [cluster {cluster_id}] no ticket content to summarise — skipping")
            continue

        def _build_prompt(snips):
            numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(snips))
            return (
                f"You are a senior IT knowledge manager at George Washington University analyzing a cluster of similar IT support tickets.\n"
                f"The following {len(snips)} tickets have been grouped together because they describe similar IT problems.\n"
                f"Total tickets in this cluster: {size}\n"
                f"Gap status: {gap_flag} (CRITICAL=no KB article covers this, PARTIAL=partially covered, COVERED=existing KB article exists)\n"
                f"Top terms seen across tickets: {top_terms}\n\n"
                f"Ticket descriptions (sample):\n{numbered}\n\n"
                f"Provide a detailed analysis of this cluster using EXACTLY the following 5 labeled sections. "
                f"Each section should be 1-3 sentences. Be specific — name the actual systems, tools, departments, and error types seen in the tickets.\n\n"
                f"**Problem Type:** What category of IT issue is this? (e.g. account access, hardware, software install, network, etc.)\n"
                f"**Affected Users:** Who submits these tickets? (students, faculty, staff, specific school/department, alumni, etc.)\n"
                f"**Systems & Tools Involved:** Which specific GW systems, software, or tools are mentioned? (e.g. GWeb, Banner, NetID, Zoom, VPN, etc.)\n"
                f"**Common Failure Patterns:** What are the recurring triggers or failure modes seen across these tickets?\n"
                f"**Typical Resolution:** How is this type of issue usually resolved by IT staff?\n\n"
                f"Do NOT include any preamble, ticket numbers, cluster IDs, or closing remarks. Output only the 5 labeled sections."
            )

        # ── GPT-4.1 call with retry (resample on content filter) ─────────────
        import random as _random
        summary = None
        current_snippets = snippets
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=AZURE_DEPLOYMENT,
                    messages=[{"role": "user", "content": _build_prompt(current_snippets)}],
                    max_tokens=500,
                    temperature=0.3,
                )
                summary = resp.choices[0].message.content.strip()
                break
            except Exception as exc:
                is_cf = "content_filter" in str(exc) or "ResponsibleAIPolicyViolation" in str(exc)
                if is_cf and len(current_snippets) > 5:
                    new_lim = max(5, len(current_snippets) // 2)
                    current_snippets = _random.sample(current_snippets, new_lim)
                    log.warning(f"  [cluster {cluster_id}] content filter — resampling {new_lim} tickets (attempt {attempt+1})")
                elif attempt < 2:
                    wait = 2 ** attempt
                    log.warning(f"  [cluster {cluster_id}] attempt {attempt+1} failed: {exc} — retry in {wait}s")
                    _time.sleep(wait)
                else:
                    log.error(f"  [cluster {cluster_id}] summarisation failed after 3 attempts: {exc}")

        if not summary:
            continue

        # ── Write back to clusters.summary ────────────────────────────────────
        try:
            with engine.connect() as conn:
                conn.execute(_text("""
                    UPDATE clusters SET summary = :s
                    WHERE  run_id = :rid AND cluster_id = :cid
                """), {"s": summary, "rid": run_id, "cid": int(cluster_id)})
                conn.commit()
            n_ok += 1
        except Exception as e:
            log.warning(f"  [cluster {cluster_id}] DB write failed: {e}")

        _time.sleep(0.3)  # gentle rate-limit buffer

        # Progress log every 10 clusters
        if (n_ok % 10 == 0 and n_ok > 0) or n_ok == len(rows):
            log.info(f"  [summarise] {n_ok}/{len(rows)} clusters done...")

    log.info(f"  Cluster summaries written: {n_ok}/{len(rows)} ✅")
    return n_ok


def run(engine, run_id: str) -> dict:
    t_start = time.time()
    log.info("── PHASE 4: KB Gap Analysis ──")

    # ── Azure OpenAI client (for cluster summarisation) ────────────────────────
    azure_client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VER,
    )
    log.info(f"Azure OpenAI client ready — deployment={AZURE_DEPLOYMENT}")

    # ── 1. Load from DB ────────────────────────────────────────────────────────
    cluster_sum, centroids = _load_clusters_from_db(engine, run_id)
    kb_emb, kb_meta        = _load_kb_embeddings(engine)

    K    = len(centroids)
    N_KB = len(kb_emb)
    log.info(f"  Clusters: {K}  KB articles: {N_KB}")

    X_c  = normalize(centroids, norm="l2").astype(np.float32)
    X_kb = normalize(kb_emb,   norm="l2").astype(np.float32)

    # ── 2. Full similarity matrix (K × N_KB) ──────────────────────────────────
    log.info(f"Computing similarity matrix ({K} × {N_KB})...")
    sim = cosine_similarity(X_c, X_kb).astype(np.float32)
    log.info(f"  Range: [{sim.min():.4f}, {sim.max():.4f}]  mean={sim.mean():.4f}")

    # ── 3. Multi-signal coverage scoring ──────────────────────────────────────
    max_kb_sim   = sim.max(axis=1)
    best_kb_idx  = sim.argmax(axis=1)
    top3_sorted  = np.sort(sim, axis=1)[:, ::-1][:, :3]
    avg_top3_sim = top3_sorted.mean(axis=1)
    p75_global   = float(np.percentile(sim, 75))
    n_above_p75  = (sim > p75_global).sum(axis=1).astype(float)
    breadth_norm = n_above_p75 / N_KB
    coverage_score = 0.5 * max_kb_sim + 0.3 * avg_top3_sim + 0.2 * breadth_norm
    log.info(f"  coverage_score range: [{coverage_score.min():.4f}, {coverage_score.max():.4f}]")

    # ── 4. Gap classification — absolute calibrated thresholds ───────────────
    def classify_gap(s):
        if s >= SIM_COVERED: return "COVERED"
        if s >= SIM_PARTIAL:  return "PARTIAL"
        return "CRITICAL"

    gap_flag = np.array([classify_gap(s) for s in max_kb_sim])
    counts   = Counter(gap_flag)
    log.info(f"  Thresholds: COVERED>={SIM_COVERED}  PARTIAL>={SIM_PARTIAL}  else=CRITICAL")
    log.info(f"  CRITICAL={counts['CRITICAL']}  PARTIAL={counts['PARTIAL']}  COVERED={counts['COVERED']}")

    # ── 5. Evidence quality per cluster ───────────────────────────────────────
    cluster_ids_list = cluster_sum["cluster_id"].tolist()
    ev_quality = _load_evidence_quality(engine, run_id, cluster_ids_list)
    ev_quality_arr = np.array([ev_quality.get(int(cid), 0.0) for cid in cluster_ids_list])
    ev_quality_norm = MinMaxScaler().fit_transform(
        ev_quality_arr.reshape(-1, 1)).flatten() if ev_quality_arr.max() > 0 else ev_quality_arr

    # ── 6. Multi-signal priority score ────────────────────────────────────────
    sizes          = cluster_sum["size"].values.astype(float)
    cov_scaled     = MinMaxScaler().fit_transform(coverage_score.reshape(-1, 1)).flatten()
    gap_score_arr  = 1.0 - cov_scaled
    volume_norm    = MinMaxScaler().fit_transform(sizes.reshape(-1, 1)).flatten()

    # Recency: tickets in last 90 days (best-effort; skip if column missing)
    recency_norm = np.zeros(K, dtype=np.float32)
    try:
        recency_df = pd.read_sql("""
            SELECT ca.cluster_id,
                   COUNT(*) FILTER (WHERE ip.reported_date >= NOW() - INTERVAL '90 days') AS recent_count
            FROM   cluster_assignments ca
            JOIN   incidents_processed ip ON ca.source_id::text = ip.id::text
            WHERE  ca.run_id = %(r)s AND ca.source = 'incident'
            GROUP  BY ca.cluster_id
        """, engine, params={"r": run_id})
        recency_map = dict(zip(recency_df["cluster_id"], recency_df["recent_count"]))
        recency_arr = np.array([float(recency_map.get(int(cid), 0)) for cid in cluster_ids_list])
        if recency_arr.max() > 0:
            recency_norm = MinMaxScaler().fit_transform(recency_arr.reshape(-1, 1)).flatten()
    except Exception as e:
        log.debug(f"  Recency signal skipped: {e}")

    priority_raw  = (0.35 * volume_norm + 0.25 * recency_norm +
                     0.25 * gap_score_arr + 0.15 * ev_quality_norm)
    priority_norm = priority_raw / max(priority_raw.max(), 1e-9)

    # ── 7. LLM-based duplicate detection ──────────────────────────────────────
    # Load cluster summaries to enrich GPT context
    summary_map = {}
    try:
        sum_df = pd.read_sql(
            "SELECT cluster_id, summary FROM clusters WHERE run_id = %(r)s",
            engine, params={"r": run_id}
        )
        summary_map = dict(zip(sum_df["cluster_id"].astype(int), sum_df["summary"].fillna("")))
    except Exception as _e:
        log.warning(f"  Could not load cluster summaries for dedup: {_e}")

    summary_for_dedup = [
        {"cluster_id":    int(cluster_sum.iloc[i]["cluster_id"]),
         "cluster_label": str(cluster_sum.iloc[i]["cluster_label"]),
         "size":          int(cluster_sum.iloc[i]["size"]),
         "top_terms":     str(cluster_sum.iloc[i].get("top_terms", "")),
         "max_kb_sim":    round(float(max_kb_sim[i]), 4),
         "gap_flag":      gap_flag[i],
         "summary":       summary_map.get(int(cluster_sum.iloc[i]["cluster_id"]), "")}
        for i in range(K)
    ]
    summary_for_dedup = _llm_deduplicate_clusters(
        X_c, summary_for_dedup, azure_client, AZURE_DEPLOYMENT
    )
    merged_set     = {r["cluster_id"] for r in summary_for_dedup if "merged_into" in r}
    dup_reason_map = {
        r["cluster_id"]: r.get("llm_dup_reason", "")
        for r in summary_for_dedup if "merged_into" in r
    }

    # ── 8. Build gap_report dataframe ─────────────────────────────────────────
    def make_recommendation(flag, label, best_kb_title):
        if flag == "CRITICAL":
            return f"GENERATE: Create new KB article — '{label[:40]}'"
        if flag == "PARTIAL":
            return f"UPDATE: Expand '{best_kb_title[:45]}' to cover '{label[:40]}'"
        return f"MONITOR: '{best_kb_title[:45]}' adequately covers this cluster"

    gap_rows = []
    for cid in range(K):
        row       = cluster_sum.iloc[cid]
        actual_cid = int(row["cluster_id"])
        bkb_idx   = int(best_kb_idx[cid])
        bkb_title = kb_meta["title"].iloc[bkb_idx] if bkb_idx < len(kb_meta) else ""
        is_merged  = actual_cid in merged_set
        merged_into_id = next(
            (r["merged_into"] for r in summary_for_dedup
             if r["cluster_id"] == actual_cid and "merged_into" in r),
            None
        )
        dup_reason = dup_reason_map.get(actual_cid, "")
        flag       = gap_flag[cid]
        rec = (
            f"DUPLICATE: Confirmed by GPT-4.1 — same problem as cluster {merged_into_id}. {dup_reason}"
            if is_merged else make_recommendation(flag, str(row["cluster_label"]), bkb_title)
        )
        gap_rows.append({
            "cluster_id":    actual_cid,
            "cluster_label": str(row["cluster_label"]),
            "size":          int(row["size"]),
            "top_terms":     str(row.get("top_terms", "")),
            "max_kb_sim":    round(float(max_kb_sim[cid]), 4),
            "avg_top3_sim":  round(float(avg_top3_sim[cid]), 4),
            "n_above_p75":   int(n_above_p75[cid]),
            "coverage_score": round(float(coverage_score[cid]), 4),
            "priority_score": round(float(priority_norm[cid]), 4),
            "gap_flag":      "DUPLICATE" if is_merged else flag,
            "recommendation": rec,
            "best_kb_title": bkb_title,
            "best_kb_idx":   bkb_idx,
        })
    gap_df     = pd.DataFrame(gap_rows)
    all_counts = Counter(gap_df["gap_flag"])
    log.info(f"  Final flags: {dict(all_counts)}")

    # ── 9. KB utilization analysis ────────────────────────────────────────────
    log.info("Analysing KB article utilization...")
    kb_as_best   = pd.Series(best_kb_idx).value_counts()
    kb_max_sim   = sim.max(axis=0)
    kb_mean_sim  = sim.mean(axis=0)
    kb_breadth   = (sim > p75_global).sum(axis=0)

    def kb_status(row):
        if row["clusters_as_best"] >= 5:                                      return "OVER-RELIED"
        if row["clusters_as_best"] == 0 and row["max_cluster_sim"] < SIM_PARTIAL: return "ORPHAN"
        if row["clusters_as_best"] > 0:                                       return "ACTIVE"
        return "PERIPHERAL"

    kb_util_df = pd.DataFrame({
        "kb_idx":           range(N_KB),
        "title":            kb_meta["title"].values,
        "clusters_as_best": [int(kb_as_best.get(i, 0)) for i in range(N_KB)],
        "max_cluster_sim":  kb_max_sim.round(4),
        "mean_cluster_sim": kb_mean_sim.round(4),
        "breadth_count":    kb_breadth.astype(int),
    })
    kb_util_df["status"] = kb_util_df.apply(kb_status, axis=1)
    kb_util_df = kb_util_df.sort_values("clusters_as_best", ascending=False)
    log.info(f"  KB status: {kb_util_df['status'].value_counts().to_dict()}")

    # ── 8. Write to DB ─────────────────────────────────────────────────────────
    log.info("Writing gap_analysis → DB...")
    _write_gap_analysis_to_db(engine, run_id, gap_df)
    log.info("Writing kb_utilization → DB...")
    _write_kb_utilization_to_db(engine, run_id, kb_util_df, kb_meta)
    log.info("Seeding kb_search_index with existing KB articles...")
    _seed_kb_search_index(engine, run_id, kb_meta, X_kb)

    # ── 9. Physical merge of DUPLICATE clusters ────────────────────────────────
    if merged_set:
        log.info(f"Physically merging {len(merged_set)} duplicate cluster(s)...")
        merged_into_dict = {
            r["cluster_id"]: r["merged_into"]
            for r in summary_for_dedup if "merged_into" in r
        }
        _merge_duplicate_clusters(engine, run_id, merged_into_dict)

    # ── 10. Cluster summarisation (GPT-4.1) ────────────────────────────────────
    log.info("Generating cluster summaries...")
    n_summaries = _summarize_clusters(engine, run_id, azure_client)

    elapsed = time.time() - t_start
    log.info(f"✅ Phase 4 complete in {elapsed/60:.1f}m")
    return {"gap_counts": dict(all_counts),
            "critical_count": all_counts["CRITICAL"],
            "summaries_generated": n_summaries,
            "elapsed_s": round(elapsed, 1)}


def _write_gap_analysis_to_db(engine, run_id, gap_df):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    rows = [
        (str(uuid.uuid4()), int(r["cluster_id"]), run_id,
         str(r["cluster_label"]), int(r["size"]),
         float(r["max_kb_sim"]), float(r["avg_top3_sim"]),
         float(r["n_above_p75"]), float(r["coverage_score"]),
         float(r["priority_score"]), str(r["gap_flag"]),
         str(r["recommendation"]), str(r["best_kb_title"]),
         int(r["best_kb_idx"]), str(r.get("top_terms", "")))
        for _, r in gap_df.iterrows()
    ]
    execute_values(cur, """
        INSERT INTO gap_analysis
            (id, cluster_id, run_id, cluster_label, size, max_kb_sim, avg_top3_sim,
             n_above_p75, coverage_score, priority_score, gap_flag,
             recommendation, best_kb_title, best_kb_idx, top_terms)
        VALUES %s
        ON CONFLICT (cluster_id, run_id) DO UPDATE
            SET cluster_label   = EXCLUDED.cluster_label,
                size            = EXCLUDED.size,
                max_kb_sim      = EXCLUDED.max_kb_sim,
                avg_top3_sim    = EXCLUDED.avg_top3_sim,
                n_above_p75     = EXCLUDED.n_above_p75,
                coverage_score  = EXCLUDED.coverage_score,
                priority_score  = EXCLUDED.priority_score,
                gap_flag        = EXCLUDED.gap_flag,
                recommendation  = EXCLUDED.recommendation,
                best_kb_title   = EXCLUDED.best_kb_title,
                best_kb_idx     = EXCLUDED.best_kb_idx,
                top_terms       = EXCLUDED.top_terms
    """, rows, page_size=200)
    conn.commit(); cur.close(); conn.close()


def _write_kb_utilization_to_db(engine, run_id, kb_util_df, kb_meta):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    rows = [
        (str(uuid.uuid4()), int(r["kb_idx"]), run_id,
         str(kb_meta["id"].iloc[min(int(r["kb_idx"]), len(kb_meta)-1)]),
         str(r["title"]), int(r["clusters_as_best"]),
         float(r["max_cluster_sim"]), float(r["mean_cluster_sim"]),
         int(r["breadth_count"]), str(r["status"]))
        for _, r in kb_util_df.iterrows()
    ]
    execute_values(cur, """
        INSERT INTO kb_utilization
            (id, kb_idx, run_id, kb_article_id, title, clusters_as_best,
             max_cluster_sim, mean_cluster_sim, breadth_count, status)
        VALUES %s
        ON CONFLICT (kb_idx, run_id) DO UPDATE
            SET title            = EXCLUDED.title,
                clusters_as_best = EXCLUDED.clusters_as_best,
                max_cluster_sim  = EXCLUDED.max_cluster_sim,
                mean_cluster_sim = EXCLUDED.mean_cluster_sim,
                breadth_count    = EXCLUDED.breadth_count,
                status           = EXCLUDED.status
    """, rows, page_size=400)
    conn.commit(); cur.close(); conn.close()


def _seed_kb_search_index(engine, run_id, kb_meta, X_kb):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    rows = []
    for i, row in kb_meta.iterrows():
        content = f"{row.get('title','')} {row.get('issue','')} {row.get('solution','')}".strip()[:500]
        rows.append((
            str(uuid.uuid4()), run_id,
            "existing", str(row.get("id", str(i))),
            str(row.get("title", "")), None, content,
            X_kb[i].tolist() if i < len(X_kb) else None,
            False,
        ))
    execute_values(cur, """
        INSERT INTO kb_search_index
            (entry_id, run_id, source, source_id, title, category, content, embedding, is_generated)
        VALUES %s
        ON CONFLICT (source, source_id, run_id) DO UPDATE
            SET embedding=EXCLUDED.embedding, title=EXCLUDED.title
    """, rows, page_size=400)
    conn.commit()
    log.info(f"  kb_search_index seeded with {len(rows)} existing KB articles.")
    cur.close(); conn.close()
