"use client";

import { useEffect, useState } from "react";
import { useRun } from "@/lib/RunContext";
import StatCard from "@/components/StatCard";
import { FileText, ChevronDown, ChevronRight, ShieldCheck, AlertTriangle, Copy, Check, Pencil, X, Save } from "lucide-react";
import type { KBArticle } from "@/lib/types";

const HIDDEN_CLUSTER_IDS = new Set<number>([69, 112, 170]);

function parseList(raw: string[] | string | null | undefined): string[] {
  if (!raw) return [];
  // Backend now returns string[] directly — fast path.
  if (Array.isArray(raw)) return raw.filter(Boolean);
  // Defensive fallback: parse a valid JSON array string (no brittle quote-swapping).
  const s = raw.trim();
  try {
    const p = JSON.parse(s);
    if (Array.isArray(p)) return p.map(String).filter(Boolean);
  } catch {}
  // Last resort: newline-split plain text.
  return s.split("\n").map(l => l.trim()).filter(Boolean);
}


/** Split additional_notes on '---' to extract the contact block */
function parseNotes(raw: string | null) {
  if (!raw) return { notes: "", contact: [] as string[] };
  const sepIdx = raw.indexOf("---");
  if (sepIdx === -1) return { notes: raw.trim(), contact: [] as string[] };
  const notes   = raw.slice(0, sepIdx).trim();
  const contact = raw.slice(sepIdx + 3).trim()
    .split("\u2022")
    .map(s => s.trim())
    .filter(s => s && !s.toLowerCase().includes("need more help"));
  return { notes, contact };
}

/** Auto-link emails, phones, https URLs and bare *.gwu.edu domains — single pass */
function linkify(text: string): string {
  // Strip any pre-existing HTML so we start from clean plain text
  const plain = text.replace(/<[^>]+>/g, "");
  return plain.replace(
    /([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})|\b(https?:\/\/[^\s<>"]+)|\b(\d{3}-\d{3}-\d{4})\b|\b([a-zA-Z0-9-]+\.gwu\.edu[^\s<>",.]*)/g,
    (_match, email, url, phone, domain) => {
      if (email)  return `<a href="mailto:${email}" class="link link-primary">${email}</a>`;
      if (url)    return `<a href="${url}" target="_blank" rel="noopener" class="link link-primary">${url}</a>`;
      if (phone)  return `<a href="tel:${phone}" class="link link-primary">${phone}</a>`;
      if (domain) return `<a href="https://${domain}" target="_blank" rel="noopener" class="link link-primary">${domain}</a>`;
      return _match;
    }
  );
}

function articleToText(a: KBArticle): string {
  const out: string[] = [];
  out.push(a.title);
  out.push("");
  out.push(`Category: ${a.category}`);
  out.push(`Cluster: ${a.cluster_id}`);
  out.push("");
  if (a.problem_statement) { out.push("Problem Statement"); out.push(a.problem_statement); out.push(""); }
  const syms = parseList(a.symptoms);
  if (syms.length) {
    out.push("Symptoms");
    syms.forEach(s => out.push(`- ${s}`));
    out.push("");
  }
  const steps = parseList(a.resolution_steps);
  if (steps.length) {
    out.push("Resolution Steps");
    steps.forEach((s, i) => out.push(`${i + 1}. ${s}`));
    out.push("");
  }
  const { notes, contact } = parseNotes(a.additional_notes);
  if (notes) { out.push("Additional Notes"); out.push(notes); out.push(""); }
  if (contact.length) {
    out.push("Need more help? Contact GW IT:");
    contact.forEach(c => out.push(`- ${c.replace(/<[^>]+>/g, "")}`));
  }
  return out.join("\n").trim();
}

type EditablePatch = {
  title: string;
  problem_statement: string;
  symptoms: string[];
  resolution_steps: string[];
  additional_notes: string;
};

