"""
phase2_preprocessing.py — Phase 2: Unified Embedding Extraction & Quality Filtering

Reads from:  incidents_processed, workorders_processed (existing tables)
Writes to:   ticket_embeddings table  (source='incident' | 'workorder')

Incidents  : embeddings read from pre-computed JSONB columns (no new API calls).
Work orders: detailed_description is loaded as raw TEXT, cleaned of email-thread
             noise, and re-embedded via Azure OpenAI text-embedding-ada-002 so that
             problem_vec reflects the actual IT problem — not the email structure.
             e_description and e_activity_logs are still read from DB as JSONB.
             Source tables (workorders_processed) are NOT modified.
All output stored in DB only — no .npy or CSV files created.
"""
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from dotenv import load_dotenv
from openai import AzureOpenAI
from sklearn.preprocessing import normalize
from sqlalchemy import text

from pipeline.embedding_utils import parse_jsonb_vector, combine_embeddings
from core.pipeline_logger import get_phase_logger as get_logger

log = get_logger(__name__)

BATCH_SIZE           = 3000
DIM                  = 1536
MIN_RESOLUTION_WORDS = 5     # incidents: resolution_summary min word count
MIN_ACTIVITY_WORDS   = 30    # work orders: min activity words to pass quality filter
MIN_CLEAN_DETAIL_WORDS = 22  # min words remaining after email strip to warrant re-embedding

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY  = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VER  = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
EMBED_MODEL    = os.getenv("AZURE_OPENAI_EMBED_MODEL", "text-embedding-ada-002")


def _get_evidence_tier(resolution_text: str) -> int:
    """Classify resolution/activity text quality into tiers 1-4.

    Tier 1 — structured AI-template response (starts with [$$): best evidence
    Tier 2 — long clean resolution (>150 chars after stripping ***)
    Tier 3 — short but real (>50 chars)
    Tier 4 — too short, empty, or all-redacted
    """
    if not resolution_text:
        return 4
    if resolution_text.startswith("[$$"):
        return 1
    text = resolution_text.replace("***", "").strip()
    if len(text) > 150:
        return 2
    if len(text) > 50:
        return 3
    return 4


# ─────────────────────────────────────────────────────────────────────────────
# Email noise stripping (work orders — Option C)
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_HEADER_RE = re.compile(
    r'^(From:|To:|CC:|BCC:|Subject:|Date:|Sent:|Assigned To:|Pending by:'
    r'|Importance:|Priority:|Reply-To:|Return-Path:|X-[A-Za-z0-9-]+:)',
    re.IGNORECASE,
)
_FORWARDED_SEP_RE = re.compile(
    r'(-{3,}\s*(Forwarded|Original)\s*(message|Mail)?|-{3,}|_{10,})',
    re.IGNORECASE,
)
_REDACTED_ONLY_RE  = re.compile(r'^\s*(\*{3}\s*)+$')
_REPLY_HEADER_RE   = re.compile(r'^On\s+.{5,80}wrote\s*:', re.IGNORECASE | re.DOTALL)
_QUOTED_REPLY_RE   = re.compile(r'^>+\s')
_OUTLOOK_SIG_RE    = re.compile(r'^\s*Get\s+Outlook\s+for\s+(iOS|Android|Mac|Windows)', re.IGNORECASE)
_SENT_FROM_RE      = re.compile(r'^\s*Sent\s+from\s+my\s+', re.IGNORECASE)


def strip_email_noise(text: str) -> str:
    """Remove email-thread boilerplate from work order detailed_description.

    Strips email headers (From:, To:, Subject: …), forwarded-message separators,
    redacted-PII-only lines (***), and reply attribution lines.
    If a forwarded separator is found, only the text before it is kept
    (the most recent message is at the top in GW IT's email format).

    Returns cleaned text, or "" if nothing meaningful remains.
    """
    if not text:
        return ""
    # Trim at first forwarded/original-message separator
    sep_match = _FORWARDED_SEP_RE.search(text)
    if sep_match:
        text = text[:sep_match.start()].strip()

    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _EMAIL_HEADER_RE.match(stripped):
            continue
        if _REDACTED_ONLY_RE.match(stripped):
            continue
        if _REPLY_HEADER_RE.match(stripped):
            continue
        if _QUOTED_REPLY_RE.match(stripped):
            continue
        if _OUTLOOK_SIG_RE.match(stripped):
            continue
        if _SENT_FROM_RE.match(stripped):
            continue
        clean_lines.append(stripped)

    return " ".join(clean_lines).strip()


