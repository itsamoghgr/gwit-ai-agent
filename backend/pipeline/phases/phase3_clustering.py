"""
phase3_clustering.py — Phase 3: Per-Ticket KB Coverage + Gap Clustering

New approach:
  1. Match every ticket to closest KB article (ANN)  → ticket_coverage table
  2. Cluster only UNCOVERED tickets (max_kb_sim < 0.87)
  3. Label clusters from problem text (detailed_description), not resolution text
  4. Classify gaps with absolute calibrated thresholds (0.87 / 0.90)

Reads from:  ticket_embeddings, incidents_processed, workorders_processed,
             kb_search_index (existing KB embeddings seeded by phase4 previously,
             but we also fall back to knowledge_base_articles directly)
Writes to:   ticket_coverage, clusters, cluster_assignments, cluster_sweep,
             cluster_kb_sim
"""
import json
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from openai import AzureOpenAI
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer, ENGLISH_STOP_WORDS
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize
from sqlalchemy import text as _text

from pipeline.embedding_utils import parse_jsonb_vector, combine_embeddings
from core.pipeline_logger import get_phase_logger as get_logger

# ── LLM labeling config (same env vars as phase4_1) ──────────────────────────
_AZURE_ENDPOINT      = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_AZURE_API_KEY       = os.getenv("AZURE_OPENAI_API_KEY", "")
_AZURE_API_VER       = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
_AZURE_DEPLOYMENT    = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
LLM_LABEL_RETRY      = 3
LLM_LABEL_RATE_SLEEP = 0.4
LLM_LABEL_SAMPLES    = 5     # representative ticket descriptions per cluster
LLM_LABEL_DESC_CHARS = 200   # truncate each sample to this length

log    = get_logger(__name__)
K_VALS = [10, 20, 30, 50, 75, 100, 125]

# Cluster-quality post-pass: any cluster larger than this is split via inner
# K-Means. Set to max(MAX_CLUSTER_SIZE_FLOOR, 2 × median_cluster_size) at runtime.
MAX_CLUSTER_SIZE_FLOOR = 500
SPLIT_TARGET_PER_SUBCLUSTER = 300   # inner K = ceil(size / 300)

# Absolute calibrated thresholds (from data: p25=0.885, p60=0.911)
SIM_COVERED  = 0.90   # max_kb_sim >= this  → COVERED
SIM_PARTIAL  = 0.87   # max_kb_sim >= this  → PARTIAL (below COVERED)
# max_kb_sim < SIM_PARTIAL                  → UNCOVERED (to be clustered)

GW_TERM_MAP = {
    "buff blue": "gw software license", "buff": "gw software",
    "gweb": "gw web portal", "gwid": "gw student id",
    "gworld": "gw id card", "myapps": "gw application portal",
    "gwmail": "gw email", "gwemail": "gw email",
    "sspr": "password self service reset", "aadsts": "azure login error",
    "eduroam": "gw wifi", "ithelp": "", "https gwu": "", "gwu edu": "",
}
GW_EXTRA_STOPS = {
    # URL/domain fragments
    "https", "gwu", "edu", "http", "www", "ithelp", "https gwu", "gwu edu",
    "com", "net", "org", "gov",
    # Generic action/workflow words
    "called", "calling", "assisted", "assistance", "confirmed", "checked",
    "provided", "instructions", "instruction", "helped", "resolved", "issue",
    "came", "came pick", "picked", "dropped", "customer", "user", "follow",
    "sent", "available", "answered", "answer", "asked", "ask", "contact",
    "ticket", "closed", "got", "got new", "given", "give", "need",
    "added", "add", "requested", "action", "forwarded", "forward",
    "completed", "complete", "submitted", "submit", "assigned", "assign",
    "created", "create", "opened", "open", "status", "progress",
    "started", "start", "set", "does", "non", "details", "detail",
    "task", "comment", "comments", "noted", "note",
    # Email subject/forwarded-email noise
    "subject", "email", "fwd", "fw", "re", "thank", "thanks",
    "attached", "form", "hello", "image", "dear", "hi", "please",
    "request", "regards", "number", "new", "smartit",
    # Time/date words (too generic)
    "date", "day", "days", "time", "week", "month", "year",
    # Role/org words too generic to be meaningful labels
    "role", "roles", "staff", "member", "team", "group", "department",
    "division", "office",
    # Additional generic IT noise
    "via", "per", "able", "let", "just", "know", "use", "used",
    "update", "updated", "access", "account",
    # Short meaningless abbreviations / workflow roles
    "poc",
}


def _normalize_gw_text(text: str) -> str:
    text = text.lower().replace("***", " ")
    for term, rep in sorted(GW_TERM_MAP.items(), key=lambda x: -len(x[0])):
        text = text.replace(term, rep)
    return text


def _get_db_conn(engine):
    url = engine.url
    conn = psycopg2.connect(
        host=str(url.host), port=url.port or 5432,
        dbname=str(url.database), user=str(url.username),
        password=str(url.password),
    )
    register_vector(conn)
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Load ticket embeddings with problem text for labeling
# ─────────────────────────────────────────────────────────────────────────────