function ArticleBody({
  article,
  runId,
  onSaved,
}: {
  article: KBArticle;
  runId: string;
  onSaved: (patch: Partial<KBArticle>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [copied, setCopied]   = useState(false);
  const [err, setErr]         = useState<string | null>(null);

  // Preserve the contact block (after ---) so edits don't clobber it.
  const { notes: initNotes, contact } = parseNotes(article.additional_notes);
  const [draft, setDraft] = useState<EditablePatch>({
    title:             article.title || "",
    problem_statement: article.problem_statement || "",
    symptoms:          parseList(article.symptoms),
    resolution_steps:  parseList(article.resolution_steps),
    additional_notes:  initNotes,
  });

  function startEdit() {
    setErr(null);
    setDraft({
      title:             article.title || "",
      problem_statement: article.problem_statement || "",
      symptoms:          parseList(article.symptoms),
      resolution_steps:  parseList(article.resolution_steps),
      additional_notes:  initNotes,
    });
    setEditing(true);
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(articleToText(article));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }

  async function handleSave() {
    setSaving(true);
    setErr(null);

    // Re-attach the contact block so the footer is preserved.
    const contactBlock = contact.length
      ? "\n---\n" + contact.map(c => "• " + c.replace(/<[^>]+>/g, "")).join("\n")
      : "";
    const newAdditionalNotes = draft.additional_notes.trim() + contactBlock;

    const payload = {
      title:             draft.title,
      problem_statement: draft.problem_statement,
      symptoms:          draft.symptoms.filter(s => s.trim()),
      resolution_steps:  draft.resolution_steps.filter(s => s.trim()),
      additional_notes:  newAdditionalNotes,
    };

    try {
      const res = await fetch(`/api/kb-articles/${runId}/${article.cluster_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onSaved({
        title:             payload.title,
        problem_statement: payload.problem_statement,
        symptoms:          payload.symptoms,
        resolution_steps:  payload.resolution_steps,
        additional_notes:  payload.additional_notes,
      });
      setEditing(false);
    } catch (e: any) {
      setErr(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    const syms  = parseList(article.symptoms);
    const steps = parseList(article.resolution_steps);
    return (
      <div className="px-6 pb-5 pt-4 border-t border-base-200 bg-base-100 space-y-5 text-sm">
        <div className="flex items-center gap-2 -mt-1">
          <button
            onClick={handleCopy}
            className="text-[11px] flex items-center gap-1 px-2 py-1 rounded border border-base-300 text-base-content/60 hover:text-base-content hover:bg-base-200"
          >
            {copied ? <Check size={11} /> : <Copy size={11} />}
            {copied ? "Copied" : "Copy"}
          </button>
          <button
            onClick={startEdit}
            className="text-[11px] flex items-center gap-1 px-2 py-1 rounded border border-base-300 text-base-content/60 hover:text-base-content hover:bg-base-200"
          >
            <Pencil size={11} /> Edit
          </button>
        </div>

        {article.problem_statement && (
          <div>
            <h3 className="font-bold text-base text-base-content mb-1.5">Problem Statement</h3>
            <p className="text-base-content leading-relaxed" dangerouslySetInnerHTML={{ __html: linkify(article.problem_statement) }} />
          </div>
        )}
        {syms.length > 0 && (
          <div>
            <h3 className="font-bold text-base text-base-content mb-1.5">Symptoms</h3>
            <ul className="list-disc list-outside pl-5 space-y-1">
              {syms.map((s, i) => <li key={i} className="text-base-content leading-relaxed" dangerouslySetInnerHTML={{ __html: linkify(s) }} />)}
            </ul>
          </div>
        )}
        {steps.length > 0 && (
          <div>
            <h3 className="font-bold text-base text-base-content mb-1.5">Resolution Steps</h3>
            <ol className="list-decimal list-outside pl-5 space-y-2">
              {steps.map((s, i) => <li key={i} className="text-base-content leading-relaxed" dangerouslySetInnerHTML={{ __html: linkify(s) }} />)}
            </ol>
          </div>
        )}
        {(initNotes || contact.length > 0) && (
          <div>
            {initNotes && (
              <>
                <h3 className="font-bold text-base text-base-content mb-1.5">Additional Notes</h3>
                <p className="text-base-content leading-relaxed mb-4" dangerouslySetInnerHTML={{ __html: linkify(initNotes) }} />
              </>
            )}
            {contact.length > 0 && (
              <div className="bg-base-200 border border-base-300 rounded-lg px-4 py-3">
                <p className="text-xs font-semibold text-base-content/50 mb-2">Need more help? Contact GW IT:</p>
                <ul className="space-y-1">
                  {contact.map((item, i) => (
                    <li key={i} className="text-sm text-base-content flex gap-1.5">
                      <span className="text-base-content/30">&bull;</span>
                      <span dangerouslySetInnerHTML={{ __html: linkify(item) }} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        <div className="pt-3 border-t border-base-200 flex items-center gap-3 text-xs text-base-content/30">
          <span>Cluster {article.cluster_id}</span>
          {article.wo_in_cluster > 0 && <span>· {article.wo_in_cluster} Work Orders</span>}
          {article.is_duplicate_of && <span>· Duplicate of: {article.is_duplicate_of}</span>}
        </div>
      </div>
    );
  }

  // --- Edit mode ---
  const fieldLabel = "text-xs font-semibold uppercase tracking-wider text-base-content/50 mb-1.5";
  const inputCls = "w-full px-3 py-2 rounded-lg border border-base-300 bg-base-100 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40";

  function updateListItem(key: "symptoms" | "resolution_steps", idx: number, val: string) {
    setDraft(d => {
      const arr = [...d[key]];
      arr[idx] = val;
      return { ...d, [key]: arr };
    });
  }
  function addListItem(key: "symptoms" | "resolution_steps") {
    setDraft(d => ({ ...d, [key]: [...d[key], ""] }));
  }
  function removeListItem(key: "symptoms" | "resolution_steps", idx: number) {
    setDraft(d => {
      const arr = d[key].filter((_, i) => i !== idx);
      return { ...d, [key]: arr };
    });
  }

  return (
    <div className="px-6 pb-5 pt-4 border-t border-base-200 bg-base-100 space-y-4 text-sm">
      <div className="flex items-center gap-2 -mt-1">
        <button
          onClick={handleSave}
          disabled={saving}
          className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded bg-primary text-primary-content hover:opacity-90 disabled:opacity-50"
        >
          <Save size={11} /> {saving ? "Saving…" : "Save"}
        </button>
        <button
          onClick={() => { setEditing(false); setErr(null); }}
          disabled={saving}
          className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded border border-base-300 text-base-content/60 hover:text-base-content hover:bg-base-200"
        >
          <X size={11} /> Cancel
        </button>
        {err && <span className="text-[11px] text-error ml-2">{err}</span>}
      </div>

      <div>
        <div className={fieldLabel}>Title</div>
        <input
          className={inputCls}
          value={draft.title}
          onChange={e => setDraft(d => ({ ...d, title: e.target.value }))}
        />
      </div>

      <div>
        <div className={fieldLabel}>Problem Statement</div>
        <textarea
          rows={3}
          className={inputCls + " resize-y"}
          value={draft.problem_statement}
          onChange={e => setDraft(d => ({ ...d, problem_statement: e.target.value }))}
        />
      </div>

      <div>
        <div className={fieldLabel}>Symptoms</div>
        <div className="space-y-1.5">
          {draft.symptoms.map((s, i) => (
            <div key={i} className="flex gap-1.5">
              <input
                className={inputCls + " flex-1"}
                value={s}
                onChange={e => updateListItem("symptoms", i, e.target.value)}
              />
              <button
                onClick={() => removeListItem("symptoms", i)}
                className="px-2 rounded border border-base-300 text-base-content/50 hover:text-error hover:border-error"
              ><X size={12} /></button>
            </div>
          ))}
          <button
            onClick={() => addListItem("symptoms")}
            className="text-[11px] text-primary hover:underline"
          >+ Add symptom</button>
        </div>
      </div>

      <div>
        <div className={fieldLabel}>Resolution Steps</div>
        <div className="space-y-1.5">
          {draft.resolution_steps.map((s, i) => (
            <div key={i} className="flex gap-1.5">
              <span className="w-5 pt-2 text-right text-xs text-base-content/40">{i + 1}.</span>
              <textarea
                rows={2}
                className={inputCls + " flex-1 resize-y"}
                value={s}
                onChange={e => updateListItem("resolution_steps", i, e.target.value)}
              />
              <button
                onClick={() => removeListItem("resolution_steps", i)}
                className="px-2 self-start mt-1 rounded border border-base-300 text-base-content/50 hover:text-error hover:border-error"
              ><X size={12} /></button>
            </div>
          ))}
          <button
            onClick={() => addListItem("resolution_steps")}
            className="text-[11px] text-primary hover:underline"
          >+ Add step</button>
        </div>
      </div>

      <div>
        <div className={fieldLabel}>Additional Notes</div>
        <textarea
          rows={3}
          className={inputCls + " resize-y"}
          value={draft.additional_notes}
          onChange={e => setDraft(d => ({ ...d, additional_notes: e.target.value }))}
        />
        {contact.length > 0 && (
          <p className="text-[10px] text-base-content/40 mt-1">
            GW IT contact block is preserved automatically — no need to re-add it.
          </p>
        )}
      </div>
    </div>
  );
}

export default function KBArticlesPage() {
  const { runId } = useRun();
  const [data, setData]     = useState<{ stats: any; articles: KBArticle[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    fetch(`/api/kb-articles/${runId}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [runId]);

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-base-content/30">
        <FileText size={36} strokeWidth={1.5} />
        <p className="text-sm">Select a pipeline run from the sidebar.</p>
      </div>
    );
  }

  const articles = (Array.isArray(data?.articles) ? data!.articles : [])
    .filter(a => !HIDDEN_CLUSTER_IDS.has(a.cluster_id));

  // Recompute KPI stats from the visible articles so the cards match the list.
  // `validated` is backend-only (kb_validation_results) so we read it from the payload.
  const stats = data ? (() => {
    const n_dups = articles.filter(a => a.is_duplicate_of && a.is_duplicate_of !== "None").length;
    const avgQ = articles.length
      ? articles.reduce((s, a) => s + (a.quality_score || 0), 0) / articles.length
      : 0;
    return {
      canonical:    articles.length - n_dups,
      duplicates:   n_dups,
      avg_quality:  Math.round(avgQ * 10) / 10,
      needs_review: articles.filter(a => a.needs_review).length,
      validated:    data.stats?.validated ?? 0,
    };
  })() : null;
  const shown    = search.trim()
    ? articles.filter(a =>
        a.title.toLowerCase().includes(search.toLowerCase()) ||
        a.category.toLowerCase().includes(search.toLowerCase()))
    : articles;

  function toggleExpand(id: number) {
    setExpanded(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-base-content tracking-tight">Generated KB Articles</h1>
        <p className="text-sm text-base-content/45 mt-0.5">AI-generated knowledge base articles from cluster gap analysis.</p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-5 gap-3 mb-6">
        {loading || !stats ? Array(5).fill(0).map((_, i) => (
          <div key={i} className="h-[76px] rounded-xl bg-base-200 animate-pulse border border-base-300" />
        )) : <>
          <StatCard label="Canonical"    value={stats.canonical}    sub="Unique articles" />
          <StatCard label="Duplicates"   value={stats.duplicates}   sub="Flagged as duplicates" />
          <StatCard label="Avg Quality"  value={stats.avg_quality}  sub="0–10 scale" />
          <StatCard label="Needs Review" value={stats.needs_review} accent="warning" />
          <StatCard label="Validated"    value={stats.validated}    accent="success" />
        </>}
      </div>

      {/* Search */}
      <div className="flex items-center gap-3 mb-4">
        <input
          className="input input-sm input-bordered flex-1 max-w-sm bg-base-100"
          placeholder="Search by title or category…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <span className="text-[11px] text-base-content/30">{shown.length} article{shown.length !== 1 ? "s" : ""}</span>
      </div>

      {/* Article list */}
      {loading ? (
        <div className="space-y-2">{Array(5).fill(0).map((_, i) => <div key={i} className="h-14 rounded-xl bg-base-200 animate-pulse border border-base-300" />)}</div>
      ) : shown.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-base-content/30 text-sm">No articles found.</div>
      ) : (
        <div className="space-y-1.5">
          {shown.map(a => {
            const isOpen          = expanded.has(a.cluster_id);
            const symptoms        = parseList(a.symptoms);
            const resolutionSteps = parseList(a.resolution_steps);
            const { notes: addNotes, contact } = parseNotes(a.additional_notes);
            const confLabel = typeof a.confidence === "string" && ["HIGH","MEDIUM","LOW"].includes(a.confidence)
              ? a.confidence : null;
            return (
              <div key={a.cluster_id} className="border border-base-300 rounded-xl overflow-hidden">
                <button
                  className="w-full text-left px-4 py-3 bg-base-100 hover:bg-base-200/50 transition-colors flex items-center gap-3"
                  onClick={() => toggleExpand(a.cluster_id)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-[13px] text-base-content truncate">{a.title}</span>
                      {a.needs_review && <span className="badge badge-xs badge-warning"><AlertTriangle size={8} className="mr-0.5" />Review</span>}
                    </div>
                    <p className="text-[11px] text-base-content/35 mt-0.5">{a.category} · Cluster {a.cluster_id}</p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {confLabel && (
                      <span className={`badge badge-xs ${
                        confLabel === "HIGH" ? "badge-success" : confLabel === "MEDIUM" ? "badge-warning" : "badge-error"
                      }`}>{confLabel}</span>
                    )}
                    {isOpen ? <ChevronDown size={13} className="text-base-content/25" /> : <ChevronRight size={13} className="text-base-content/25" />}
                  </div>
                </button>
                {isOpen && (
                  <ArticleBody
                    article={a}
                    runId={runId}
                    onSaved={patch => {
                      setData(d => d && {
                        ...d,
                        articles: d.articles.map(x => x.cluster_id === a.cluster_id ? { ...x, ...patch } : x),
                      });
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
