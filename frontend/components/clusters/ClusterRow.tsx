"use client";

import { useState } from "react";
import {
  AlertCircle, AlertTriangle, CheckCircle, Copy,
  ChevronDown, ChevronRight, BrainCircuit, ShieldCheck,
  GitMerge, Loader2,
} from "lucide-react";
import type { ClusterOut, Ticket } from "@/lib/types";
import { CONF_COLORS } from "@/lib/types";

/** Render **bold** and newlines from LLM-generated markdown summaries */
function md(text: string) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br/>");
}

/** Single-line cell — double-click to expand full text in-place */
function ExpandableCell({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <td
      className={`
        min-w-[200px] max-w-[300px] cursor-pointer select-text align-middle
        transition-all duration-150
        ${expanded
          ? "whitespace-normal break-words ring-1 ring-primary/40 bg-primary/5 rounded"
          : ""}
      `}
      title={expanded ? undefined : text}
      onDoubleClick={() => setExpanded(e => !e)}
    >
      <div className={`leading-relaxed py-0.5 ${expanded ? "" : "truncate"}`}>
        {text}
      </div>
    </td>
  );
}

/* ── Gap icon map ─────────────────────────────────────────── */
const GAP_ICON: Record<string, React.ReactNode> = {
  CRITICAL:  <AlertCircle  size={13} className="text-error"           />,
  PARTIAL:   <AlertTriangle size={13} className="text-warning"        />,
  COVERED:   <CheckCircle  size={13} className="text-success"         />,
  DUPLICATE: <Copy         size={13} className="text-base-content/35" />,
};

const GAP_TEXT: Record<string, string> = {
  CRITICAL:  "text-error",
  PARTIAL:   "text-warning",
  COVERED:   "text-success",
  DUPLICATE: "text-base-content/35",
};

interface Props {
  cluster:     ClusterOut;
  allClusters: ClusterOut[];
  runId:       string;
}

