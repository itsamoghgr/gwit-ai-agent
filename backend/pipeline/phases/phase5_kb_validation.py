"""
phase4_1_kb_validation.py — Phase 4.1: LLM KB Coverage Validation

For every CRITICAL cluster (max_kb_sim < 0.87, i.e. no KB article covers it via
vector similarity), ask GPT-4.1 to semantically validate the top-10 KB candidates
and determine whether any of them actually addresses the cluster's issue.

Why this exists:
  Vector cosine similarity can miss true matches when terminology differs
  (e.g. "VCL environment" vs "Virtual Computer Lab Access"). The GPT-4.1
  cluster summaries from Phase 4 are rich structured descriptions — ideal
  inputs for a second-opinion LLM judge.

Design:
  1. Load CRITICAL clusters that have an existing summary (written by Phase 4)
  2. For each cluster: retrieve its top-10 KB candidates by vector cosine sim
     (already stored in cluster_kb_sim from Phase 3)
  3. Send summary + top-10 KB titles & solution snippets → GPT-4.1
  4. Parse structured JSON response
  5. Write llm_kb_match, llm_confidence, llm_kb_reasoning back to clusters table
  6. If confidence = HIGH: override gap_flag to 'PARTIAL' in both clusters and
     gap_analysis tables, suppressing redundant KB article generation in Phase 5

Reads from:  clusters, cluster_kb_sim, knowledge_base_articles
Writes to:   clusters (llm_kb_match, llm_confidence, llm_kb_reasoning),
             gap_analysis (gap_flag, recommendation override)

Run standalone:
    python3 run_pipeline.py --phase 4.1
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from openai import AzureOpenAI
from sklearn.preprocessing import normalize
from sqlalchemy import text as _text

from pipeline.embedding_utils import parse_jsonb_vector, combine_embeddings
from core.pipeline_logger import get_phase_logger as get_logger

log = get_logger(__name__)

# ── Azure OpenAI config ────────────────────────────────────────────────────────
AZURE_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY    = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VER    = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

# ── Tuneable constants ─────────────────────────────────────────────────────────
TOP_K_KB_CANDIDATES  = 10     # KB candidates per cluster sent to the LLM
KB_SOLUTION_SNIPPET  = 350    # chars of solution text to include per candidate
RETRY_ATTEMPTS       = 3
RATE_LIMIT_SLEEP     = 0.4    # seconds between API calls

# Gap flag override: HIGH-confidence LLM match → downgrade CRITICAL → PARTIAL
OVERRIDE_FLAG_ON_HIGH = True


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_critical_clusters(engine, run_id: str) -> pd.DataFrame:
    """Return CRITICAL clusters that have a GPT-4.1 summary from Phase 4."""
    log.info("Loading CRITICAL clusters with summaries...")
    with engine.connect() as conn:
        rows = conn.execute(_text("""
            SELECT cluster_id, cluster_label, size, top_terms, summary,
                   max_kb_sim, gap_flag
            FROM   clusters
            WHERE  run_id    = :rid
              AND  gap_flag  = 'CRITICAL'
              AND  summary   IS NOT NULL
              AND  summary   != ''
            ORDER  BY size DESC
        """), {"rid": run_id}).fetchall()

    df = pd.DataFrame(rows, columns=[
        "cluster_id", "cluster_label", "size", "top_terms",
        "summary", "max_kb_sim", "gap_flag",
    ])
    log.info(f"  Found {len(df)} CRITICAL clusters with summaries")
    return df


def _load_kb_articles(engine) -> pd.DataFrame:
    """Load all KB articles with embeddings for candidate retrieval."""
    log.info("Loading KB articles...")
    df = pd.read_sql(
        "SELECT id, title, issue, solution, e_title, e_solution "
        "FROM knowledge_base_articles",
        engine,
    )
    log.info(f"  Loaded {len(df)} KB articles")
    return df


def _build_kb_matrix(df_kb: pd.DataFrame):
    """Build normalized embedding matrix from KB articles. Returns (matrix, valid_mask)."""
    DIM = 1536
    n   = len(df_kb)
    mat = np.zeros((n, DIM), dtype=np.float32)
    valid = np.zeros(n, dtype=bool)
    for i, (_, row) in enumerate(df_kb.iterrows()):
        vec = combine_embeddings(
            parse_jsonb_vector(row["e_title"]),
            parse_jsonb_vector(row["e_solution"]),
            0.5, 0.5,
        )
        if vec is not None:
            mat[i]   = vec
            valid[i] = True
    kb_norm = normalize(mat[valid], norm="l2").astype(np.float32)
    df_valid = df_kb[valid].reset_index(drop=True)
    log.info(f"  KB embedding matrix: {kb_norm.shape}")
    return kb_norm, df_valid


def _load_cluster_centroid(engine, run_id: str, cluster_id: int) -> Optional[np.ndarray]:
    """Fetch and normalize the centroid vector for a single cluster."""
    with engine.connect() as conn:
        row = conn.execute(_text("""
            SELECT centroid FROM clusters
            WHERE run_id = :rid AND cluster_id = :cid
        """), {"rid": run_id, "cid": cluster_id}).fetchone()
    if row is None or row[0] is None:
        return None
    # centroid comes back from DB as a JSON string — parse it first
    parsed = parse_jsonb_vector(row[0]) if isinstance(row[0], str) else row[0]
    if parsed is None:
        return None
    vec = np.asarray(parsed, dtype=np.float32).reshape(1, -1)
    return normalize(vec, norm="l2")[0]


# ─────────────────────────────────────────────────────────────────────────────
# Top-K KB candidate retrieval
# ─────────────────────────────────────────────────────────────────────────────

def _get_top_k_candidates(centroid: np.ndarray, kb_norm: np.ndarray,
                           df_kb: pd.DataFrame, k: int = TOP_K_KB_CANDIDATES) -> list[dict]:
    """Return top-k KB articles by cosine similarity to the cluster centroid."""
    sims   = kb_norm @ centroid          # (n_kb,)
    top_idx = np.argsort(sims)[::-1][:k]
    candidates = []
    for idx in top_idx:
        row = df_kb.iloc[idx]
        solution_text = str(row.get("solution", "") or "").strip()
        candidates.append({
            "kb_id":    str(row["id"]),
            "title":    str(row["title"]),
            "snippet":  solution_text[:KB_SOLUTION_SNIPPET],
            "sim":      round(float(sims[idx]), 4),
        })
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# LLM prompt + call
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt(cluster_label: str, cluster_summary: str,
                  top_terms: str, cluster_size: int,
                  candidates: list[dict]) -> str:
    cand_block = ""
    for i, c in enumerate(candidates, 1):
        snippet = c["snippet"].replace("\n", " ").strip()
        cand_block += (
            f"\n{i}. Title: {c['title']}\n"
            f"   Vector similarity: {c['sim']}\n"
            f"   Content: {snippet or '(no content)'}\n"
        )

    return (
        "You are a knowledge base curator at George Washington University's IT help desk.\n\n"
        "A cluster of similar IT support tickets has been automatically summarized below. "
        "The vector embedding search says NO existing KB article covers this cluster — "
        "but vector similarity can miss matches when terminology differs. "
        "Your job: read the cluster summary carefully, then check each candidate KB article "
        "to see if any of them ACTUALLY addresses the core issue described.\n\n"
        "=== CLUSTER INFO ===\n"
        f"Label: {cluster_label}\n"
        f"Top terms: {top_terms}\n"
        f"Ticket volume: {cluster_size}\n\n"
        "=== CLUSTER SUMMARY (GPT-generated from real tickets) ===\n"
        f"{cluster_summary}\n\n"
        f"=== TOP {len(candidates)} CANDIDATE KB ARTICLES (by vector similarity) ===\n"
        f"{cand_block}\n"
        "=== TASK ===\n"
        "Does any candidate KB article already cover the core IT issue described in this cluster?\n"
        "A match means a user or IT staff could USE that article to resolve or understand the issue.\n"
        "Do NOT match on superficial keyword overlap — only match if the article genuinely addresses "
        "the same problem type and resolution path.\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation outside JSON):\n"
        "{\n"
        '  "match_found": true or false,\n'
        '  "matched_kb_id": "the kb_id string if match_found else null",\n'
        '  "matched_kb_title": "exact title if match_found else null",\n'
        '  "confidence": "HIGH, MEDIUM, or LOW",\n'
        '  "reasoning": "one concise sentence explaining your decision"\n'
        "}"
    )


def _call_llm(client: AzureOpenAI, prompt: str, cluster_id: int) -> Optional[dict]:
    """Call GPT-4.1 with retry. Returns parsed JSON dict or None on failure."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.chat.completions.create(
                model=AZURE_DEPLOYMENT,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content.strip()
            parsed = json.loads(raw)
            # Validate required keys
            required = {"match_found", "matched_kb_id", "matched_kb_title",
                        "confidence", "reasoning"}
            if not required.issubset(parsed.keys()):
                log.warning(f"  [cluster {cluster_id}] LLM response missing keys: {parsed.keys()}")
                return None
            return parsed
        except json.JSONDecodeError as e:
            log.warning(f"  [cluster {cluster_id}] JSON parse error attempt {attempt+1}: {e}")
        except Exception as exc:
            wait = 2 ** attempt
            if attempt < RETRY_ATTEMPTS - 1:
                log.warning(f"  [cluster {cluster_id}] API error attempt {attempt+1}: {exc} — retry in {wait}s")
                time.sleep(wait)
            else:
                log.error(f"  [cluster {cluster_id}] LLM call failed after {RETRY_ATTEMPTS} attempts: {exc}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DB write helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_llm_result(engine, run_id: str, cluster_id: int, result: dict) -> None:
    """Write LLM validation results to clusters table."""
    with engine.connect() as conn:
        conn.execute(_text("""
            UPDATE clusters
            SET    llm_kb_match      = :match_title,
                   llm_confidence    = :confidence,
                   llm_kb_reasoning  = :reasoning
            WHERE  run_id = :rid AND cluster_id = :cid
        """), {
            "match_title": result.get("matched_kb_title"),
            "confidence":  result.get("confidence"),
            "reasoning":   result.get("reasoning"),
            "rid":         run_id,
            "cid":         cluster_id,
        })
        conn.commit()


def _override_gap_flag(engine, run_id: str, cluster_id: int,
                       matched_kb_title: str) -> None:
    """Downgrade gap_flag CRITICAL → PARTIAL in both clusters and gap_analysis."""
    new_flag = "PARTIAL"
    new_rec  = f"UPDATE: Expand '{matched_kb_title[:50]}' to cover this cluster (LLM-validated)"
    with engine.connect() as conn:
        conn.execute(_text("""
            UPDATE clusters
            SET    gap_flag = :flag
            WHERE  run_id = :rid AND cluster_id = :cid AND gap_flag = 'CRITICAL'
        """), {"flag": new_flag, "rid": run_id, "cid": cluster_id})

        conn.execute(_text("""
            UPDATE gap_analysis
            SET    gap_flag = :flag, recommendation = :rec
            WHERE  run_id = :rid AND cluster_id = :cid AND gap_flag = 'CRITICAL'
        """), {"flag": new_flag, "rec": new_rec, "rid": run_id, "cid": cluster_id})
        conn.commit()

    log.info(f"    ↳ gap_flag overridden: CRITICAL → PARTIAL (KB: '{matched_kb_title[:60]}')")


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(engine, run_id: str) -> dict:
    t_start = time.time()
    log.info("── PHASE 4.1: LLM KB Coverage Validation ──")

    # ── Azure OpenAI client ────────────────────────────────────────────────────
    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VER,
    )
    log.info(f"Azure OpenAI client ready — deployment={AZURE_DEPLOYMENT}")

    # ── Load data ──────────────────────────────────────────────────────────────
    df_clusters = _load_critical_clusters(engine, run_id)
    if df_clusters.empty:
        log.warning("No CRITICAL clusters with summaries found — nothing to validate.")
        return {"validated": 0, "matches_found": 0, "overrides": 0, "elapsed_s": 0}

    df_kb            = _load_kb_articles(engine)
    kb_norm, df_kb_v = _build_kb_matrix(df_kb)

    # ── Per-cluster LLM validation ─────────────────────────────────────────────
    n_validated  = 0
    n_matches    = 0
    n_overrides  = 0
    n_high       = 0
    n_medium     = 0
    n_low        = 0
    n_no_match   = 0

    total = len(df_clusters)
    for i, row in df_clusters.iterrows():
        cluster_id = int(row["cluster_id"])
        log.info(f"  [{i+1}/{total}] Cluster {cluster_id}: '{row['cluster_label']}' "
                 f"(size={row['size']}, max_kb_sim={row['max_kb_sim']:.3f})")

        # 1. Fetch centroid
        centroid = _load_cluster_centroid(engine, run_id, cluster_id)
        if centroid is None:
            log.warning(f"    No centroid found for cluster {cluster_id} — skipping")
            continue

        # 2. Get top-K KB candidates by vector sim
        candidates = _get_top_k_candidates(centroid, kb_norm, df_kb_v)
        if not candidates:
            log.warning(f"    No KB candidates — skipping")
            continue

        log.info(f"    Top candidate: '{candidates[0]['title']}' (sim={candidates[0]['sim']:.3f})")

        # 3. Build prompt & call GPT-4.1
        prompt = _build_prompt(
            cluster_label=str(row["cluster_label"]),
            cluster_summary=str(row["summary"]),
            top_terms=str(row["top_terms"] or ""),
            cluster_size=int(row["size"]),
            candidates=candidates,
        )
        result = _call_llm(client, prompt, cluster_id)
        if result is None:
            log.warning(f"    LLM call failed — skipping cluster {cluster_id}")
            continue

        n_validated += 1
        match_found  = result.get("match_found", False)
        confidence   = result.get("confidence", "LOW").upper()
        kb_title     = result.get("matched_kb_title") or ""
        reasoning    = result.get("reasoning", "")

        log.info(f"    → match_found={match_found}  confidence={confidence}")
        log.info(f"    → matched_kb: '{kb_title[:60]}'")
        log.info(f"    → reasoning: {reasoning[:120]}")

        # 4. Write to DB
        _write_llm_result(engine, run_id, cluster_id, result)

        # 5. Stats
        if match_found:
            n_matches += 1
            if confidence == "HIGH":
                n_high += 1
            elif confidence == "MEDIUM":
                n_medium += 1
            else:
                n_low += 1
        else:
            n_no_match += 1

        # 6. Override gap_flag if HIGH confidence match
        if match_found and confidence == "HIGH" and OVERRIDE_FLAG_ON_HIGH and kb_title:
            _override_gap_flag(engine, run_id, cluster_id, kb_title)
            n_overrides += 1

        time.sleep(RATE_LIMIT_SLEEP)

        # Progress log every 10 clusters
        if (n_validated % 10 == 0):
            log.info(f"  Progress: {n_validated}/{total} validated, "
                     f"{n_matches} matches ({n_high} HIGH / {n_medium} MEDIUM / {n_low} LOW)")

    # ── Final summary ──────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    log.info(f"\n{'─'*55}")
    log.info(f"  Phase 4.1 complete in {elapsed/60:.1f}m")
    log.info(f"  Clusters validated : {n_validated}/{total}")
    log.info(f"  Matches found      : {n_matches} ({n_high} HIGH / {n_medium} MEDIUM / {n_low} LOW)")
    log.info(f"  No match           : {n_no_match}")
    log.info(f"  Gap flag overrides : {n_overrides}  (CRITICAL → PARTIAL)")
    log.info(f"{'─'*55}")

    return {
        "validated":    n_validated,
        "matches_found": n_matches,
        "high_confidence": n_high,
        "medium_confidence": n_medium,
        "low_confidence": n_low,
        "no_match": n_no_match,
        "overrides": n_overrides,
        "elapsed_s": round(elapsed, 1),
    }
