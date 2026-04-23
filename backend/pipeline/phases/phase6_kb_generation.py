"""
phase5_kb_generation.py — Phase 5: LLM KB Article Generation (Azure GPT-4.1)

Reads from:  gap_analysis table, cluster_assignments table,
             incidents_processed, workorders_processed (evidence text),
             knowledge_base_articles (existing KB)
Writes to:   generated_kb_articles, kb_search_index
"""
import json
import sys
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


import os
AZURE_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY    = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VER    = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
CHAT_MODEL       = os.getenv("AZURE_OPENAI_CHAT_MODEL", "gpt-4.1")
EMBED_MODEL      = os.getenv("AZURE_OPENAI_EMBED_MODEL", "text-embedding-ada-002")

GW_SYSTEM_PROMPT = """You are a Knowledge Base article writer for George Washington University IT Support (GW IT).
Write articles for IT helpdesk technicians (first-level support) who need step-by-step guidance.
When relevant, reference specific GW systems: identity.gwu.edu, go.gwu.edu, gwu.box.com,
myapps.gwu.edu, gwu.sharepoint.com, gworld.gwu.edu, gwmail.gwu.edu.
Base your article strictly on the evidence provided.
Always return valid JSON matching the schema provided."""

ARTICLE_SCHEMA = {
    "title": "string — concise title (≤80 chars)",
    "category": "string — one of: Account Access, Network, Software, Hardware, Email, Other",
    "keywords": ["list", "of", "search", "keywords"],
    "problem_statement": "string — clear description of the problem/symptom",
    "affected_systems": ["list", "of", "affected", "systems"],
    "resolution_steps": ["Step 1", "Step 2", "Step 3"],
    "additional_notes": "string — edge cases, workarounds, links",
    "escalation_trigger": "string — when to escalate and to whom",
    "confidence": 0.95,
}


def _get_db_conn(engine):
    url  = engine.url
    conn = psycopg2.connect(
        host=str(url.host), port=url.port or 5432,
        dbname=str(url.database), user=str(url.username),
        password=str(url.password),
    )
    register_vector(conn)
    return conn


def _load_critical_gaps(engine, run_id: str) -> pd.DataFrame:
    """Load CRITICAL clusters with dominant service_type for balanced generation."""
    return pd.read_sql("""
        SELECT g.cluster_id, g.cluster_label, g.size, g.top_terms,
               g.priority_score, g.gap_flag,
               COALESCE(s.dominant_service_type, 'Unknown') AS service_type
        FROM   gap_analysis g
        LEFT JOIN (
            SELECT cluster_id,
                   MODE() WITHIN GROUP (ORDER BY service_type) AS dominant_service_type
            FROM   cluster_assignments
            WHERE  run_id = %s
              AND  service_type NOT IN ('', 'Unknown', 'N/A', 'None', 'none')
            GROUP  BY cluster_id
        ) s ON g.cluster_id = s.cluster_id
        WHERE  g.run_id = %s AND g.gap_flag = 'CRITICAL'
        ORDER  BY g.priority_score DESC
    """, engine, params=(run_id, run_id))


def _select_balanced_gaps(critical_df: pd.DataFrame, max_articles: int) -> pd.DataFrame:
    """Stratified cluster selection: proportional slots per service_type.
    Ensures every service type with CRITICAL gaps gets at least 1 article.
    Within each service type, highest priority_score clusters are selected first.
    """
    if "service_type" not in critical_df.columns or critical_df.empty:
        return critical_df.head(max_articles)

    total = len(critical_df)
    if total <= max_articles:
        return critical_df  # no need to trim — take all

    groups = {stype: grp for stype, grp in critical_df.groupby("service_type")}

    # Proportional allocation with minimum 1 per service type
    allocations = {}
    for stype, grp in groups.items():
        proportional = max(1, round(max_articles * len(grp) / total))
        allocations[stype] = proportional

    # Scale down if over budget
    alloc_total = sum(allocations.values())
    if alloc_total > max_articles:
        scale = max_articles / alloc_total
        allocations = {k: max(1, int(v * scale)) for k, v in allocations.items()}
        # Fill remaining slots (rounding losses) with highest priority types
        remaining = max_articles - sum(allocations.values())
        for stype in sorted(groups, key=lambda s: groups[s]["priority_score"].max(), reverse=True):
            if remaining <= 0:
                break
            allocations[stype] += 1
            remaining -= 1

    selected = []
    for stype, grp in groups.items():
        slots = allocations.get(stype, 1)
        selected.append(grp.head(slots))

    result = pd.concat(selected).sort_values("priority_score", ascending=False)
    log.info(f"  Balanced selection: {len(result)} clusters across {len(groups)} service types")
    for stype in sorted(allocations):
        log.info(f"    {stype}: {allocations[stype]} slots")
    return result.head(max_articles)


