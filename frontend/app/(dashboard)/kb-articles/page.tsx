"use client";

import { useEffect, useState } from "react";
import { useRun } from "@/lib/RunContext";
import StatCard from "@/components/StatCard";
import { FileText, ChevronDown, ChevronRight, ShieldCheck, AlertTriangle } from "lucide-react";
import type { KBArticle } from "@/lib/types";

const HIDDEN_CLUSTER_IDS = new Set<number>([112, 170]);

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

  const articles = (data?.articles ?? []).filter(a => !HIDDEN_CLUSTER_IDS.has(a.cluster_id));
  const stats    = data?.stats;
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
                  <div className="px-6 pb-5 pt-4 border-t border-base-200 bg-base-100 space-y-5 text-sm">
                    {a.problem_statement && (
                      <div>
                        <h3 className="font-bold text-base text-base-content mb-1.5">Problem Statement</h3>
                        <p className="text-base-content leading-relaxed">{a.problem_statement}</p>
                      </div>
                    )}
                    {symptoms.length > 0 && (
                      <div>
                        <h3 className="font-bold text-base text-base-content mb-1.5">Symptoms</h3>
                        <ul className="list-disc list-outside pl-5 space-y-1">
                          {symptoms.map((s, i) => <li key={i} className="text-base-content leading-relaxed">{s}</li>)}
                        </ul>
                      </div>
                    )}
                    {resolutionSteps.length > 0 && (
                      <div>
                        <h3 className="font-bold text-base text-base-content mb-1.5">Resolution Steps</h3>
                        <ol className="list-decimal list-outside pl-5 space-y-2">
                          {resolutionSteps.map((s, i) => <li key={i} className="text-base-content leading-relaxed">{s}</li>)}
                        </ol>
                      </div>
                    )}
                    {(addNotes || contact.length > 0) && (
                      <div>
                        {addNotes && (
                          <>
                            <h3 className="font-bold text-base text-base-content mb-1.5">Additional Notes</h3>
                            <p className="text-base-content leading-relaxed mb-4">{addNotes}</p>
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
                      <span>Cluster {a.cluster_id}</span>
                      {a.wo_in_cluster > 0 && <span>· {a.wo_in_cluster} Work Orders</span>}
                      {a.is_duplicate_of && <span>· Duplicate of: {a.is_duplicate_of}</span>}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
