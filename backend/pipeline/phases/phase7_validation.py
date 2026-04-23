"""
phase7_validation.py — Phase 7: KB Article Validation

For each generated KB article, validates that it:
  1. Genuinely answers the problems described in the cluster's ticket evidence (LLM critique)
  2. Is distinct from existing KB articles (gap novelty check)
  3. Is semantically on-topic for the cluster it was built for (cluster alignment check)

Reads from:  generated_kb_articles, kb_search_index, clusters,
             cluster_assignments, incidents_processed, workorders_processed
Writes to:   kb_validation_results, evaluation_results (category='validation'),
             updates generated_kb_articles.needs_review
"""
import json
import os
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from openai import AzureOpenAI
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values
from sklearn.preprocessing import normalize
from sqlalchemy import text

from core.pipeline_logger import get_phase_logger as get_logger

log = get_logger(__name__)

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY  = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VER  = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
CHAT_MODEL     = os.getenv("AZURE_OPENAI_CHAT_MODEL", "gpt-4.1")

# Reuse same thresholds as phases 3/4/6 for consistency
GAP_NOVELTY_THRESHOLD   = 0.87
CLUSTER_ALIGN_THRESHOLD = 0.75
MAX_EVIDENCE_FOR_CRITIQUE = 3

CRITIQUE_SYSTEM_PROMPT = """You are a senior GW IT knowledge manager validating KB articles.
Your job is to determine whether a generated KB article genuinely addresses the IT support problems
described in real ticket evidence from GW IT helpdesk tickets.
Always return valid JSON matching the schema provided."""


def _get_db_conn(engine):
    url  = engine.url
    conn = psycopg2.connect(
        host=str(url.host), port=url.port or 5432,
        dbname=str(url.database), user=str(url.username),
        password=str(url.password),
    )
    register_vector(conn)
    return conn


def _load_generated_articles(engine, run_id: str) -> pd.DataFrame:
    """Load non-duplicate generated articles that have embeddings."""
    return pd.read_sql("""
        SELECT article_id, cluster_id, gap_flag, title, problem_statement,
               resolution_steps, escalation_trigger, article_embedding
        FROM   generated_kb_articles
        WHERE  run_id = %s
          AND  is_duplicate_of IS NULL
          AND  article_embedding IS NOT NULL
    """, engine, params=(run_id,))