# Known system-generated boilerplate patterns in work order detailed_description.
# These are auto-filled by BMC Smart IT and contain no real problem signal.
# Each tuple is a set of signatures — a text is boilerplate if ALL sigs in any set match.
_BOILERPLATE_SIGNATURE_SETS = [
    # Template 1: "Assign the first task to yourself..." (lifecycle refresh WOs)
    {"assign the first", "set the wo status to"},
    # Template 2: "For the *** of this request, please click on the View REQ..." (service request WOs)
    {"click on the view req", "located under functions"},
    # Template 3: "Please see related REQ for any additional information"
    {"please see related req"},
    # Template 4: "For the question responses, please open the related REQ"
    {"please open the related req"},
    # Template 5: Fully-redacted action form "Action: *** \n*** Requested: *** \nComments:"
    {"*** requested:", "comments:"},
]


def _is_boilerplate(text: str) -> bool:
    """Return True if the cleaned detailed_description is a system-generated
    boilerplate template with no real problem signal."""
    if not text:
        return False
    lower = text.lower()
    return any(all(sig in lower for sig in sig_set) for sig_set in _BOILERPLATE_SIGNATURE_SETS)


def _embed_texts_batch(
    client: AzureOpenAI,
    texts: list,
    embed_batch_size: int = 16,
) -> list:
    """Embed a list of strings via Azure OpenAI in batches.

    Returns a list[Optional[np.ndarray]] of the same length as texts.
    Items are None for empty inputs or failed calls.
    Uses exponential backoff (up to 4 attempts) on rate-limit / transient errors.
    """
    results = [None] * len(texts)
    for start in range(0, len(texts), embed_batch_size):
        end   = min(start + embed_batch_size, len(texts))
        batch = texts[start:end]
        non_empty = [(i, t) for i, t in enumerate(batch) if t.strip()]
        if not non_empty:
            continue
        idxs, txts = zip(*non_empty)
        for attempt in range(4):
            try:
                resp = client.embeddings.create(
                    model=EMBED_MODEL,
                    input=[t[:8000] for t in txts],
                )
                for local_i, data in zip(idxs, resp.data):
                    results[start + local_i] = np.array(data.embedding, dtype=np.float32)
                break
            except Exception as exc:
                wait = 2 ** attempt
                if attempt < 3:
                    log.warning(
                        f"  [embed] batch [{start}:{end}] attempt {attempt+1} failed: "
                        f"{exc} — retrying in {wait}s"
                    )
                    time.sleep(wait)
                else:
                    log.error(f"  [embed] batch [{start}:{end}] failed after 4 attempts: {exc}")
        time.sleep(0.3)  # gentle rate-limit buffer
        if (start // embed_batch_size) % 10 == 0 and start > 0:
            log.info(f"  [embed] {end}/{len(texts)} texts embedded")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Incidents
# ─────────────────────────────────────────────────────────────────────────────

INC_META_COLS = [
    "id", "ticket_number", "service_type", "assigned_group",
    "assigned_support_company", "impact", "urgency",
    "reported_source", "reported_date", "resolved_date",
    "resolution_summary", "pii_detected",
    "description",  # raw subject text — used to detect templated/generic subjects
]


# Generic / templated incident subjects that leak noise into problem_vec.
# When the raw description matches any of these patterns, we use
# detailed_description alone (weight 1.0) instead of blending in e_description.
_GENERIC_SUBJECT_RE = re.compile(
    r'^\s*('
    r'service\s+request(\s*-\s*.*)?'        # "Service Request - Account Access"
    r'|inc\s*-\s*'                           # "Inc - …"
    r'|incident\s*-\s*'                      # "Incident - …"
    r'|new\s+request'                        # "New Request"
    r'|general\s+inquiry'                    # "General Inquiry"
    r'|access\s+request'                     # "Access Request"
    r'|password\s+(reset|change)\s*$'        # bare "Password Reset" with no detail
    r'|fwd?:\s*'                             # bare "Fwd:" subjects
    r'|re:\s*$'                              # lone "Re:"
    r')',
    re.IGNORECASE,
)


def _is_generic_subject(subject: str) -> bool:
    """Return True if the raw incident description (subject line) is a generic
    templated phrase that adds no problem-specific signal."""
    if not subject:
        return True   # missing subject is treated as generic — use detail only
    s = subject.strip()
    if len(s.split()) < 2:
        return True   # 0–1 word subjects ("password", "help") are generic
    return bool(_GENERIC_SUBJECT_RE.match(s))


def _load_incidents(engine):
    """Return (df_meta, problem_matrix, resolution_matrix) for incidents."""
    import pandas as pd
    with engine.connect() as conn:
        inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents_processed")).scalar()
    log.info(f"  Incidents: {inc_count:,} rows")

    meta_str = ", ".join(INC_META_COLS)
    batches = []
    for offset in range(0, inc_count, BATCH_SIZE):
        q = (f"SELECT {meta_str} FROM incidents_processed "
             f"ORDER BY id LIMIT {BATCH_SIZE} OFFSET {offset}")
        batches.append(pd.read_sql(q, engine))
        log.info(f"  [incidents] meta {min(offset+BATCH_SIZE, inc_count):,}/{inc_count:,}")
    df_meta = pd.concat(batches, ignore_index=True)
    n = len(df_meta)

    def load_col(col: str):
        mat   = np.zeros((n, DIM), dtype=np.float32)
        valid = np.zeros(n, dtype=bool)
        for offset in range(0, n, BATCH_SIZE):
            end = min(offset + BATCH_SIZE, n)
            rows = pd.read_sql(
                f"SELECT {col} FROM incidents_processed ORDER BY id "
                f"LIMIT {end-offset} OFFSET {offset}", engine
            )[col].tolist()
            for i, val in enumerate(rows):
                vec = parse_jsonb_vector(val)
                if vec is not None:
                    mat[offset + i] = vec
                    valid[offset + i] = True
            log.info(f"  [incidents] {col}: {min(offset+BATCH_SIZE, n):,}/{n:,}")
        return mat, valid

    e_desc_mat,   e_desc_valid   = load_col("e_description")
    e_detail_mat, e_detail_valid = load_col("e_detailed_description")
    e_res_mat,    e_res_valid    = load_col("e_resolution_summary")

    # Combine problem vec
    # Generic-subject incidents bypass description blending — use detail only
    # so the templated subject ("Service Request - …") doesn't leak into problem_vec.
    generic_subj = df_meta["description"].fillna("").apply(_is_generic_subject).values
    n_generic = int(generic_subj.sum())
    log.info(f"  [incidents] generic templated subjects: {n_generic:,}/{n} → detail-only embedding")

    problem_matrix = np.zeros((n, DIM), dtype=np.float32)
    problem_valid  = np.zeros(n, dtype=bool)
    for i in range(n):
        if generic_subj[i] and e_detail_valid[i]:
            # Templated subject — use detailed_description alone (weight 1.0)
            vec = e_detail_mat[i]
        elif e_desc_valid[i] and e_detail_valid[i]:
            vec = combine_embeddings(e_desc_mat[i], e_detail_mat[i], w_primary=0.4, w_secondary=0.6)
        elif e_desc_valid[i]:
            vec = e_desc_mat[i]
        elif e_detail_valid[i]:
            vec = e_detail_mat[i]
        else:
            vec = None
        if vec is not None:
            problem_matrix[i] = vec
            problem_valid[i]  = True
    del e_desc_mat, e_detail_mat

    # Quality filter
    df_meta["res_word_count"] = df_meta["resolution_summary"].fillna("").str.split().str.len()
    df_meta["evidence_tier"]  = df_meta["resolution_summary"].fillna("").apply(_get_evidence_tier)
    f_res_len    = df_meta["res_word_count"].values >= MIN_RESOLUTION_WORDS
    quality_mask = problem_valid & e_res_valid & f_res_len

    log.info(f"  [incidents] quality pass: {quality_mask.sum():,}/{n:,} ({quality_mask.mean()*100:.1f}%)")

    problem_f    = normalize(problem_matrix[quality_mask], norm="l2").astype(np.float32)
    resolution_f = normalize(e_res_mat[quality_mask],     norm="l2").astype(np.float32)
    df_filtered  = df_meta[quality_mask].reset_index(drop=True)
    del problem_matrix, e_res_mat

    return df_filtered, problem_f, resolution_f


# ─────────────────────────────────────────────────────────────────────────────
# Work Orders
# ─────────────────────────────────────────────────────────────────────────────

WO_META_COLS = [
    "id", "ticket_number", "service_type", "assigned_group",
    "status", "activity_logs_text",
    "detailed_description",   # loaded as TEXT for email cleaning + re-embedding
]


def _load_workorders(engine, client: AzureOpenAI):
    """Return (df_meta, problem_matrix, resolution_matrix) for work orders.

    Option C implementation — source table is NOT modified:
      1. e_description JSONB read from DB (short clean title — unchanged)
      2. detailed_description TEXT loaded, email-noise stripped, re-embedded fresh
         via Azure OpenAI → used as the 'detail' component of problem_vec
      3. e_activity_logs JSONB read from DB (technician notes — unchanged) → res_vec

    Rows where clean detail < MIN_CLEAN_DETAIL_WORDS fall back to description-only.
    """
    import pandas as pd
    with engine.connect() as conn:
        wo_count = conn.execute(text("SELECT COUNT(*) FROM workorders_processed")).scalar()
    log.info(f"  Work orders: {wo_count:,} rows")

    meta_str = ", ".join(WO_META_COLS)
    batches = []
    for offset in range(0, wo_count, BATCH_SIZE):
        q = (f"SELECT {meta_str} FROM workorders_processed "
             f"ORDER BY id LIMIT {BATCH_SIZE} OFFSET {offset}")
        batches.append(pd.read_sql(q, engine))
        log.info(f"  [workorders] meta {min(offset+BATCH_SIZE, wo_count):,}/{wo_count:,}")
    df_meta = pd.concat(batches, ignore_index=True)
    n = len(df_meta)

    def load_col(col: str):
        mat   = np.zeros((n, DIM), dtype=np.float32)
        valid = np.zeros(n, dtype=bool)
        for offset in range(0, n, BATCH_SIZE):
            end = min(offset + BATCH_SIZE, n)
            rows = pd.read_sql(
                f"SELECT {col} FROM workorders_processed ORDER BY id "
                f"LIMIT {end-offset} OFFSET {offset}", engine
            )[col].tolist()
            for i, val in enumerate(rows):
                vec = parse_jsonb_vector(val)
                if vec is not None:
                    mat[offset + i] = vec
                    valid[offset + i] = True
            log.info(f"  [workorders] {col}: {min(offset+BATCH_SIZE, n):,}/{n:,}")
        return mat, valid

    # ── Step 1: Load e_description from DB (clean short title — no change) ──────
    e_desc_mat, e_desc_valid = load_col("e_description")

    # ── Step 2: Load e_activity_logs from DB (technician notes — no change) ─────
    e_act_mat, e_act_valid = load_col("e_activity_logs")

    # ── Step 3: Strip email noise from detailed_description TEXT + re-embed ──────
    log.info("  [workorders] Stripping email noise from detailed_description...")
    df_meta["detailed_description_clean"] = (
        df_meta["detailed_description"].fillna("").apply(strip_email_noise)
    )
    df_meta["clean_word_count"] = (
        df_meta["detailed_description_clean"].str.split().str.len().fillna(0)
    )

    df_meta["is_boilerplate"] = df_meta["detailed_description_clean"].apply(_is_boilerplate)
    n_boilerplate = int(df_meta["is_boilerplate"].sum())
    if n_boilerplate:
        log.info(f"  [workorders] {n_boilerplate:,} rows have boilerplate detailed_description → excluded from detail embedding")

    embed_mask  = (df_meta["clean_word_count"] >= MIN_CLEAN_DETAIL_WORDS) & ~df_meta["is_boilerplate"]
    n_to_embed  = int(embed_mask.sum())
    n_too_short = n - n_to_embed
    log.info(
        f"  [workorders] {n_to_embed:,} rows have clean detail (≥{MIN_CLEAN_DETAIL_WORDS} words, non-boilerplate) "
        f"→ will embed;  {n_too_short:,} rows excluded"
    )

    e_detail_clean_mat   = np.zeros((n, DIM), dtype=np.float32)
    e_detail_clean_valid = np.zeros(n, dtype=bool)

    if n_to_embed > 0:
        log.info(f"  [workorders] Embedding {n_to_embed:,} clean descriptions via Azure OpenAI "
                 f"({EMBED_MODEL})...")
        texts_to_embed = df_meta.loc[embed_mask, "detailed_description_clean"].tolist()
        embed_indices  = df_meta.index[embed_mask].tolist()
        embeddings     = _embed_texts_batch(client, texts_to_embed)
        n_embed_ok = 0
        for global_idx, emb in zip(embed_indices, embeddings):
            if emb is not None:
                e_detail_clean_mat[global_idx]   = emb
                e_detail_clean_valid[global_idx] = True
                n_embed_ok += 1
        log.info(f"  [workorders] Clean detail embeddings: {n_embed_ok:,}/{n_to_embed:,} succeeded")

    # ── Step 4: Build problem_vec ─────────────────────────────────────────────────
    boilerplate_flags = df_meta["is_boilerplate"].values
    problem_matrix = np.zeros((n, DIM), dtype=np.float32)
    problem_valid  = np.zeros(n, dtype=bool)
    for i in range(n):
        if e_desc_valid[i] and e_detail_clean_valid[i]:
            vec = combine_embeddings(
                e_desc_mat[i], e_detail_clean_mat[i], w_primary=0.4, w_secondary=0.6
            )
        elif boilerplate_flags[i] and e_act_valid[i]:
            # Boilerplate detailed_description: use activity log embedding as problem signal
            vec = e_act_mat[i]
        elif e_desc_valid[i]:
            vec = e_desc_mat[i]  # fallback: title only (email noise stripped away)
        else:
            vec = None
        if vec is not None:
            problem_matrix[i] = vec
            problem_valid[i]  = True
    del e_desc_mat, e_detail_clean_mat

    # ── Step 5: Quality filter ────────────────────────────────────────────────────
    df_meta["res_word_count"] = df_meta["activity_logs_text"].fillna("").str.split().str.len()
    df_meta["evidence_tier"]  = df_meta["activity_logs_text"].fillna("").apply(_get_evidence_tier)
    f_act_len = df_meta["res_word_count"].values >= MIN_ACTIVITY_WORDS

    # Clean-detail guard: drop tickets where the stripped description < MIN_CLEAN_DETAIL_WORDS.
    # Boilerplate tickets are dropped outright — their activity logs are typically the same
    # templated lifecycle text (e.g. "Assign the first task..."), so the activity-log
    # fallback ends up clustering boilerplate WOs together by template, not by problem.
    f_clean_detail   = embed_mask.values
    f_not_boilerplate = ~boilerplate_flags

    # Email density guard: reject if >40% of activity_logs_text lines are email headers
    def _email_dominated(text: str) -> bool:
        if not text:
            return False
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) < 3:
            return False
        n_header = sum(1 for l in lines if _EMAIL_HEADER_RE.match(l))
        return n_header / len(lines) > 0.40

    f_not_email_dom = ~df_meta["activity_logs_text"].fillna("").apply(_email_dominated).values
    quality_mask    = (problem_valid & e_act_valid & f_act_len
                       & f_clean_detail & f_not_email_dom & f_not_boilerplate)

    n_drop_short     = int((problem_valid & e_act_valid & ~f_act_len).sum())
    n_drop_no_detail = int((problem_valid & e_act_valid & f_act_len & ~f_clean_detail).sum())
    n_drop_boiler    = int((problem_valid & e_act_valid & f_act_len & f_clean_detail & ~f_not_boilerplate).sum())
    n_drop_email     = int((problem_valid & e_act_valid & f_act_len & f_clean_detail & f_not_boilerplate & ~f_not_email_dom).sum())
    log.info(
        f"  [workorders] quality pass: {quality_mask.sum():,}/{n:,} "
        f"({quality_mask.mean()*100:.1f}%)  "
        f"dropped: {n_drop_short:,} too-short activity + "
        f"{n_drop_no_detail:,} insufficient clean description (<{MIN_CLEAN_DETAIL_WORDS} words) + "
        f"{n_drop_boiler:,} boilerplate template + "
        f"{n_drop_email:,} email-dominated"
    )

    problem_f    = normalize(problem_matrix[quality_mask], norm="l2").astype(np.float32)
    resolution_f = normalize(e_act_mat[quality_mask],     norm="l2").astype(np.float32)
    df_filtered  = df_meta[quality_mask].reset_index(drop=True)
    del problem_matrix, e_act_mat

    return df_filtered, problem_f, resolution_f