def _load_ticket_embeddings(engine, run_id: str):
    """Load problem_vec + res_vec + metadata including detailed_description for labeling.

    Returns
    -------
    df          : DataFrame with metadata + problem/resolution text columns
    P           : (n, 1536) problem-vector matrix (NaN-safe; rows without a
                  problem_vec are zero with res_valid handling them)
    R           : (n, 1536) resolution-vector matrix (zero where missing)
    res_valid   : (n,) bool — True where res_vec was present in the DB row
    """
    log.info("Loading ticket embeddings (problem_vec + res_vec) + descriptions from DB...")
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    cur.execute("""
        SELECT te.id          AS embed_id,
               te.source,
               te.source_id,
               te.problem_vec,
               te.res_vec,
               ip.ticket_number,
               ip.service_type,
               ip.assigned_group,
               COALESCE(ip.detailed_description, ip.description, '') AS problem_text,
               ip.resolution_summary AS resolution_text
        FROM   ticket_embeddings te
        JOIN   incidents_processed ip ON te.source_id::text = ip.id::text
        WHERE  te.run_id = %s AND te.quality_pass = true AND te.source = 'incident'

        UNION ALL

        SELECT te.id          AS embed_id,
               te.source,
               te.source_id,
               te.problem_vec,
               te.res_vec,
               wp.ticket_number,
               wp.service_type,
               wp.assigned_group,
               COALESCE(wp.detailed_description, wp.description, '') AS problem_text,
               wp.activity_logs_text AS resolution_text
        FROM   ticket_embeddings te
        JOIN   workorders_processed wp ON te.source_id::text = wp.id::text
        WHERE  te.run_id = %s AND te.quality_pass = true AND te.source = 'workorder'

        ORDER  BY embed_id
    """, (run_id, run_id))
    rows = cur.fetchall()
    cur.close(); conn.close()

    cols = ["embed_id", "source", "source_id", "problem_vec", "res_vec",
            "ticket_number", "service_type", "assigned_group",
            "problem_text", "resolution_text"]
    df = pd.DataFrame(rows, columns=cols)
    n_inc = (df["source"] == "incident").sum()
    n_wo  = (df["source"] == "workorder").sum()
    log.info(f"  Loaded {len(df):,} ticket embeddings ({n_inc:,} incidents + {n_wo:,} workorders)")

    n   = len(df)
    DIM = 1536
    P = np.zeros((n, DIM), dtype=np.float32)
    R = np.zeros((n, DIM), dtype=np.float32)
    res_valid = np.zeros(n, dtype=bool)
    for i, (pv, rv) in enumerate(zip(df["problem_vec"].values, df["res_vec"].values)):
        if pv is not None:
            P[i] = np.asarray(pv, dtype=np.float32)
        if rv is not None:
            R[i] = np.asarray(rv, dtype=np.float32)
            res_valid[i] = True
    log.info(f"  res_vec available on {res_valid.sum():,}/{n:,} tickets "
             f"({res_valid.mean()*100:.1f}%) — others fall back to problem_vec")
    # Drop the heavy vector columns from df before returning to save memory
    df = df.drop(columns=["problem_vec", "res_vec"])
    return df, P, R, res_valid


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Load KB embeddings
# ─────────────────────────────────────────────────────────────────────────────

def _load_kb_embeddings(engine):
    """Load KB article embeddings. Prefer kb_search_index (is_generated=false),
    fall back to knowledge_base_articles if index not yet seeded."""
    log.info("Loading KB embeddings...")
    with engine.connect() as conn:
        idx_count = conn.execute(_text(
            "SELECT COUNT(*) FROM kb_search_index WHERE is_generated = false"
        )).scalar()

    if idx_count > 0:
        log.info(f"  Using kb_search_index ({idx_count} existing KB articles)")
        df_kb = pd.read_sql(
            "SELECT entry_id AS id, title, category, embedding FROM kb_search_index "
            "WHERE is_generated = false",
            engine
        )
        DIM = 1536
        n   = len(df_kb)
        kb_matrix = np.zeros((n, DIM), dtype=np.float32)
        kb_valid  = np.zeros(n, dtype=bool)
        for i, val in enumerate(df_kb["embedding"].tolist()):
            vec = parse_jsonb_vector(val) if not isinstance(val, (list, np.ndarray)) else val
            if vec is not None:
                kb_matrix[i] = np.asarray(vec, dtype=np.float32)
                kb_valid[i]  = True
    else:
        log.info("  kb_search_index empty — loading from knowledge_base_articles")
        df_kb = pd.read_sql(
            "SELECT id, title, e_title, e_solution FROM knowledge_base_articles", engine
        )
        DIM = 1536
        n   = len(df_kb)
        kb_matrix = np.zeros((n, DIM), dtype=np.float32)
        kb_valid  = np.zeros(n, dtype=bool)
        for i, (_, row) in enumerate(df_kb.iterrows()):
            e_t = parse_jsonb_vector(row["e_title"])
            e_s = parse_jsonb_vector(row["e_solution"])
            vec = combine_embeddings(e_t, e_s, 0.5, 0.5)
            if vec is not None:
                kb_matrix[i] = vec
                kb_valid[i]  = True

    kb_clean   = normalize(kb_matrix[kb_valid], norm="l2").astype(np.float32)
    df_kb_meta = df_kb[kb_valid].reset_index(drop=True)
    log.info(f"  KB matrix: {kb_clean.shape}")
    return kb_clean, df_kb_meta


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Per-ticket ANN coverage matching
# ─────────────────────────────────────────────────────────────────────────────

