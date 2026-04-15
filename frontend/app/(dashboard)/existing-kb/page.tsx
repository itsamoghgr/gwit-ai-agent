"use client";

import { useEffect, useState } from "react";
import { useRun } from "@/lib/RunContext";
import StatCard from "@/components/StatCard";
import UtilBadge from "@/components/UtilBadge";
import { BookOpen, ChevronDown, ChevronRight } from "lucide-react";
import type { ExistingKB } from "@/lib/types";

const UTIL_FILTERS = ["All", "ACTIVE", "ORPHAN", "OVER-RELIED", "PERIPHERAL"] as const;
type UtilFilter = typeof UTIL_FILTERS[number];

export default function ExistingKBPage() {
  const { runId } = useRun();
  const [data, setData]       = useState<{ stats: any; articles: ExistingKB[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch]   = useState("");
  const [filter, setFilter]   = useState<UtilFilter>("All");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    setLoading(true);
    const qs = runId ? `?run_id=${runId}` : "";
    fetch(`/api/existing-kb${qs}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [runId]);

  const articles = data?.articles ?? [];
  const stats    = data?.stats;

  let shown = articles;
  if (search.trim()) {
    const q = search.toLowerCase();
    shown = shown.filter(a => a.title.toLowerCase().includes(q) || a.issue.toLowerCase().includes(q));
  }
  if (filter !== "All") shown = shown.filter(a => a.util_status === filter);

  function toggle(id: string) {
    setExpanded(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-base-content tracking-tight">Existing KB Articles</h1>
        <p className="text-sm text-base-content/45 mt-0.5">
          GWU's existing knowledge base, enriched with utilization analysis.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-6">
        {loading || !stats ? Array(4).fill(0).map((_, i) => (
          <div key={i} className="h-[76px] rounded-xl bg-base-200 animate-pulse border border-base-300" />
        )) : <>
          <StatCard label="Total Articles" value={stats.total.toLocaleString()} />
          <StatCard label="Active"         value={stats.active.toLocaleString()}      accent="success" />
          <StatCard label="Orphaned"       value={stats.orphaned.toLocaleString()} />
          <StatCard label="Over-Relied"    value={stats.over_relied.toLocaleString()} accent="critical" />
        </>}
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <input
          className="input input-sm input-bordered bg-base-100 max-w-xs"
          placeholder="Search title or issue…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <div className="flex gap-1">
          {UTIL_FILTERS.map(f => (
            <button key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                filter === f
                  ? "bg-primary text-primary-content shadow-sm"
                  : "bg-base-200 text-base-content/50 hover:text-base-content border border-base-300"
              }`}
            >{f}</button>
          ))}
        </div>
        <span className="text-[11px] text-base-content/30 ml-auto">{shown.length} articles</span>
      </div>

      {loading ? (
        <div className="space-y-2">{Array(6).fill(0).map((_, i) => <div key={i} className="h-12 rounded-xl bg-base-200 animate-pulse border border-base-300" />)}</div>
      ) : shown.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-40 gap-3 text-base-content/30">
          <BookOpen size={32} strokeWidth={1.5} />
          <p className="text-sm">No articles match.</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {shown.map(a => {
            const isOpen = expanded.has(a.id);
            return (
              <div key={a.id} className="border border-base-300 rounded-xl overflow-hidden">
                <button
                  className="w-full text-left px-4 py-3 bg-base-100 hover:bg-base-200/50 transition-colors flex items-center gap-3"
                  onClick={() => toggle(a.id)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-[13px] text-base-content">{a.title}</span>
                      {a.util_status && <UtilBadge status={a.util_status} count={a.util_count ?? 0} />}
                    </div>
                    <p className="text-[11px] text-base-content/35 mt-0.5 truncate">{a.issue.slice(0, 100)}</p>
                  </div>
                  {isOpen
                    ? <ChevronDown size={13} className="text-base-content/25 flex-shrink-0" />
                    : <ChevronRight size={13} className="text-base-content/25 flex-shrink-0" />
                  }
                </button>
                {isOpen && (
                  <div className="px-4 pb-4 pt-3 border-t border-base-200 bg-base-100 space-y-3 text-[12px]">
                    {a.issue && (
                      <div>
                        <p className="text-[9px] font-bold uppercase tracking-wider text-base-content/30 mb-1">Issue / Problem</p>
                        <p className="text-base-content/70 leading-relaxed">{a.issue}</p>
                      </div>
                    )}
                    {a.solution && (
                      <div>
                        <p className="text-[9px] font-bold uppercase tracking-wider text-base-content/30 mb-1">Solution</p>
                        <div className="max-h-64 overflow-y-auto bg-base-200 rounded-lg p-3 text-[11px] text-base-content/60 leading-relaxed whitespace-pre-wrap border border-base-300">
                          {a.solution}
                        </div>
                      </div>
                    )}
                    <div className="pt-1 border-t border-base-200 text-[10px] text-base-content/25 flex items-center gap-3">
                      <span>ID: {a.id}</span>
                      {a.util_count != null && a.util_count > 0 && <span>· Matched by {a.util_count} cluster(s)</span>}
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