def _load_existing_kb_embeddings(engine, run_id: str):
    """Load existing (non-generated) KB embeddings and titles from kb_search_index."""
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    cur.execute("""
        SELECT title, embedding
        FROM   kb_search_index
        WHERE  run_id = %s AND is_generated = false AND embedding IS NOT NULL
    """, (run_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    titles = [r[0] for r in rows]
    matrix = np.vstack([np.asarray(r[1], dtype=np.float32) for r in rows]) if rows else np.empty((0, 1536))
    return titles, matrix


def _load_cluster_centroids(engine, run_id: str) -> dict:
    """Return {cluster_id: centroid_array} for the run."""
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    cur.execute("SELECT cluster_id, centroid FROM clusters WHERE run_id = %s", (run_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {int(r[0]): np.asarray(r[1], dtype=np.float32) for r in rows if r[1] is not None}


def _load_cluster_evidence(engine, run_id: str, cluster_id: int, n: int = MAX_EVIDENCE_FOR_CRITIQUE) -> list:
    """Load up to n best-evidence ticket pairs for LLM critique (mirrors phase5 pattern)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT problem_text, resolution_text FROM (
                SELECT COALESCE(ip.detailed_description, ip.description, '') AS problem_text,
                       ip.resolution_summary AS resolution_text,
                       COALESCE(te.evidence_tier, 4) AS evidence_tier
                FROM   cluster_assignments ca
                JOIN   incidents_processed ip ON ca.source_id::text = ip.id::text
                LEFT JOIN ticket_embeddings te
                       ON ca.source_id::text = te.source_id::text
                      AND ca.source = te.source AND te.run_id = :r
                WHERE  ca.run_id = :r AND ca.cluster_id = :c AND ca.source = 'incident'
                  AND  ip.resolution_summary IS NOT NULL
                  AND  LENGTH(REPLACE(ip.resolution_summary, '***', '')) > 30

                UNION ALL

                SELECT COALESCE(wp.detailed_description, wp.description, '') AS problem_text,
                       wp.activity_logs_text AS resolution_text,
                       COALESCE(te.evidence_tier, 4) AS evidence_tier
                FROM   cluster_assignments ca
                JOIN   workorders_processed wp ON ca.source_id::text = wp.id::text
                LEFT JOIN ticket_embeddings te
                       ON ca.source_id::text = te.source_id::text
                      AND ca.source = te.source AND te.run_id = :r
                WHERE  ca.run_id = :r AND ca.cluster_id = :c AND ca.source = 'workorder'
                  AND  wp.activity_logs_text IS NOT NULL
                  AND  LENGTH(REPLACE(wp.activity_logs_text, '***', '')) > 30
            ) combined
            ORDER BY evidence_tier ASC, RANDOM()
            LIMIT :n
        """), {"r": run_id, "c": cluster_id, "n": n}).fetchall()
    return [{"problem": r[0] or "", "resolution": r[1] or ""} for r in rows if r[1]]


def _llm_critique(client: AzureOpenAI, article: dict, evidence: list,
                  gap_flag: str = "CRITICAL") -> dict:
    """Call GPT-4.1 to critique whether the article genuinely addresses the ticket evidence.

    For CRITICAL gaps: article must cover genuinely new ground not in existing KB.
    For PARTIAL gaps:  article is a supplement — overlap with existing KB is expected.
                       Only evaluate whether it adds useful content for the specific
                       ticket problems that existing KB did not fully address.

    Returns dict with keys: coverage_score, gap_confirmed, missing_elements, verdict, critique.
    Falls back to a default PARTIAL result if the LLM call fails.
    """
    steps = article.get("resolution_steps", [])
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            steps = [steps]
    steps_text = "\n".join(steps) if isinstance(steps, list) else str(steps)

    article_block = (
        f"Title: {article.get('title', '')}\n"
        f"Problem: {article.get('problem_statement', '')}\n"
        f"Resolution Steps:\n{steps_text}\n"
        f"Escalation: {article.get('escalation_trigger', '')}"
    )

    if evidence:
        ev_items = []
        for i, e in enumerate(evidence, 1):
            ev_items.append(
                f"  <item id=\"{i}\">\n"
                f"    <problem>{e['problem'][:350]}</problem>\n"
                f"    <resolution>{e['resolution'][:500]}</resolution>\n"
                f"  </item>"
            )
        evidence_block = "<ticket_evidence>\n" + "\n".join(ev_items) + "\n</ticket_evidence>"
    else:
        evidence_block = "<ticket_evidence>(no ticket evidence available)</ticket_evidence>"

    if gap_flag == "PARTIAL":
        context_note = (
            "This article is a SUPPLEMENT to an existing KB article — it is expected to "
            "overlap with existing guidance. Evaluate only whether it adds useful, specific "
            "content that addresses the ticket problems the existing KB did not fully cover. "
            "Do NOT penalise it for being related to an existing KB article."
        )
        gap_confirmed_field = (
            '"gap_confirmed": <true if the article adds useful content beyond what a generic '
            'existing KB article would cover for these specific tickets, false only if it is '
            'completely redundant and adds nothing new>,'
        )
        verdict_rule = (
            "VALIDATED if coverage_score >= 7, "
            "PARTIAL if coverage_score 4-6, "
            "INSUFFICIENT if coverage_score < 4"
        )
    else:
        context_note = (
            "This article addresses a CRITICAL gap — no existing KB article covers this topic. "
            "Evaluate whether the article genuinely solves the specific problems in the ticket "
            "evidence and covers genuinely new ground."
        )
        gap_confirmed_field = (
            '"gap_confirmed": <true if the article covers genuinely new ground not in standard '
            'GW IT KB, false if it seems to duplicate obvious existing guidance>,'
        )
        verdict_rule = (
            "VALIDATED if coverage_score >= 7 and gap_confirmed is true, "
            "PARTIAL if coverage_score 4-6 or gap questionable, "
            "INSUFFICIENT if coverage_score < 4"
        )

    prompt = f"""Below is a generated GW IT KB article and real ticket evidence from the support gap it was meant to address.
{context_note}

<generated_article>
{article_block}
</generated_article>

{evidence_block}

Return ONLY valid JSON matching this schema exactly:
{{
  "coverage_score": <integer 0-10, how well the article addresses the ticket problems>,
  {gap_confirmed_field}
  "missing_elements": [<specific things the article fails to address from the ticket evidence>],
  "verdict": "<{verdict_rule}>",
  "critique": "<1-2 sentence summary of the validation result>"
}}"""

    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": CRITIQUE_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=600,
        )
        result = json.loads(resp.choices[0].message.content)
        # Normalise verdict to expected values
        v = str(result.get("verdict", "PARTIAL")).upper()
        if v not in ("VALIDATED", "PARTIAL", "INSUFFICIENT"):
            v = "PARTIAL"
        result["verdict"] = v
        result["coverage_score"] = float(result.get("coverage_score", 5))
        result["gap_confirmed"]  = bool(result.get("gap_confirmed", True))
        result["missing_elements"] = result.get("missing_elements", [])
        result["critique"] = str(result.get("critique", ""))
        return result
    except Exception as e:
        log.warning(f"  LLM critique failed: {e} — using embedding-only fallback")
        return None   # caller handles None as fallback


def _compute_novelty(article_vec: np.ndarray, existing_kb_matrix: np.ndarray,
                     existing_kb_titles: list, gap_flag: str = "CRITICAL") -> tuple:
    """Return (max_existing_kb_sim, closest_kb_title, novelty_pass).

    Threshold differs by gap_flag:
    - CRITICAL: article must be genuinely new content (< 0.87 sim to any existing KB)
    - PARTIAL:  article is a supplement — it is allowed to be close to existing KB.
                Only fail novelty if it is nearly identical (>= 0.97), meaning it adds
                nothing beyond what already exists.
    """
    if existing_kb_matrix.shape[0] == 0:
        return 0.0, "", True
    art_norm  = article_vec / (np.linalg.norm(article_vec) + 1e-9)
    kb_norm   = normalize(existing_kb_matrix, norm="l2")
    sims      = kb_norm @ art_norm
    best_idx  = int(np.argmax(sims))
    max_sim   = float(sims[best_idx])
    threshold = GAP_NOVELTY_THRESHOLD if gap_flag == "CRITICAL" else 0.97
    return max_sim, existing_kb_titles[best_idx], max_sim < threshold


def _compute_alignment(article_vec: np.ndarray, centroid: np.ndarray) -> tuple:
    """Return (cluster_alignment_score, alignment_pass)."""
    art_norm = article_vec / (np.linalg.norm(article_vec) + 1e-9)
    cen_norm = centroid    / (np.linalg.norm(centroid)    + 1e-9)
    score    = float(np.dot(art_norm, cen_norm))
    return score, score >= CLUSTER_ALIGN_THRESHOLD


def _final_verdict(llm_verdict: str, novelty_pass: bool, alignment_pass: bool,
                   gap_flag: str = "CRITICAL") -> str:
    """Combine LLM verdict and embedding checks into a single validation_status.

    For CRITICAL gaps: all three signals must pass for VALIDATED.
    For PARTIAL gaps:  novelty is informational only — supplement articles are
                       expected to overlap with existing KB. Only LLM verdict
                       and cluster alignment determine the final status.
    """
    if llm_verdict == "INSUFFICIENT":
        return "FAIL"

    if gap_flag == "PARTIAL":
        # Novelty not a hard gate for supplement articles
        if llm_verdict == "VALIDATED" and alignment_pass:
            return "VALIDATED"
        if not alignment_pass:
            return "PARTIAL"
        return "PARTIAL" if llm_verdict == "PARTIAL" else "VALIDATED"

    # CRITICAL gap: all signals required
    if not novelty_pass and not alignment_pass:
        return "FAIL"
    if llm_verdict == "VALIDATED" and novelty_pass and alignment_pass:
        return "VALIDATED"
    return "PARTIAL"


def _write_validation_results(engine, run_id: str, results: list):
    if not results:
        return
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    cur.execute("DELETE FROM kb_validation_results WHERE run_id = %s", (run_id,))
    rows = []
    for r in results:
        rows.append((
            str(uuid.uuid4()), run_id,
            str(r["article_id"]),
            int(r["cluster_id"]),
            str(r.get("gap_flag", "")),
            r.get("llm_coverage_score"),
            r.get("llm_gap_confirmed"),
            r.get("llm_verdict"),
            r.get("llm_critique"),
            json.dumps(r.get("llm_missing_elements", [])),
            bool(r.get("llm_fallback", False)),
            r.get("max_existing_kb_sim"),
            r.get("closest_existing_kb"),
            r.get("cluster_alignment_score"),
            r.get("novelty_pass"),
            r.get("alignment_pass"),
            str(r["validation_status"]),
        ))
    execute_values(cur, """
        INSERT INTO kb_validation_results
            (id, run_id, article_id, cluster_id, gap_flag,
             llm_coverage_score, llm_gap_confirmed, llm_verdict, llm_critique,
             llm_missing_elements, llm_fallback,
             max_existing_kb_sim, closest_existing_kb,
             cluster_alignment_score, novelty_pass, alignment_pass,
             validation_status)
        VALUES %s
        ON CONFLICT (article_id, run_id) DO UPDATE
            SET validation_status=EXCLUDED.validation_status,
                llm_verdict=EXCLUDED.llm_verdict,
                llm_coverage_score=EXCLUDED.llm_coverage_score,
                novelty_pass=EXCLUDED.novelty_pass,
                alignment_pass=EXCLUDED.alignment_pass
    """, rows, page_size=50)
    conn.commit(); cur.close(); conn.close()
    log.info(f"  Written {len(rows)} validation results to kb_validation_results.")


def _update_needs_review(engine, run_id: str, article_ids: list):
    """Set needs_review=True for articles that failed or partially passed validation."""
    if not article_ids:
        return
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    cur.executemany(
        "UPDATE generated_kb_articles SET needs_review = true WHERE article_id = %s AND run_id = %s",
        [(aid, run_id) for aid in article_ids]
    )
    conn.commit(); cur.close(); conn.close()
    log.info(f"  Flagged {len(article_ids)} articles as needs_review.")


def _write_eval_metrics(engine, run_id: str, results: list):
    """Append validation summary rows to evaluation_results (category='validation')."""
    if not results:
        return

    total = len(results)
    n_validated   = sum(1 for r in results if r["validation_status"] == "VALIDATED")
    n_partial     = sum(1 for r in results if r["validation_status"] == "PARTIAL")
    n_fail        = sum(1 for r in results if r["validation_status"] == "FAIL")
    n_novelty     = sum(1 for r in results if r.get("novelty_pass") is True)
    scores        = [r["llm_coverage_score"] for r in results if r.get("llm_coverage_score") is not None]
    align_scores  = [r["cluster_alignment_score"] for r in results if r.get("cluster_alignment_score") is not None]
    mean_score    = float(np.mean(scores))    if scores    else 0.0
    mean_align    = float(np.mean(align_scores)) if align_scores else 0.0

    pct_validated = n_validated / total * 100
    pct_fail      = n_fail      / total * 100
    pct_novelty   = n_novelty   / total * 100

    eval_rows = [
        ("Articles fully validated (VALIDATED)",
         f"{n_validated}/{total} ({pct_validated:.0f}%)", "> 70%",
         "Pass ✅" if pct_validated >= 70 else "Fail ❌", "validation"),

        ("Articles partially valid (PARTIAL)",
         f"{n_partial}/{total} ({n_partial/total*100:.0f}%)", "—",
         "Info", "validation"),

        ("Articles failed validation (FAIL)",
         f"{n_fail}/{total} ({pct_fail:.0f}%)", "< 10%",
         "Pass ✅" if pct_fail < 10 else "Fail ❌", "validation"),

        ("Mean LLM coverage score (0-10)",
         f"{mean_score:.1f}/10", "> 6.0",
         "Pass ✅" if mean_score >= 6.0 else "Fail ❌", "validation"),

        ("Articles with gap novelty confirmed",
         f"{n_novelty}/{total} ({pct_novelty:.0f}%)", "> 80%",
         "Pass ✅" if pct_novelty >= 80 else "Warn ⚠", "validation"),

        ("Mean cluster alignment score",
         f"{mean_align:.3f}", "> 0.75",
         "Pass ✅" if mean_align >= 0.75 else "Warn ⚠", "validation"),
    ]

    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    # Remove any prior validation rows for this run (idempotent)
    cur.execute("DELETE FROM evaluation_results WHERE run_id = %s AND category = 'validation'", (run_id,))
    rows = [
        (str(uuid.uuid4()), run_id, metric, result, target, status, cat)
        for metric, result, target, status, cat in eval_rows
    ]
    execute_values(cur, """
        INSERT INTO evaluation_results (eval_id, run_id, metric, result, target, status, category)
        VALUES %s
    """, rows, page_size=20)
    conn.commit(); cur.close(); conn.close()

    log.info("  ═══════════════════════════════════════════════════════════════")
    log.info("  GW IT TRIAGE — PHASE 7 VALIDATION SCORECARD")
    log.info("  ═══════════════════════════════════════════════════════════════")
    for metric, result, target, status, _ in eval_rows:
        log.info(f"  {metric:<48} {result:<18} {target:<12} {status}")
    log.info("  ═══════════════════════════════════════════════════════════════")


def run(engine, run_id: str) -> dict:
    t_start = time.time()
    log.info("── PHASE 7: KB Article Validation ──")

    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VER,
    )
    log.info(f"Azure OpenAI client ready — model={CHAT_MODEL}")

    # ── Load inputs ────────────────────────────────────────────────────────────
    log.info("Loading generated articles...")
    articles_df = _load_generated_articles(engine, run_id)
    log.info(f"  {len(articles_df)} non-duplicate articles with embeddings")

    if articles_df.empty:
        log.warning("  No articles to validate — phase complete (nothing to do).")
        return {"validated": 0, "elapsed_s": 0}

    log.info("Loading existing KB embeddings...")
    existing_kb_titles, existing_kb_matrix = _load_existing_kb_embeddings(engine, run_id)
    log.info(f"  {len(existing_kb_titles)} existing KB articles loaded for novelty check")

    log.info("Loading cluster centroids...")
    centroids = _load_cluster_centroids(engine, run_id)
    log.info(f"  {len(centroids)} cluster centroids loaded for alignment check")

    # ── Validate each article ─────────────────────────────────────────────────
    results           = []
    flagged_for_review = []
    n_llm_fallback    = 0

    for i, (_, art) in enumerate(articles_df.iterrows()):
        article_id = str(art["article_id"])
        cluster_id = int(art["cluster_id"])
        gap_flag   = str(art.get("gap_flag", ""))
        log.info(f"[{i+1}/{len(articles_df)}] Validating article for cluster {cluster_id} ({gap_flag}): {str(art.get('title',''))[:60]}")

        emb = art["article_embedding"]
        if isinstance(emb, str):
            emb = json.loads(emb)
        art_vec = np.asarray(emb, dtype=np.float32)

        # Signal 2a — Gap novelty (embedding vs existing KB)
        # PARTIAL gap articles are supplements — allowed to be close to existing KB
        max_kb_sim, closest_kb, novelty_pass = _compute_novelty(
            art_vec, existing_kb_matrix, existing_kb_titles, gap_flag=gap_flag)

        # Signal 2b — Cluster alignment (embedding vs centroid)
        centroid = centroids.get(cluster_id)
        if centroid is not None:
            align_score, alignment_pass = _compute_alignment(art_vec, centroid)
        else:
            align_score, alignment_pass = 0.0, False
            log.warning(f"  No centroid found for cluster {cluster_id} — alignment_pass=False")

        # Signal 1 — LLM critique (prompt adapts based on gap_flag)
        evidence   = _load_cluster_evidence(engine, run_id, cluster_id)
        llm_result = _llm_critique(client, art.to_dict(), evidence, gap_flag=gap_flag)

        if llm_result is None:
            # LLM failed — use embedding signals only
            n_llm_fallback += 1
            llm_verdict = "PARTIAL" if alignment_pass else "INSUFFICIENT"
            llm_result  = {
                "coverage_score":   None,
                "gap_confirmed":    novelty_pass,
                "missing_elements": [],
                "verdict":          llm_verdict,
                "critique":         "LLM critique unavailable — embedding-only fallback used.",
            }
            llm_fallback = True
        else:
            llm_fallback = False

        validation_status = _final_verdict(
            llm_result["verdict"], novelty_pass, alignment_pass, gap_flag=gap_flag)

        log.info(f"  llm_verdict={llm_result['verdict']}  coverage={llm_result.get('coverage_score')}  "
                 f"novelty={'✅' if novelty_pass else '❌'}  alignment={'✅' if alignment_pass else '❌'}  "
                 f"→ {validation_status}")

        row = {
            "article_id":             article_id,
            "cluster_id":             cluster_id,
            "gap_flag":               gap_flag,
            "llm_coverage_score":     llm_result.get("coverage_score"),
            "llm_gap_confirmed":      llm_result.get("gap_confirmed"),
            "llm_verdict":            llm_result["verdict"],
            "llm_critique":           llm_result.get("critique"),
            "llm_missing_elements":   llm_result.get("missing_elements", []),
            "llm_fallback":           llm_fallback,
            "max_existing_kb_sim":    round(max_kb_sim, 4),
            "closest_existing_kb":    closest_kb,
            "cluster_alignment_score": round(align_score, 4),
            "novelty_pass":           novelty_pass,
            "alignment_pass":         alignment_pass,
            "validation_status":      validation_status,
        }
        results.append(row)

        if validation_status in ("PARTIAL", "FAIL"):
            flagged_for_review.append(article_id)

    # ── Write outputs ──────────────────────────────────────────────────────────
    log.info("Writing kb_validation_results → DB...")
    _write_validation_results(engine, run_id, results)

    log.info("Updating needs_review flags in generated_kb_articles...")
    _update_needs_review(engine, run_id, flagged_for_review)

    log.info("Writing validation metrics → evaluation_results...")
    _write_eval_metrics(engine, run_id, results)

    n_validated = sum(1 for r in results if r["validation_status"] == "VALIDATED")
    n_partial   = sum(1 for r in results if r["validation_status"] == "PARTIAL")
    n_fail      = sum(1 for r in results if r["validation_status"] == "FAIL")

    elapsed = time.time() - t_start
    log.info(f"✅ Phase 7 complete in {elapsed/60:.1f}m  "
             f"[VALIDATED={n_validated}  PARTIAL={n_partial}  FAIL={n_fail}  "
             f"llm_fallback={n_llm_fallback}]")
    return {
        "total":        len(results),
        "validated":    n_validated,
        "partial":      n_partial,
        "fail":         n_fail,
        "llm_fallback": n_llm_fallback,
        "elapsed_s":    round(elapsed, 1),
    }