def _load_partial_gaps(engine, run_id: str) -> pd.DataFrame:
    """Load PARTIAL clusters with their best-matching existing KB article text."""
    return pd.read_sql("""
        SELECT g.cluster_id, g.cluster_label, g.size, g.top_terms,
               g.priority_score, g.gap_flag, g.best_kb_title,
               k.issue AS kb_issue, k.solution AS kb_solution
        FROM   gap_analysis g
        LEFT JOIN knowledge_base_articles k ON g.best_kb_title = k.title
        WHERE  g.run_id = %s AND g.gap_flag = 'PARTIAL'
        ORDER  BY g.priority_score DESC
    """, engine, params=(run_id,))


def _load_cluster_evidence(engine, run_id: str, cluster_id: int, n: int = 5) -> list[dict]:
    """Return up to n evidence pairs {problem, resolution} ordered by evidence tier (best first).

    Incidents: problem = detailed_description | description; resolution = resolution_summary
    Work orders: problem = detailed_description | description; resolution = activity_logs_text
    Filters out resolutions that are shorter than 30 chars after stripping ***.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT problem_text, resolution_text, evidence_tier FROM (
                -- Incidents
                SELECT COALESCE(ip.detailed_description, ip.description, '') AS problem_text,
                       ip.resolution_summary AS resolution_text,
                       COALESCE(te.evidence_tier, 4)                        AS evidence_tier
                FROM   cluster_assignments ca
                JOIN   incidents_processed ip ON ca.source_id::text = ip.id::text
                LEFT JOIN ticket_embeddings te
                       ON ca.source_id::text = te.source_id::text
                      AND ca.source = te.source AND te.run_id = :r
                WHERE  ca.run_id = :r AND ca.cluster_id = :c AND ca.source = 'incident'
                  AND  ip.resolution_summary IS NOT NULL
                  AND  LENGTH(REPLACE(ip.resolution_summary, '***', '')) > 30

                UNION ALL

                -- Work orders
                SELECT COALESCE(wp.detailed_description, wp.description, '') AS problem_text,
                       wp.activity_logs_text AS resolution_text,
                       COALESCE(te.evidence_tier, 4)                         AS evidence_tier
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
    return [
        {"problem": row[0] or "", "resolution": row[1] or "", "tier": row[2]}
        for row in rows if row[1]
    ]


def _evidence_quality_score(evidence: list[dict]) -> float:
    """Return 0..1 quality score based on tier distribution."""
    if not evidence:
        return 0.0
    tier_weights = {1: 3.0, 2: 1.5, 3: 0.5, 4: 0.0}
    score = sum(tier_weights.get(e.get("tier", 4), 0.0) for e in evidence)
    return min(score / (len(evidence) * 3.0), 1.0)


def _generate_article(client: AzureOpenAI, gap: dict, evidence: list[dict],
                      existing_kb: dict = None) -> dict:
    """Call GPT-4.1 to generate a structured KB article.

    evidence is a list of {problem, resolution, tier} dicts.
    existing_kb (optional) is {title, issue, solution} for PARTIAL gaps — used to generate
    a supplement article that fills cases not covered by the existing KB article.
    Retries with progressively fewer evidence items if Azure content filter triggers.
    """
    def _build_prompt(ev_items: list[dict]) -> str:
        if ev_items:
            item_blocks = []
            for e in ev_items:
                item_blocks.append(
                    f"  <item>\n"
                    f"    <problem>{e['problem'][:400]}</problem>\n"
                    f"    <resolution>{e['resolution'][:600]}</resolution>\n"
                    f"  </item>"
                )
            evidence_section = "<evidence_data>\n" + "\n".join(item_blocks) + "\n</evidence_data>"
        else:
            evidence_section = "<evidence_data>(no raw ticket evidence available — write a general article)</evidence_data>"

        if existing_kb:
            existing_section = f"""