# ─────────────────────────────────────────────────────────────────────────────
# DB write
# ─────────────────────────────────────────────────────────────────────────────

def _write_to_db(engine, run_id: str, source: str, df, problem_f, resolution_f):
    """Bulk-write unified ticket embeddings using raw psycopg2 for speed."""
    from psycopg2.extras import execute_values
    import psycopg2
    url = engine.url
    conn = psycopg2.connect(
        host=str(url.host), port=url.port or 5432,
        dbname=str(url.database), user=str(url.username),
        password=str(url.password),
    )
    cur = conn.cursor()
    rows = []
    for i, row in df.iterrows():
        rows.append((
            str(uuid.uuid4()),
            source,
            str(row["id"]),
            run_id,
            problem_f[i].tolist(),
            resolution_f[i].tolist(),
            True,
            int(row.get("res_word_count", 0)),
            int(row.get("evidence_tier", 4)),
        ))
    execute_values(
        cur,
        """
        INSERT INTO ticket_embeddings
            (id, source, source_id, run_id, problem_vec, res_vec, quality_pass, res_word_count, evidence_tier)
        VALUES %s
        ON CONFLICT (source, source_id, run_id) DO UPDATE
            SET problem_vec    = EXCLUDED.problem_vec,
                res_vec        = EXCLUDED.res_vec,
                quality_pass   = EXCLUDED.quality_pass,
                res_word_count = EXCLUDED.res_word_count,
                evidence_tier  = EXCLUDED.evidence_tier
        """,
        rows,
        page_size=500,
    )
    conn.commit()
    cur.close()
    conn.close()
    log.info(f"  [{source}] {len(rows):,} rows written to ticket_embeddings ✅")