export default function ClusterRow({ cluster, allClusters, runId }: Props) {
  const [open, setOpen]     = useState(false);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);

  const { gap_flag, cluster_id, cluster_label, size, max_kb_sim } = cluster;

  const dupRows   = allClusters.filter(c => c.gap_flag === "DUPLICATE" && c.canonical_cluster_id === cluster_id);
  const mergedSize = size + dupRows.reduce((s, d) => s + d.size, 0);
  const canonRow  = cluster.canonical_cluster_id != null
    ? allClusters.find(c => c.cluster_id === cluster.canonical_cluster_id) : null;

  const isDup    = gap_flag === "DUPLICATE";
  const llmConf  = cluster.llm_confidence ?? "NONE";
  const confStyle = CONF_COLORS[llmConf] ?? CONF_COLORS["NONE"];
  const hasLLM   = !!(cluster.llm_kb_match || cluster.llm_confidence);

  async function fetchTickets() {
    if (fetched) return;
    setLoading(true);
    const extra = dupRows.map(d => d.cluster_id).join(",");
    const qs    = extra ? `?extra_ids=${extra}` : "";
    try {
      const res  = await fetch(`/api/clusters/${runId}/${cluster_id}/tickets${qs}`);
      const data = await res.json();
      setTickets(Array.isArray(data) ? data : []);
    } catch {
      setTickets([]);
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }

  function handleToggle() {
    const next = !open;
    setOpen(next);
    if (next && !fetched) fetchTickets();
  }

  return (
    <div className={`border rounded-xl overflow-hidden mb-1.5 transition-shadow hover:shadow-sm ${
      open ? "border-base-300 shadow-sm" : "border-base-300"
    }`}>
      {/* ── Row header ── */}
      <button
        className="w-full text-left px-4 py-3 bg-base-100 hover:bg-base-200/50 transition-colors flex items-center gap-3 focus:outline-none"
        onClick={handleToggle}
      >
        <span className="flex-shrink-0">{GAP_ICON[gap_flag]}</span>

        <span className={`text-[10px] font-bold uppercase tracking-wide flex-shrink-0 w-20 ${GAP_TEXT[gap_flag]}`}>
          {gap_flag}
        </span>

        <span className="text-[10px] text-base-content/25 flex-shrink-0 font-mono">c{cluster_id}</span>

        <span className="text-sm text-base-content flex-1 min-w-0 truncate font-medium">
          {cluster_label}
        </span>

        <div className="flex items-center gap-3 flex-shrink-0 text-xs text-base-content/35">
          <span className="tabular-nums">{mergedSize.toLocaleString()} tickets{dupRows.length > 0 ? <span className="text-base-content/25"> (+{mergedSize - size})</span> : null}</span>
          {max_kb_sim != null && <span className="tabular-nums">KB {max_kb_sim.toFixed(3)}</span>}
          {isDup && cluster.canonical_cluster_id != null && (
            <span className="flex items-center gap-1 text-base-content/25">
              <GitMerge size={10} />c{cluster.canonical_cluster_id}
            </span>
          )}
          {cluster.llm_confidence === "HIGH" && cluster.llm_kb_match && (
            <span className="flex items-center gap-1 text-success text-[10px] font-semibold">
              <ShieldCheck size={10} /> KB matched
            </span>
          )}
          {open ? <ChevronDown size={13} className="text-base-content/25" /> : <ChevronRight size={13} className="text-base-content/25" />}
        </div>
      </button>

      {/* ── Expanded body ── */}
      {open && (
        <div className="px-4 pb-4 pt-3 border-t border-base-200 bg-base-100 space-y-3">

          {/* Duplicate of */}
          {isDup && (
            <div className="bg-base-200 rounded-lg p-3.5 border border-base-300">
              <p className="text-[10px] font-bold uppercase tracking-wider text-base-content/30 mb-1.5 flex items-center gap-1">
                <GitMerge size={9} /> Duplicate of
              </p>
              {canonRow
                ? <p className="text-[13px] text-base-content/70"><strong className="text-base-content">c{cluster.canonical_cluster_id}</strong> · {canonRow.cluster_label}</p>
                : cluster.canonical_cluster_id != null
                  ? <p className="text-base-content/70"><strong>c{cluster.canonical_cluster_id}</strong></p>
                  : <p className="text-base-content/30">Canonical cluster not identified</p>
              }
            </div>
          )}

          {/* Cluster summary */}
          {cluster.summary && (
            <div className="bg-summary rounded-lg p-3.5">
              <p className="text-[10px] font-bold uppercase tracking-wider text-indigo-400 mb-2 flex items-center gap-1">
                <BrainCircuit size={9} /> Cluster Summary
              </p>
              <p className="text-base-content/75 leading-relaxed text-[13px]"
                dangerouslySetInnerHTML={{ __html: md(cluster.summary) }}
              />
            </div>
          )}

          {/* LLM KB validation */}
          {hasLLM && (
            <div className="rounded-lg p-3.5 border" style={{ background: confStyle.bg, borderColor: confStyle.fg + "40" }}>
              <div className="flex items-center gap-2 mb-1.5">
                <p className="text-[10px] font-bold uppercase tracking-wider flex items-center gap-1" style={{ color: confStyle.fg }}>
                  <ShieldCheck size={9} /> LLM KB Validation
                </p>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-white" style={{ background: confStyle.fg }}>
                  {llmConf}
                </span>
              </div>
              <p className="text-[13px] text-base-content/75"><strong>Match:</strong> {cluster.llm_kb_match || "No match found"}</p>
              {cluster.llm_kb_reasoning && (
                <p className="text-xs text-base-content/50 mt-1.5 leading-relaxed">{cluster.llm_kb_reasoning}</p>
              )}
            </div>
          )}

          {/* Merged duplicates */}
          {dupRows.length > 0 && (
            <div className="bg-base-200 rounded-lg p-3.5 border border-base-300">
              <p className="text-[10px] font-bold uppercase tracking-wider text-base-content/30 mb-2 flex items-center gap-1">
                <Copy size={9} /> Merged duplicates ({dupRows.length})
              </p>
              {dupRows.sort((a, b) => b.size - a.size).map(d => (
                <div key={d.cluster_id} className="mt-0.5 text-base-content/50">
                  · <strong className="text-base-content/70">c{d.cluster_id}</strong>{" "}
                  <span className="text-base-content/30">({d.size.toLocaleString()} tickets)</span>{" "}
                  · <span className="text-[12px]">{d.cluster_label.slice(0, 80)}</span>
                </div>
              ))}
            </div>
          )}

          {/* Mini stats */}
          <div className="grid grid-cols-4 gap-2">
            {[
              { label: "Total Tickets", value: mergedSize.toLocaleString() + (dupRows.length > 0 ? "*" : "") },
              { label: "Incidents",     value: cluster.inc_tickets.toLocaleString() },
              { label: "Work Orders",   value: cluster.wo_tickets.toLocaleString() },
              { label: "KB Similarity", value: max_kb_sim?.toFixed(3) ?? "N/A" },
            ].map(stat => (
              <div key={stat.label} className="bg-base-200 rounded-lg p-2.5 border border-base-300 text-center">
                <p className="text-[10px] text-base-content/35 uppercase tracking-wide font-bold">{stat.label}</p>
                <p className="text-base font-bold mt-1 text-base-content">{stat.value}</p>
              </div>
            ))}
          </div>

          <div className="h-px bg-base-300" />

          {/* Ticket table */}
          {loading ? (
            <div className="flex items-center justify-center py-6 gap-2 text-base-content/30 text-sm">
              <Loader2 size={16} className="animate-spin" /> Loading tickets…
            </div>
          ) : tickets.length === 0 ? (
            <p className="text-center text-base-content/30 text-sm py-4">No ticket detail found.</p>
          ) : (
            <div className="overflow-x-auto overflow-y-auto max-h-[420px] rounded-lg border border-base-300">
              <table className="table table-sm table-zebra w-full text-xs">
                <thead className="sticky top-0 bg-base-200 z-10">
                  <tr>
                    {dupRows.length > 0 && <th>Cluster</th>}
                    <th>Type</th>
                    <th>Ticket #</th>
                    <th>Service</th>
                    <th>Group</th>
                    <th>Problem</th>
                    <th>Resolution</th>
                  </tr>
                </thead>
                <tbody>
                  {tickets.map((t, i) => (
                    <tr key={i}>
                      {dupRows.length > 0 && (
                        <td className="text-base-content/30 font-mono">c{t.cluster_id}</td>
                      )}
                      <td>
                        <span className={`badge badge-xs ${t.source === "incident" ? "badge-info" : "badge-warning"}`}>
                          {t.source === "incident" ? "Inc" : "WO"}
                        </span>
                      </td>
                      <td className="font-mono">{t.ticket_number}</td>
                      <td className="max-w-24 truncate" title={t.service_type}>{t.service_type}</td>
                      <td className="max-w-24 truncate" title={t.assigned_group}>{t.assigned_group}</td>
                      <ExpandableCell text={t.problem_text} />
                      <ExpandableCell text={t.resolution_text} />
                    </tr>
                  ))}
                </tbody>
              </table>
              {dupRows.length > 0 && (
                <p className="text-[10px] text-base-content/25 mt-1.5 pl-1">* Includes tickets from {dupRows.length} merged cluster(s).</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