<existing_kb_article>
  <title>{existing_kb.get('title', '')}</title>
  <issue>{str(existing_kb.get('kb_issue', ''))[:400]}</issue>
  <solution>{str(existing_kb.get('kb_solution', ''))[:600]}</solution>
</existing_kb_article>

An existing KB article partially covers this topic (shown above). Generate a SUPPLEMENTAL article
that covers the specific gap cases described in the ticket evidence below that the existing article
does NOT address. Do not repeat what the existing article already covers."""
        else:
            existing_section = ""

        return f"""Generate a GW IT Knowledge Base article for the IT support gap described below.

<gap_metadata>
  <topic>{gap['cluster_label']}</topic>
  <ticket_volume>{gap['size']:,} tickets</ticket_volume>
  <key_terms>{gap.get('top_terms', 'N/A')}</key_terms>
  <priority_score>{gap.get('priority_score', 0):.2f}</priority_score>
</gap_metadata>
{existing_section}
The following are anonymised problem descriptions and resolution notes from real GW IT support tickets.
Use them as background context only — do not reproduce them verbatim.
{evidence_section}

Return ONLY valid JSON matching this schema:
{json.dumps(ARTICLE_SCHEMA, indent=2)}"""

    # Retry schedule: try with full evidence first, then fewer items on
    # content_filter failures (text kept intact — count reduced, not modified).
    n_ev_start = min(len(evidence), 5)
    retry_counts = [n_ev_start, max(n_ev_start - 2, 0), 0]
    retry_counts = list(dict.fromkeys(retry_counts))  # deduplicate while preserving order

    for n_ev in retry_counts:
        prompt = _build_prompt(evidence[:n_ev])
        try:
            resp = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": GW_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
                max_tokens=2000,
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            err_str = str(e)
            if "content_filter" in err_str or "content management" in err_str.lower():
                if n_ev > 0:
                    log.warning(f"  Content filter triggered with {n_ev} evidence items — retrying with fewer...")
                    continue
                raise
            raise



def _score_article(article: dict) -> tuple[int, list[str], bool]:
    """Score article quality (0-10). Returns (score, issues, needs_review)."""
    issues = []
    score  = 10
    steps  = article.get("resolution_steps", [])
    if not isinstance(steps, list) or len(steps) < 2:
        issues.append("Too few resolution steps"); score -= 3
    elif len(steps) < 3:
        issues.append("Needs more steps"); score -= 1
    if len(str(article.get("problem_statement", ""))) < 50:
        issues.append("Problem statement too short"); score -= 2
    if not article.get("escalation_trigger"):
        issues.append("Missing escalation trigger"); score -= 1
    if article.get("confidence", 1.0) < 0.70:
        issues.append("Low model confidence"); score -= 1
    return max(0, score), issues, score < 6


def _embed_text(client: AzureOpenAI, text: str) -> list[float]:
    """Embed article text using text-embedding-ada-002."""
    resp = client.embeddings.create(model=EMBED_MODEL, input=text[:8000])
    return resp.data[0].embedding


def run(engine, run_id: str) -> dict:
    t_start = time.time()
    log.info("── PHASE 5: KB Article Generation ──")

    client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VER,
    )
    log.info(f"Azure OpenAI client ready — model={CHAT_MODEL}  api_version={AZURE_API_VER}")

    # ── Load critical + partial gaps from DB ──────────────────────────────────
    log.info("Loading critical and partial gaps...")
    critical_df = _load_critical_gaps(engine, run_id)
    partial_df  = _load_partial_gaps(engine, run_id)
    log.info(f"  CRITICAL clusters: {len(critical_df)}  PARTIAL clusters: {len(partial_df)}")

    n_critical = len(critical_df)
    n_partial  = len(partial_df)

    articles = []

    # ── Process CRITICAL gaps ──────────────────────────────────────────────────
    log.info(f"Generating {n_critical} articles for ALL CRITICAL gaps...")
    for i, (_, gap) in enumerate(critical_df.iterrows()):
        cid   = int(gap["cluster_id"])
        label = str(gap["cluster_label"])
        log.info(f"[CRITICAL {i+1}/{n_critical}] C{cid}: {label}")

        evidence   = _load_cluster_evidence(engine, run_id, cid)
        ev_quality = _evidence_quality_score(evidence)

        try:
            article = _generate_article(client, gap.to_dict(), evidence)
        except Exception as e:
            log.error(f"  Article generation failed for C{cid}: {e}")
            continue

        quality_score, quality_issues, needs_review = _score_article(article)

        # Force needs_review for low-evidence clusters
        if ev_quality < 0.2:
            needs_review = True
            quality_issues = quality_issues + ["Insufficient evidence — requires technician review"]
            if "additional_notes" in article:
                article["additional_notes"] = (
                    article["additional_notes"] + " [AUTO-STUB: Low evidence quality — "
                    "technician review required before publishing.]"
                )
            else:
                article["additional_notes"] = (
                    "[AUTO-STUB: Insufficient evidence — requires technician review before publishing.]"
                )

        # Embed the article
        try:
            embed_text = (f"{article.get('title','')} {article.get('problem_statement','')} "
                          f"{' '.join(article.get('resolution_steps', []) if isinstance(article.get('resolution_steps'), list) else [])}")
            embedding  = _embed_text(client, embed_text)
        except Exception as e:
            log.warning(f"  Embedding failed for C{cid}: {e}")
            embedding = None

        articles.append({
            "cluster_id":         cid,
            "cluster_label":      label,
            "ticket_count":       int(gap["size"]),
            "priority_score":     float(gap["priority_score"]),
            "gap_flag":           str(gap["gap_flag"]),
            "evidence_count":     len(evidence),
            "ev_quality":         round(ev_quality, 3),
            "quality_score":      quality_score,
            "quality_issues":     quality_issues,
            "needs_review":       needs_review,
            "article_embedding":  embedding,
            **{k: v for k, v in article.items() if k != "confidence"},
            "confidence":         float(article.get("confidence", 0.9)),
        })
        log.info(f"  quality={quality_score}/10  needs_review={needs_review}  "
                 f"evidence={len(evidence)}  ev_quality={ev_quality:.2f}")

    # ── Process PARTIAL gaps (supplement articles) ─────────────────────────────
    log.info(f"Generating {n_partial} supplement articles for ALL PARTIAL gaps...")
    for i, (_, gap) in enumerate(partial_df.iterrows()):
        cid   = int(gap["cluster_id"])
        label = str(gap["cluster_label"])
        log.info(f"[PARTIAL {i+1}/{n_partial}] C{cid}: {label}")

        evidence   = _load_cluster_evidence(engine, run_id, cid)
        ev_quality = _evidence_quality_score(evidence)

        existing_kb = {
            "title":      str(gap.get("best_kb_title", "")),
            "kb_issue":   str(gap.get("kb_issue", "")),
            "kb_solution": str(gap.get("kb_solution", "")),
        }

        try:
            article = _generate_article(client, gap.to_dict(), evidence, existing_kb=existing_kb)
        except Exception as e:
            log.error(f"  Supplement article generation failed for C{cid}: {e}")
            continue

        quality_score, quality_issues, needs_review = _score_article(article)

        if ev_quality < 0.2:
            needs_review = True
            quality_issues = quality_issues + ["Insufficient evidence — requires technician review"]
            article.setdefault("additional_notes", "")
            article["additional_notes"] = (
                article["additional_notes"] +
                " [AUTO-STUB: Low evidence quality — technician review required before publishing.]"
            )

        try:
            embed_text = (f"{article.get('title','')} {article.get('problem_statement','')} "
                          f"{' '.join(article.get('resolution_steps', []) if isinstance(article.get('resolution_steps'), list) else [])}")
            embedding  = _embed_text(client, embed_text)
        except Exception as e:
            log.warning(f"  Embedding failed for C{cid}: {e}")
            embedding = None

        articles.append({
            "cluster_id":        cid,
            "cluster_label":     label,
            "ticket_count":      int(gap["size"]),
            "priority_score":    float(gap["priority_score"]),
            "gap_flag":          "PARTIAL",
            "evidence_count":    len(evidence),
            "ev_quality":        round(ev_quality, 3),
            "quality_score":     quality_score,
            "quality_issues":    quality_issues,
            "needs_review":      needs_review,
            "article_embedding": embedding,
            **{k: v for k, v in article.items() if k != "confidence"},
            "confidence":        float(article.get("confidence", 0.9)),
        })
        log.info(f"  quality={quality_score}/10  needs_review={needs_review}  "
                 f"evidence={len(evidence)}  ev_quality={ev_quality:.2f}")

    log.info(f"Generated {len(articles)} articles total ({n_critical} CRITICAL + {n_partial} PARTIAL). Running post-generation deduplication...")
    articles = _deduplicate_articles(articles)
    log.info(f"Writing {len(articles)} articles to DB...")
    _write_articles_to_db(engine, run_id, articles)
    log.info("Writing to kb_search_index...")
    _add_generated_to_search_index(engine, run_id, articles)

    log.info("Creating HNSW vector indexes (IF NOT EXISTS)...")
    _create_hnsw_indexes(engine)

    elapsed = time.time() - t_start
    log.info(f"✅ Phase 5 complete in {elapsed/60:.1f}m  [{len(articles)} articles generated]")
    return {"articles_generated": len(articles), "elapsed_s": round(elapsed, 1)}


def _deduplicate_articles(articles: list[dict], sim_threshold: float = 0.95) -> list[dict]:
    """Mark near-duplicate articles (article_embedding cosine_sim > threshold).
    For each duplicate pair keep the higher quality_score article as canonical.
    Sets 'is_duplicate_of' to the canonical article's cluster_id (used as a temp key;
    actual UUID is resolved at write time).
    """
    embeds = [a.get("article_embedding") for a in articles]
    valid  = [(i, np.asarray(e, dtype=np.float32)) for i, e in enumerate(embeds) if e is not None]
    if len(valid) < 2:
        return articles

    idxs    = [v[0] for v in valid]
    matrix  = np.vstack([v[1] for v in valid]).astype(np.float32)
    norms   = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms   = np.where(norms == 0, 1, norms)
    matrix  = matrix / norms
    sim_mat = matrix @ matrix.T

    duplicate_of = {}   # article index → canonical article index
    for i in range(len(idxs)):
        if idxs[i] in duplicate_of:
            continue
        for j in range(i + 1, len(idxs)):
            if idxs[j] in duplicate_of:
                continue
            if float(sim_mat[i, j]) > sim_threshold:
                qi = articles[idxs[i]].get("quality_score", 0)
                qj = articles[idxs[j]].get("quality_score", 0)
                canonical, dup = (idxs[i], idxs[j]) if qi >= qj else (idxs[j], idxs[i])
                duplicate_of[dup] = canonical

    n_dups = len(duplicate_of)
    if n_dups:
        log.info(f"  Post-gen dedup: {n_dups} duplicate article(s) marked.")
        for dup_idx, can_idx in duplicate_of.items():
            articles[dup_idx]["is_duplicate_of_cluster"] = articles[can_idx]["cluster_id"]
    else:
        log.info("  Post-gen dedup: no duplicates found.")
    return articles


def _write_articles_to_db(engine, run_id, articles):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    # Clear existing rows for this run so re-runs don't leave stale duplicates.
    # Delete validation results first (FK references generated_kb_articles.article_id).
    cur.execute("""
        DELETE FROM kb_validation_results
        WHERE article_id IN (
            SELECT article_id FROM generated_kb_articles WHERE run_id = %s
        )
    """, (run_id,))
    cur.execute("DELETE FROM generated_kb_articles WHERE run_id = %s", (run_id,))
    conn.commit()
    rows = []
    for a in articles:
        rows.append((
            str(uuid.uuid4()), run_id,
            int(a["cluster_id"]), str(a.get("cluster_label", "")),
            int(a.get("ticket_count", 0)),
            float(a.get("priority_score", 0)),
            str(a.get("gap_flag", "")),
            str(a.get("title", "")),
            str(a.get("category", "Other")),
            json.dumps(a.get("keywords", [])),
            str(a.get("problem_statement", "")),
            json.dumps(a.get("affected_systems", [])),
            json.dumps(a.get("resolution_steps", [])),
            str(a.get("additional_notes", "")),
            str(a.get("escalation_trigger", "")),
            json.dumps({k: v for k, v in a.items() if k != "article_embedding"}),
            float(a.get("confidence", 0.9)),
            int(a.get("quality_score", 0)),
            json.dumps(a.get("quality_issues", [])),
            bool(a.get("needs_review", False)),
            CHAT_MODEL,
            int(a.get("evidence_count", 0)),
            a["article_embedding"],           # list[float] or None
        ))
    execute_values(cur, """
        INSERT INTO generated_kb_articles
            (article_id, run_id, cluster_id, cluster_label, ticket_count,
             priority_score, gap_flag, title, category, keywords,
             problem_statement, affected_systems, resolution_steps,
             additional_notes, escalation_trigger, full_article_json,
             confidence, quality_score, quality_issues, needs_review,
             model, evidence_count, article_embedding)
        VALUES %s
        ON CONFLICT (cluster_id, run_id) DO UPDATE
            SET title=EXCLUDED.title, quality_score=EXCLUDED.quality_score,
                article_embedding=EXCLUDED.article_embedding,
                needs_review=EXCLUDED.needs_review
    """, rows, page_size=50)
    conn.commit()

    # Resolve is_duplicate_of: map cluster_id → article_id just inserted
    cur.execute("""
        SELECT article_id, cluster_id FROM generated_kb_articles WHERE run_id = %s
    """, (run_id,))
    cid_to_aid = {int(r[1]): str(r[0]) for r in cur.fetchall()}
    dup_updates = []
    for a in articles:
        dup_cid = a.get("is_duplicate_of_cluster")
        if dup_cid is not None and int(dup_cid) in cid_to_aid:
            own_aid = cid_to_aid.get(int(a["cluster_id"]))
            can_aid = cid_to_aid[int(dup_cid)]
            if own_aid:
                dup_updates.append({"own": own_aid, "canonical": can_aid})
    if dup_updates:
        for upd in dup_updates:
            cur.execute(
                "UPDATE generated_kb_articles SET is_duplicate_of = %s WHERE article_id = %s",
                (upd["canonical"], upd["own"])
            )
        conn.commit()
        log.info(f"  Marked {len(dup_updates)} articles as duplicates.")
    conn.commit(); cur.close(); conn.close()
    log.info(f"  Written {len(rows)} articles to generated_kb_articles.")


def _add_generated_to_search_index(engine, run_id, articles):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    # Clear old generated entries for this run before re-inserting
    cur.execute("DELETE FROM kb_search_index WHERE run_id = %s AND source = 'generated'", (run_id,))
    conn.commit()
    rows = []
    for a in articles:
        if not a.get("article_embedding"):
            continue
        content = f"{a.get('problem_statement','')} {' '.join(a.get('resolution_steps',[]) if isinstance(a.get('resolution_steps'), list) else [])}".strip()[:500]
        rows.append((
            str(uuid.uuid4()), run_id,
            "generated", str(a["cluster_id"]),
            str(a.get("title", "")),
            str(a.get("category", "Other")),
            content,
            a["article_embedding"],
            True,
        ))
    if rows:
        execute_values(cur, """
            INSERT INTO kb_search_index
                (entry_id, run_id, source, source_id, title, category, content, embedding, is_generated)
            VALUES %s
            ON CONFLICT (source, source_id, run_id) DO UPDATE
                SET embedding=EXCLUDED.embedding, title=EXCLUDED.title
        """, rows, page_size=50)
        conn.commit()
    log.info(f"  Added {len(rows)} generated articles to kb_search_index.")
    cur.close(); conn.close()


def _create_hnsw_indexes(engine):
    """Create HNSW indexes for pgvector semantic search — idempotent."""
    from sqlalchemy import text
    ddls = [
        """CREATE INDEX IF NOT EXISTS idx_kb_search_hnsw
           ON kb_search_index USING hnsw (embedding vector_cosine_ops)
           WITH (m = 16, ef_construction = 64)""",
        """CREATE INDEX IF NOT EXISTS idx_ticket_emb_hnsw
           ON ticket_embeddings USING hnsw (problem_vec vector_cosine_ops)
           WITH (m = 16, ef_construction = 64)""",
        """CREATE INDEX IF NOT EXISTS idx_generated_art_hnsw
           ON generated_kb_articles USING hnsw (article_embedding vector_cosine_ops)
           WITH (m = 16, ef_construction = 64)""",
    ]
    with engine.connect() as conn:
        for ddl in ddls:
            conn.execute(text(ddl))
        conn.commit()
    log.info("  HNSW indexes created (or already exist). ✅")