def _compute_ticket_coverage(X_norm, kb_emb, df, batch_size=5000):
    """For each ticket, compute max cosine similarity to any KB article.

    Returns coverage Series: 'COVERED' | 'PARTIAL' | 'UNCOVERED'
    and max_kb_sim array (float32).
    """
    n_tickets = len(X_norm)
    n_kb      = len(kb_emb)
    max_sims  = np.zeros(n_tickets, dtype=np.float32)
    best_idxs = np.zeros(n_tickets, dtype=np.int32)

    log.info(f"  Computing per-ticket KB coverage ({n_tickets:,} tickets × {n_kb} KB articles)...")
    for start in range(0, n_tickets, batch_size):
        end    = min(start + batch_size, n_tickets)
        batch  = X_norm[start:end]          # (B, 1536)
        sims   = batch @ kb_emb.T           # (B, n_kb)
        max_sims[start:end]  = sims.max(axis=1)
        best_idxs[start:end] = sims.argmax(axis=1)
        if (start // batch_size) % 5 == 0:
            log.info(f"    coverage: {end:,}/{n_tickets:,}")

    coverage = np.where(
        max_sims >= SIM_COVERED, "COVERED",
        np.where(max_sims >= SIM_PARTIAL, "PARTIAL", "UNCOVERED")
    )
    return coverage, max_sims, best_idxs


def _write_ticket_coverage(engine, run_id, df, coverage, max_sims, best_idxs, kb_meta):
    """Write per-ticket coverage to ticket_coverage table."""
    log.info("Writing ticket_coverage → DB...")
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    rows = []
    for i, (_, row) in enumerate(df.iterrows()):
        best_idx   = int(best_idxs[i])
        best_kb_id = str(kb_meta["id"].iloc[best_idx]) if best_idx < len(kb_meta) else None
        best_title = str(kb_meta["title"].iloc[best_idx])[:500] if best_idx < len(kb_meta) else None
        rows.append((
            str(uuid.uuid4()),
            run_id,
            str(row["source"]),
            str(row["source_id"]),
            float(max_sims[i]),
            best_kb_id,
            best_title,
            str(coverage[i]),
        ))
    execute_values(cur, """
        INSERT INTO ticket_coverage
            (id, run_id, source, source_id, max_kb_sim, best_kb_id, best_kb_title, coverage)
        VALUES %s
        ON CONFLICT (source, source_id, run_id) DO UPDATE
            SET max_kb_sim    = EXCLUDED.max_kb_sim,
                best_kb_id    = EXCLUDED.best_kb_id,
                best_kb_title = EXCLUDED.best_kb_title,
                coverage      = EXCLUDED.coverage
    """, rows, page_size=2000)
    conn.commit(); cur.close(); conn.close()
    counts = Counter(coverage)
    log.info(f"  ticket_coverage: COVERED={counts['COVERED']:,}  "
             f"PARTIAL={counts['PARTIAL']:,}  UNCOVERED={counts['UNCOVERED']:,}")
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Cluster only UNCOVERED tickets
# ─────────────────────────────────────────────────────────────────────────────

def _split_oversize_clusters(X, cluster_ids, max_size):
    """Split any cluster larger than `max_size` via inner K-Means at K=ceil(size/300).

    Runs at most one pass (i.e. sub-clusters are not themselves re-split). After
    splitting, cluster IDs are renumbered to a contiguous 0..K'-1 range so all
    downstream code that iterates `range(best_k)` continues to work.

    Returns
    -------
    new_ids : np.ndarray of shape (n,) — contiguous renumbered cluster IDs
    new_k   : int — new total number of clusters
    """
    cluster_ids = np.asarray(cluster_ids).copy()
    sizes = np.bincount(cluster_ids)
    oversize = np.where(sizes > max_size)[0]
    if len(oversize) == 0:
        log.info(f"  cluster-size guard: no clusters > {max_size} — no splits needed")
        return cluster_ids, int(cluster_ids.max()) + 1

    log.info(
        f"  cluster-size guard: splitting {len(oversize)} oversize cluster(s) "
        f"(max_size={max_size}): {[int(c) for c in oversize]} "
        f"→ sizes {[int(sizes[c]) for c in oversize]}"
    )

    next_id = int(cluster_ids.max()) + 1
    for cid in oversize:
        member_idx = np.where(cluster_ids == cid)[0]
        size = len(member_idx)
        inner_k = max(2, int(np.ceil(size / SPLIT_TARGET_PER_SUBCLUSTER)))
        log.info(f"    cluster {int(cid)} (size={size}) → splitting into {inner_k} sub-clusters")
        sub_km = MiniBatchKMeans(
            n_clusters=inner_k, random_state=42,
            batch_size=min(4096, size), n_init=5, max_iter=200,
        )
        sub_labels = sub_km.fit_predict(X[member_idx])
        # First sub-cluster keeps the original cid; the rest get new IDs.
        for sub in range(inner_k):
            sub_mask = sub_labels == sub
            if sub == 0:
                continue   # keep cid for sub 0
            cluster_ids[member_idx[sub_mask]] = next_id
            next_id += 1

    # Renumber to a contiguous 0..K'-1 range
    unique_ids, new_ids = np.unique(cluster_ids, return_inverse=True)
    new_k = len(unique_ids)
    log.info(f"  cluster-size guard: total clusters {int(sizes.size)} → {new_k} after splits")
    return new_ids.astype(np.int32), new_k


def _sweep_and_cluster(X_uncov):
    """K-sweep + final K-Means on UNCOVERED tickets only.

    K is selected by a composite score: silhouette − 2·max(0, max_cluster_share − 0.10).
    The penalty kicks in only when a single cluster holds >10% of tickets, which
    catches K values that produce massive catch-all clusters with good silhouette.
    """
    log.info(f"Sweeping K ∈ {K_VALS} on {len(X_uncov):,} UNCOVERED tickets...")
    n_total = len(X_uncov)
    inertias, silhouettes, max_shares = [], [], []
    for k in K_VALS:
        if k >= n_total:
            log.info(f"  K={k} >= n_samples — skipping")
            inertias.append(float("inf")); silhouettes.append(-1.0); max_shares.append(1.0)
            continue
        t_k = time.time()
        km  = MiniBatchKMeans(n_clusters=k, random_state=42,
                              batch_size=4096, n_init=5, max_iter=300)
        labels = km.fit_predict(X_uncov)
        sil    = silhouette_score(X_uncov, labels, sample_size=min(10000, n_total))
        sizes  = np.bincount(labels, minlength=k)
        share  = float(sizes.max()) / float(n_total)
        inertias.append(km.inertia_); silhouettes.append(sil); max_shares.append(share)
        log.info(
            f"  K={k:4d}  inertia={km.inertia_:>10,.1f}  sil={sil:.4f}  "
            f"max_share={share:.3f}  [{time.time()-t_k:.1f}s]"
        )

    composite = [
        s - 2.0 * max(0.0, share - 0.10)
        for s, share in zip(silhouettes, max_shares)
    ]
    best_idx = int(np.argmax(composite))
    best_k   = K_VALS[best_idx]
    log.info(
        f"Auto-selected BEST_K = {best_k}  "
        f"(composite={composite[best_idx]:.4f}, "
        f"silhouette={silhouettes[best_idx]:.4f}, "
        f"max_share={max_shares[best_idx]:.3f})"
    )

    log.info(f"Fitting final K-Means (K={best_k}, n_init=15, max_iter=500)...")
    t_km    = time.time()
    km_fin  = MiniBatchKMeans(n_clusters=best_k, random_state=42,
                              batch_size=4096, n_init=15, max_iter=500)
    labels  = km_fin.fit_predict(X_uncov)
    log.info(f"  Done in {time.time()-t_km:.1f}s")
    return km_fin, labels, best_k, K_VALS, inertias, silhouettes


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Label clusters from problem text (not resolution)
# ─────────────────────────────────────────────────────────────────────────────

def _label_clusters_from_problem(df_uncov, cluster_ids, best_k):
    """TF-IDF on problem_text + resolution_text combined.

    We cluster on the blended problem+resolution vector, so labels should draw
    from the same signal. Resolution text is often the clearest description of
    what the ticket was actually about (technician's own words), so blending it
    in produces much more accurate TF-IDF terms than problem text alone.
    """
    log.info("Labeling clusters with TF-IDF on problem + resolution text...")
    combined_stops = list(ENGLISH_STOP_WORDS | GW_EXTRA_STOPS)

    def _combined(row_problem, row_resolution):
        p = str(row_problem or "").strip()
        r = str(row_resolution or "").strip()
        if p and r:
            return f"{p}  {r}"
        return p or r

    df_uncov = df_uncov.copy()
    df_uncov["_label_text"] = [
        _combined(p, r)
        for p, r in zip(df_uncov["problem_text"].tolist(),
                        df_uncov["resolution_text"].tolist())
    ]
    all_texts = [_normalize_gw_text(s)
                 for s in df_uncov["_label_text"].fillna("").tolist()]
    global_vec = TfidfVectorizer(
        stop_words=combined_stops, ngram_range=(1, 2), max_features=5000,
        min_df=5, max_df=0.80, sublinear_tf=True, token_pattern=r"[a-zA-Z]{3,}",
    )
    global_vec.fit(all_texts)
    log.info(f"  Vocabulary size: {len(global_vec.vocabulary_):,}")

    cluster_labels    = {}
    cluster_top_terms = {}
    for cid in range(best_k):
        mask  = cluster_ids == cid
        texts = [_normalize_gw_text(s)
                 for s in df_uncov.loc[mask, "_label_text"].fillna("").tolist()]
        if not texts:
            cluster_labels[cid] = f"Cluster_{cid}"; cluster_top_terms[cid] = ""; continue
        try:
            tfidf_m  = global_vec.transform(texts)
            mean_t   = tfidf_m.mean(axis=0).A1
            top_idx  = mean_t.argsort()[::-1][:20]
            terms    = [t for t in global_vec.get_feature_names_out()[top_idx].tolist() if t.strip()]
        except Exception:
            terms = []
        # Deduplicate: remove terms whose words are all already covered by an earlier term
        deduped = []
        seen_words = set()
        for term in terms:
            words = set(term.split())
            if not words.issubset(seen_words):
                deduped.append(term)
                seen_words.update(words)
            if len(deduped) >= 10:
                break
        cluster_labels[cid]    = " / ".join(deduped[:3]) if deduped else f"Cluster_{cid}"
        cluster_top_terms[cid] = ", ".join(deduped[:10])
    return cluster_labels, cluster_top_terms


def _llm_label_clusters(df_uncov, cluster_ids, cluster_labels, cluster_top_terms, best_k):
    """
    Upgrade TF-IDF cluster labels to human-readable GPT-4.1 labels.
    Falls back to TF-IDF label on any failure.
    """
    if not _AZURE_ENDPOINT or not _AZURE_API_KEY:
        log.warning("Azure OpenAI not configured — skipping LLM labeling, using TF-IDF labels.")
        return dict(cluster_labels)

    client = AzureOpenAI(
        azure_endpoint=_AZURE_ENDPOINT,
        api_key=_AZURE_API_KEY,
        api_version=_AZURE_API_VER,
    )
    updated = dict(cluster_labels)   # start with TF-IDF as fallback

    log.info(f"LLM-labeling {best_k} clusters with GPT-4.1...")
    for cid in range(best_k):
        mask       = cluster_ids == cid
        cluster_df = df_uncov[mask]
        size       = int(mask.sum())
        top_terms  = cluster_top_terms.get(cid, "")

        sample_df = cluster_df.sample(
            n=min(LLM_LABEL_SAMPLES, len(cluster_df)), random_state=42
        )
        samples = []
        for _, srow in sample_df.iterrows():
            p = str(srow.get("problem_text") or "").replace("\n", " ").strip()
            r = str(srow.get("resolution_text") or "").replace("\n", " ").strip()
            if not p and not r:
                continue
            half = LLM_LABEL_DESC_CHARS // 2
            block = ""
            if p:
                block += f"Problem: {p[:half]}"
            if r:
                if block:
                    block += "  "
                block += f"Resolution: {r[:half]}"
            samples.append(block)
        if not samples:
            continue

        sample_block = "\n".join(f"{i+1}. {s}" for i, s in enumerate(samples))
        prompt = (
            "You are an IT support analyst at George Washington University.\n"
            "Below is a cluster of similar IT helpdesk tickets. Each sample shows\n"
            "the user's Problem description and the technician's Resolution notes.\n\n"
            f"Cluster size: {size} tickets\n"
            f"Top TF-IDF terms: {top_terms}\n\n"
            "Sample tickets:\n"
            f"{sample_block}\n\n"
            "Write a concise, human-readable label for this cluster. Use BOTH the\n"
            "problem and resolution text to decide what these tickets are really about.\n"
            "Rules:\n"
            "- 3 to 6 words maximum\n"
            "- Title Case (e.g. 'VPN Access Issues', 'Password Reset Requests')\n"
            "- Describe the IT problem type\n"
            "- Avoid generic filler like 'Issue' or 'Request' unless truly needed\n"
            "- Do NOT include university or department names\n\n"
            'Respond with ONLY valid JSON: {"label": "Your Label Here"}'
        )

        for attempt in range(LLM_LABEL_RETRY):
            try:
                resp = client.chat.completions.create(
                    model=_AZURE_DEPLOYMENT,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=40,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                raw   = resp.choices[0].message.content.strip()
                label = str(json.loads(raw).get("label", "")).strip()
                if 2 <= len(label.split()) <= 8:
                    log.info(f"  [cluster {cid}] '{label}'  (was: '{cluster_labels[cid]}')")
                    updated[cid] = label
                    break
                else:
                    log.warning(f"  [cluster {cid}] LLM returned bad label length: '{label}'")
            except json.JSONDecodeError as e:
                log.warning(f"  [cluster {cid}] JSON parse error attempt {attempt+1}: {e}")
            except Exception as exc:
                wait = 2 ** attempt
                if attempt < LLM_LABEL_RETRY - 1:
                    log.warning(f"  [cluster {cid}] API error attempt {attempt+1}: {exc} — retry in {wait}s")
                    time.sleep(wait)
                else:
                    log.error(f"  [cluster {cid}] LLM labeling failed — using TF-IDF fallback")

        time.sleep(LLM_LABEL_RATE_SLEEP)

    return updated


# ─────────────────────────────────────────────────────────────────────────────
# DB write helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_sweep_to_db(engine, run_id, K_vals, inertias, silhouettes, best_k):
    rows = [
        {"run_id": run_id, "k": int(k), "inertia": round(float(i), 1),
         "silhouette": round(float(s), 6), "is_best_k": (k == best_k)}
        for k, i, s in zip(K_vals, inertias, silhouettes)
    ]
    with engine.connect() as conn:
        for row in rows:
            conn.execute(_text("""
                INSERT INTO cluster_sweep (id, run_id, k, inertia, silhouette, is_best_k)
                VALUES (gen_random_uuid(), :run_id, :k, :inertia, :silhouette, :is_best_k)
                ON CONFLICT (run_id, k) DO UPDATE
                SET inertia=EXCLUDED.inertia, silhouette=EXCLUDED.silhouette,
                    is_best_k=EXCLUDED.is_best_k
            """), row)
        conn.commit()
    log.info(f"  Sweep written to cluster_sweep ({len(rows)} rows).")


def _write_clusters_to_db(engine, run_id, summary_rows, centroids, sil_global, pca_xy=None):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    rows = []
    for r in summary_rows:
        cid  = int(r["cluster_id"])
        px, py = pca_xy.get(cid, (None, None)) if pca_xy else (None, None)
        rows.append((
            str(uuid.uuid4()), cid, run_id,
            str(r["cluster_label"]), str(r.get("top_terms", "")),
            int(r["size"]),
            centroids[cid].tolist(),
            float(r["max_kb_sim"]),
            float(SIM_PARTIAL), float(SIM_COVERED),
            str(r["gap_flag"]),
            float(sil_global) if sil_global is not None else None,
            px, py,
            float(r["intra_sim"]) if r.get("intra_sim") is not None else None,
        ))
    execute_values(cur, """
        INSERT INTO clusters
            (id, cluster_id, run_id, cluster_label, top_terms, size, centroid,
             max_kb_sim, threshold_p25, threshold_p60, gap_flag, silhouette_score,
             pca_x, pca_y, intra_sim)
        VALUES %s
        ON CONFLICT (cluster_id, run_id) DO UPDATE
            SET cluster_label=EXCLUDED.cluster_label, size=EXCLUDED.size,
                centroid=EXCLUDED.centroid, gap_flag=EXCLUDED.gap_flag,
                max_kb_sim=EXCLUDED.max_kb_sim,
                pca_x=EXCLUDED.pca_x, pca_y=EXCLUDED.pca_y,
                intra_sim=EXCLUDED.intra_sim
    """, rows, page_size=200)
    conn.commit(); cur.close(); conn.close()


def _write_assignments_to_db(engine, run_id, df):
    conn = _get_db_conn(engine)
    cur  = conn.cursor()
    rows = [
        (str(uuid.uuid4()), str(r["source"]), str(r["source_id"]), run_id,
         int(r["cluster_id"]), str(r.get("cluster_label", "")),
         str(r.get("service_type", "")), str(r.get("assigned_group", "")),
         str(r.get("ticket_number", "")))
        for _, r in df.iterrows()
    ]
    execute_values(cur, """
        INSERT INTO cluster_assignments
            (id, source, source_id, run_id, cluster_id, cluster_label,
             service_type, assigned_group, ticket_number)
        VALUES %s
        ON CONFLICT (source, source_id, run_id) DO UPDATE
            SET cluster_id=EXCLUDED.cluster_id, cluster_label=EXCLUDED.cluster_label
    """, rows, page_size=1000)
    conn.commit(); cur.close(); conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(engine, run_id: str, best_k: Optional[int] = None) -> dict:
    t_start = time.time()
    log.info("── PHASE 3: Per-Ticket KB Coverage + Gap Clustering ──")

    # 0. Purge stale data for this run_id so re-runs produce a clean, consistent result.
    #    Without this, old clusters (from a previous K value) remain alongside new ones.
    with engine.connect() as conn:
        for table in ("cluster_assignments", "cluster_kb_sim", "cluster_sweep",
                      "clusters", "ticket_coverage"):
            deleted = conn.execute(
                _text(f"DELETE FROM {table} WHERE run_id = :rid"), {"rid": run_id}
            ).rowcount
            if deleted:
                log.info(f"  Cleared {deleted:,} stale rows from {table}.")
        conn.commit()

    # 1. Load all ticket embeddings (problem + resolution side-by-side)
    df, P, R, res_valid = _load_ticket_embeddings(engine, run_id)

    # Build the two working vectors (see plan section C):
    #   X_cluster = normalize(0.5·P + 0.5·R)  — fed to K-Means; fall back to P alone
    #               where res_vec is missing so the ticket still clusters.
    #   X_kb      = normalize(R)              — used for all KB similarity comparisons
    #               (per-ticket + per-cluster) so we compare resolution↔resolution
    #               with KB articles. Falls back to P where res_vec is missing.
    X_cluster = np.zeros_like(P)
    X_cluster[res_valid]  = 0.5 * P[res_valid] + 0.5 * R[res_valid]
    X_cluster[~res_valid] = P[~res_valid]
    X_cluster = normalize(X_cluster, norm="l2").astype(np.float32)

    X_kb = np.zeros_like(P)
    X_kb[res_valid]  = R[res_valid]
    X_kb[~res_valid] = P[~res_valid]
    X_kb = normalize(X_kb, norm="l2").astype(np.float32)
    del P, R

    # 2. Load KB embeddings
    kb_emb, kb_meta = _load_kb_embeddings(engine)
    Xkb = normalize(kb_emb, norm="l2").astype(np.float32)

    # 3. Per-ticket coverage matching — compare resolution-side ticket vector
    #    against KB articles (KBs are written in resolution language).
    coverage, max_sims, best_idxs = _compute_ticket_coverage(X_kb, Xkb, df)
    df = df.copy()
    df["coverage"]   = coverage
    df["max_kb_sim"] = max_sims
    coverage_counts  = _write_ticket_coverage(engine, run_id, df, coverage, max_sims, best_idxs, kb_meta)

    # 4. Cluster ALL tickets on the blended vector (gap flag assigned at cluster
    #    level from a separate resolution-side centroid built below).
    df_uncov = df.reset_index(drop=True)
    X_uncov  = X_cluster
    log.info(f"Clustering all {len(df_uncov):,} tickets...")

    if len(df_uncov) < 20:
        log.warning("Too few UNCOVERED tickets to cluster — skipping clustering.")
        return {"best_k": 0, "gap_counts": dict(coverage_counts), "elapsed_s": round(time.time()-t_start, 1)}

    if best_k is None:
        km_final, cluster_ids, best_k, K_vals, inertias, silhouettes = _sweep_and_cluster(X_uncov)
        _write_sweep_to_db(engine, run_id, K_vals, inertias, silhouettes, best_k)
    else:
        log.info(f"Using provided BEST_K = {best_k}")
        km_final = MiniBatchKMeans(n_clusters=best_k, random_state=42,
                                   batch_size=4096, n_init=15, max_iter=500)
        cluster_ids = km_final.fit_predict(X_uncov)

    # ── Cluster-quality post-pass: split oversize "catch-all" clusters ──────────
    # Threshold = max(MAX_CLUSTER_SIZE_FLOOR, 2 × median cluster size). Splitting
    # invalidates km_final.cluster_centers_ for affected clusters, so we recompute
    # all centroids from the underlying data after the split.
    sizes_pre = np.bincount(cluster_ids, minlength=best_k)
    max_size  = max(MAX_CLUSTER_SIZE_FLOOR, 2 * int(np.median(sizes_pre)))
    cluster_ids, best_k = _split_oversize_clusters(X_uncov, cluster_ids, max_size)

    # Recompute centroids in the clustering space from data (not from km_final).
    centroids = np.zeros((best_k, X_uncov.shape[1]), dtype=np.float32)
    for cid in range(best_k):
        mask = cluster_ids == cid
        if mask.any():
            centroids[cid] = X_uncov[mask].mean(axis=0)
    centroids_norm = normalize(centroids, norm="l2").astype(np.float32)
    df_uncov = df_uncov.copy()
    df_uncov["cluster_id"] = cluster_ids

    # ── Resolution-side centroid (for KB matching) ──────────────────────────────
    # The K-Means centroid above lives in the blended (problem+resolution) space
    # and is used for visualisation + intra_sim. KB matching uses a separate
    # centroid built from X_kb so we compare resolution↔resolution with KBs.
    kb_centroids = np.zeros((best_k, X_kb.shape[1]), dtype=np.float32)
    for cid in range(best_k):
        mask = cluster_ids == cid
        if mask.any():
            kb_centroids[cid] = X_kb[mask].mean(axis=0)
    kb_centroids = normalize(kb_centroids, norm="l2").astype(np.float32)

    # ── Intra-cluster cosine similarity (cluster-quality metric) ────────────────
    # mean(X_cluster[cluster] · centroid_norm) — both unit-norm so this is cosine.
    intra_sim_per_cluster = np.zeros(best_k, dtype=np.float32)
    for cid in range(best_k):
        mask = cluster_ids == cid
        if mask.any():
            intra_sim_per_cluster[cid] = float(
                (X_cluster[mask] @ centroids_norm[cid]).mean()
            )
    log.info(
        f"  intra_sim: median={float(np.median(intra_sim_per_cluster)):.3f}  "
        f"min={float(intra_sim_per_cluster.min()):.3f}  "
        f"low-coherence (<0.55)={int((intra_sim_per_cluster < 0.55).sum())}"
    )

    # 5. Label from problem text (detailed_description) — TF-IDF base labels
    cluster_labels, cluster_top_terms = _label_clusters_from_problem(df_uncov, cluster_ids, best_k)

    # 5b. Upgrade to human-readable GPT-4.1 labels (falls back to TF-IDF on failure)
    cluster_labels = _llm_label_clusters(
        df_uncov, cluster_ids, cluster_labels, cluster_top_terms, best_k
    )

    df_uncov["cluster_label"] = df_uncov["cluster_id"].map(cluster_labels)

    # 6. KB similarity per cluster centroid (for gap_analysis phase)
    #    Uses the resolution-side centroid (kb_centroids) so we compare
    #    resolution-language ↔ resolution-language KB articles. Avoids the
    #    language-mismatch inflation of CRITICAL counts.
    log.info("Computing resolution-side centroid → KB similarity...")
    sim_matrix = kb_centroids @ Xkb.T     # (best_k, n_kb)
    max_kb_sim = sim_matrix.max(axis=1)

    def _classify_gap(s):
        if s >= SIM_COVERED: return "COVERED"
        if s >= SIM_PARTIAL:  return "PARTIAL"
        return "CRITICAL"

    # 7. Build cluster summary rows
    summary_rows = []
    for cid in range(best_k):
        mask     = cluster_ids == cid
        size     = int(mask.sum())
        kb_sims  = sim_matrix[cid]
        best_kb  = int(kb_sims.argmax())
        gap      = _classify_gap(float(kb_sims.max()))
        summary_rows.append({
            "cluster_id":    cid,
            "cluster_label": cluster_labels[cid],
            "size":          size,
            "top_terms":     cluster_top_terms[cid],
            "max_kb_sim":    round(float(kb_sims.max()), 4),
            "best_kb_id":    best_kb,
            "best_kb_title": kb_meta["title"].iloc[best_kb] if best_kb < len(kb_meta) else "",
            "gap_flag":      gap,
            "intra_sim":     round(float(intra_sim_per_cluster[cid]), 4),
        })

    gap_counts = Counter(r["gap_flag"] for r in summary_rows)
    log.info(f"  CRITICAL={gap_counts['CRITICAL']}  PARTIAL={gap_counts['PARTIAL']}  COVERED={gap_counts['COVERED']}")

    # 8. PCA of centroids for dashboard
    log.info("Computing PCA projection of centroids...")
    pca           = PCA(n_components=2, random_state=42)
    pca_centroids = pca.fit_transform(centroids_norm)
    log.info(f"  PCA variance explained: {pca.explained_variance_ratio_.sum():.3f}")
    pca_xy = {cid: (float(px), float(py)) for cid, (px, py) in enumerate(pca_centroids)}

    # 9. Persist clusters and assignments FIRST (critical tables — write before slow sim matrix)
    log.info("Writing clusters → DB...")
    sil_global = silhouette_score(X_uncov, cluster_ids,
                                  sample_size=min(10000, len(X_uncov))) if len(X_uncov) >= best_k + 1 else None
    _write_clusters_to_db(engine, run_id, summary_rows, centroids, sil_global, pca_xy)

    log.info("Writing cluster_assignments → DB...")
    _write_assignments_to_db(engine, run_id, df_uncov)

    # 10. Write cluster_kb_sim (full similarity matrix) — bulk insert via execute_values
    log.info("Writing cluster_kb_sim → DB...")
    kb_titles = kb_meta["title"].tolist()
    sim_tuples = [
        (
            run_id,
            int(cid),
            int(kb_idx),
            (kb_titles[kb_idx][:300] if kb_idx < len(kb_titles) else ""),
            round(float(sim_matrix[cid, kb_idx]), 6),
        )
        for cid in range(best_k)
        for kb_idx in range(sim_matrix.shape[1])
    ]
    raw_conn = engine.raw_connection()
    try:
        from psycopg2.extras import execute_values
        with raw_conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO cluster_kb_sim (id, run_id, cluster_id, kb_idx, kb_title, similarity)
                VALUES %s
                ON CONFLICT (run_id, cluster_id, kb_idx) DO UPDATE
                    SET similarity=EXCLUDED.similarity, kb_title=EXCLUDED.kb_title
                """,
                sim_tuples,
                template="(gen_random_uuid(), %s, %s, %s, %s, %s)",
                page_size=500,
            )
        raw_conn.commit()
    finally:
        raw_conn.close()
    log.info(f"  cluster_kb_sim: {len(sim_tuples):,} rows written.")

    elapsed = time.time() - t_start
    log.info(f"✅ Phase 3 complete in {elapsed/60:.1f}m")
    return {
        "best_k":          best_k,
        "gap_counts":      dict(gap_counts),
        "coverage_counts": {k: int(v) for k, v in coverage_counts.items()},
        "elapsed_s":       round(elapsed, 1),
    }