# ─────────────────────────────────────────────────────────────────────────────
# Phase entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(engine, run_id: str) -> dict:
    """Execute Phase 2. Returns stats dict. Always deletes existing embeddings for this
    run_id before rewriting — ensures stale rows from previous quality filters are removed."""
    t_start = time.time()
    log.info("── PHASE 2: Unified Preprocessing & Embedding Extraction ──")

    # Delete all existing embeddings for this run so re-runs produce a clean, consistent set.
    with engine.connect() as conn:
        deleted = conn.execute(
            text("DELETE FROM ticket_embeddings WHERE run_id = :rid"), {"rid": run_id}
        ).rowcount
        conn.commit()
    if deleted:
        log.info(f"  Cleared {deleted:,} existing ticket_embeddings rows for this run_id.")

    with engine.connect() as conn:
        inc_count = conn.execute(text("SELECT COUNT(*) FROM incidents_processed")).scalar()
        wo_count  = conn.execute(text("SELECT COUNT(*) FROM workorders_processed")).scalar()
        kb_count  = conn.execute(text("SELECT COUNT(*) FROM knowledge_base_articles")).scalar()
    log.info(f"Source rows: incidents={inc_count:,}  workorders={wo_count:,}  KB={kb_count:,}")

    # ── Azure OpenAI client (used for work order detail re-embedding) ───────────
    azure_client = AzureOpenAI(
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VER,
    )
    log.info(f"Azure OpenAI client ready — embed_model={EMBED_MODEL}")

    # ── Incidents (read pre-computed JSONB embeddings — no API calls) ───────────
    log.info("Processing incidents...")
    df_inc, prob_inc, res_inc = _load_incidents(engine)
    _write_to_db(engine, run_id, "incident", df_inc, prob_inc, res_inc)
    inc_usable = len(df_inc)

    # ── Work orders (strip email noise + re-embed detailed_description) ─────────
    log.info("Processing work orders...")
    df_wo, prob_wo, res_wo = _load_workorders(engine, azure_client)
    _write_to_db(engine, run_id, "workorder", df_wo, prob_wo, res_wo)
    wo_usable = len(df_wo)

    elapsed = time.time() - t_start
    total   = inc_usable + wo_usable
    log.info(f"✅ Phase 2 complete in {elapsed/60:.1f}m")
    log.info(f"   ticket_embeddings: {total:,} rows ({inc_usable:,} incidents + {wo_usable:,} workorders)")
    return {
        "total_incidents"  : int(inc_count),
        "usable_incidents" : int(inc_usable),
        "total_workorders" : int(wo_count),
        "usable_workorders": int(wo_usable),
        "total_embedded"   : int(total),
        "kb_articles"      : int(kb_count),
        "elapsed_s"        : round(elapsed, 1),
    }
