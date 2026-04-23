"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  GitBranch, FileText, BookOpen, MessageSquare, Play,
  Sun, Moon, ChevronDown, CheckCircle, Clock, Trash2, Loader2,
  Settings2, X, EyeOff, Square,
} from "lucide-react";
import { useRun } from "@/lib/RunContext";
import { useTheme } from "@/lib/theme";
import { cancelPipelineByRunId } from "@/lib/pipeline";
import type { Run } from "@/lib/types";

const ACTIVE_STATUSES = new Set(["running", "queued"]);

const NAV = [
  { href: "/pipeline",    Icon: Play,         label: "Run Pipeline" },
  { href: "/clusters",    Icon: GitBranch,    label: "Clusters"     },
  { href: "/kb-articles", Icon: FileText,     label: "KB Articles"  },
  { href: "/existing-kb", Icon: BookOpen,     label: "Existing KB"  },
  { href: "/chat",        Icon: MessageSquare,label: "AI Chat"      },
];

function formatRunLabel(r: Run) {
  try {
    const d = new Date(r.started_at);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return r.started_at.slice(0, 10);
  }
}

export default function Sidebar() {
  const pathname = usePathname();
  const { runId, setRunId, runs, runsLoaded, deleteRun } = useRun();
  const { isDark, toggle } = useTheme();
  const loaded = runsLoaded;

  const selectedRun = runs.find((r: Run) => r.run_id === runId);

  const [manageOpen,  setManageOpen]  = useState(false);
  const [pendingId,   setPendingId]   = useState<string | null>(null); // inline-confirm row
  const [deletingId,  setDeletingId]  = useState<string | null>(null);
  const [stoppingId,  setStoppingId]  = useState<string | null>(null);
  const [deleteErr,   setDeleteErr]   = useState<string | null>(null);
  const [allRuns,     setAllRuns]     = useState<Run[] | null>(null);   // includes hidden
  const [allRunsLoading, setAllRunsLoading] = useState(false);

  // Fetch hidden-inclusive list each time the modal opens so deletions reflect immediately.
  useEffect(() => {
    if (!manageOpen) return;
    let cancelled = false;
    setAllRunsLoading(true);
    fetch("/api/runs?include_hidden=1&require_clusters=0", { cache: "no-store" })
      .then(r => r.json())
      .then((data: Run[]) => { if (!cancelled && Array.isArray(data)) setAllRuns(data); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setAllRunsLoading(false); });
    return () => { cancelled = true; };
  }, [manageOpen, runs]);

  async function onConfirmDelete(id: string) {
    setDeletingId(id);
    setDeleteErr(null);
    try {
      await deleteRun(id);
      setPendingId(null);
      setAllRuns(prev => prev ? prev.filter(r => r.run_id !== id) : prev);
    } catch (err) {
      setDeleteErr(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingId(null);
    }
  }

  async function onStopRun(id: string) {
    setStoppingId(id);
    setDeleteErr(null);
    try {
      await cancelPipelineByRunId(id);
      // Optimistic local flip so the Trash button appears immediately.
      setAllRuns(prev =>
        prev ? prev.map(r => r.run_id === id ? { ...r, status: "cancelled" } : r) : prev,
      );
    } catch (err) {
      setDeleteErr(err instanceof Error ? err.message : String(err));
    } finally {
      setStoppingId(null);
    }
  }

  function closeManage() {
    if (deletingId || stoppingId) return;
    setManageOpen(false);
    setPendingId(null);
    setDeleteErr(null);
  }

  return (
    <aside className="w-[220px] min-w-[220px] h-screen sticky top-0 flex flex-col bg-base-100 border-r border-base-300">
      {/* ── Brand ── */}
      <div className="px-4 pt-5 pb-4 flex flex-col items-center gap-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/assets/gw-logo.png"
          alt="GW IT Support"
          style={{ maxWidth: "160px", maxHeight: "64px", objectFit: "contain" }}
        />
        <p className="text-[11px] font-semibold tracking-widest uppercase text-base-content/70">IT Support</p>
      </div>

      <div className="mx-4 h-px bg-base-300" />

      {/* ── Navigation ── */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        <p className="px-3 mb-2 text-[9px] font-bold uppercase tracking-widest text-base-content/30">
          Pages
        </p>
        {NAV.map(({ href, Icon, label }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={`
                group flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium
                transition-all duration-150 select-none
                ${active
                  ? "bg-primary text-primary-content shadow-sm"
                  : "text-base-content/55 hover:text-base-content hover:bg-base-200"
                }
              `}
            >
              <Icon
                size={15}
                className={`flex-shrink-0 transition-opacity ${active ? "opacity-100" : "opacity-60 group-hover:opacity-100"}`}
              />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="mx-4 h-px bg-base-300" />

      {/* ── Pipeline Run ── */}
      <div className="px-4 py-4">
        <p className="text-[9px] font-bold uppercase tracking-widest text-base-content/30 mb-2.5">
          Pipeline Run
        </p>
        {!loaded ? (
          <div className="h-8 rounded-lg bg-base-200 animate-pulse" />
        ) : runs.length === 0 ? (
          <p className="text-xs text-base-content/40 italic">No runs available</p>
        ) : (
          <div className="relative">
            <select
              className="w-full appearance-none bg-base-200 border border-base-300 rounded-lg
                         px-3 py-2 pr-7 text-[11px] font-medium text-base-content
                         focus:outline-none focus:ring-2 focus:ring-primary/40 cursor-pointer"
              value={runId ?? ""}
              onChange={e => setRunId(e.target.value)}
            >
              {runs.map(r => (
                <option key={r.run_id} value={r.run_id}>
                  {r.status === "complete" ? "● " : "○ "}{formatRunLabel(r)}
                </option>
              ))}
            </select>
            <ChevronDown
              size={11}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base-content/35 pointer-events-none"
            />
          </div>
        )}
        {loaded && runs.length > 0 && (
          <button
            type="button"
            onClick={() => { setDeleteErr(null); setPendingId(null); setManageOpen(true); }}
            className="mt-2 w-full flex items-center justify-center gap-1.5 px-2.5 py-1.5
                       rounded-lg border border-base-300 bg-base-100 text-base-content/60
                       hover:text-base-content hover:bg-base-200 transition-colors
                       text-[10.5px] font-medium"
          >
            <Settings2 size={11} />
            Manage runs
          </button>
        )}
        {selectedRun && (
          <div className="mt-2 flex items-center gap-1.5">
            {selectedRun.status === "complete"
              ? <CheckCircle size={10} className="text-success flex-shrink-0" />
              : <Clock size={10} className="text-warning flex-shrink-0" />
            }
            <span className="text-[10px] text-base-content/35 capitalize">{selectedRun.status}</span>
          </div>
        )}
      </div>

      <div className="mx-4 h-px bg-base-300" />

      {/* ── Footer ── */}
      <div className="px-4 py-3.5 flex items-center justify-between">
        <p className="text-[9px] text-base-content/20 font-medium">GW IT Dashboard v2</p>
        <button
          onClick={toggle}
          title={isDark ? "Switch to light mode" : "Switch to dark mode"}
          className="w-7 h-7 rounded-lg bg-base-200 hover:bg-base-300 flex items-center justify-center
                     transition-colors border border-base-300"
        >
          {isDark
            ? <Sun size={13} className="text-base-content/50" />
            : <Moon size={13} className="text-base-content/50" />
          }
        </button>
      </div>

      {manageOpen && typeof document !== "undefined" && createPortal(
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm"
          onClick={closeManage}
        >
          <div
            role="dialog"
            aria-modal="true"
            className="w-[560px] max-w-[94vw] max-h-[80vh] flex flex-col
                       bg-base-100 border border-base-300 rounded-xl shadow-xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-base-300">
              <div>
                <h2 className="text-[14px] font-bold text-base-content leading-tight">Manage runs</h2>
                <p className="text-[11px] text-base-content/50 mt-0.5">
                  Delete a run to permanently remove all of its data.
                </p>
              </div>
              <button
                type="button"
                onClick={closeManage}
                disabled={!!deletingId || !!stoppingId}
                className="w-7 h-7 flex items-center justify-center rounded-lg
                           text-base-content/50 hover:text-base-content hover:bg-base-200
                           disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                aria-label="Close"
              >
                <X size={14} />
              </button>
            </div>

            {deleteErr && (
              <div className="mx-5 mt-3 p-2.5 rounded-lg bg-error/10 border border-error/30 text-[11px] text-error break-all">
                {deleteErr}
              </div>
            )}

            <ul className="overflow-y-auto px-3 py-3 space-y-1.5">
              {(allRuns ?? runs).map(r => {
                const isPending  = pendingId  === r.run_id;
                const isDeleting = deletingId === r.run_id;
                const isStopping = stoppingId === r.run_id;
                const isSelected = runId      === r.run_id;
                const isHidden   = !!r.hidden;
                const isActive   = ACTIVE_STATUSES.has(r.status);
                return (
                  <li
                    key={r.run_id}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border
                                ${isPending
                                  ? "bg-error/5 border-error/30"
                                  : isHidden
                                    ? "bg-base-200/40 border-base-300 hover:bg-base-200"
                                    : "bg-base-100 border-base-300 hover:bg-base-200"}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {r.status === "complete"
                          ? <CheckCircle size={11} className="text-success shrink-0" />
                          : <Clock size={11} className="text-warning shrink-0" />}
                        <span className={`text-[12px] font-semibold ${isHidden ? "text-base-content/65" : "text-base-content"}`}>
                          {formatRunLabel(r)}
                        </span>
                        {isSelected && (
                          <span className="text-[9px] font-bold uppercase tracking-widest
                                           text-primary bg-primary/10 border border-primary/20
                                           rounded px-1.5 py-0.5">
                            Selected
                          </span>
                        )}
                        {isHidden && (
                          <span className="text-[9px] font-bold uppercase tracking-widest
                                           text-base-content/55 bg-base-300/60 border border-base-300
                                           rounded px-1.5 py-0.5 flex items-center gap-1">
                            <EyeOff size={9} />
                            Hidden
                          </span>
                        )}
                      </div>
                      <p className="text-[10.5px] font-mono text-base-content/45 mt-0.5 truncate">
                        {r.run_id}
                      </p>
                    </div>

                    {isActive ? (
                      <button
                        type="button"
                        onClick={() => onStopRun(r.run_id)}
                        disabled={isStopping || !!deletingId}
                        title="Stop this run (current phase will finish first)"
                        aria-label={`Stop run ${r.run_id}`}
                        className="shrink-0 flex items-center gap-1 px-2 h-7 rounded-lg
                                   border border-warning/40 bg-warning/10 text-warning
                                   hover:bg-warning/20
                                   disabled:opacity-40 disabled:cursor-not-allowed
                                   text-[11px] font-semibold transition-colors"
                      >
                        {isStopping
                          ? <><Loader2 size={11} className="animate-spin" />Stopping…</>
                          : <><Square size={11} />Stop</>}
                      </button>
                    ) : isPending ? (
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => setPendingId(null)}
                          disabled={isDeleting}
                          className="btn btn-xs btn-ghost"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => onConfirmDelete(r.run_id)}
                          disabled={isDeleting}
                          className="btn btn-xs btn-error"
                        >
                          {isDeleting
                            ? <><Loader2 size={11} className="animate-spin mr-1" />Deleting…</>
                            : <>Confirm delete</>}
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => { setDeleteErr(null); setPendingId(r.run_id); }}
                        disabled={!!deletingId || !!stoppingId}
                        title="Delete this run"
                        aria-label={`Delete run ${r.run_id}`}
                        className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg
                                   border border-base-300 bg-base-100 text-base-content/50
                                   hover:text-error hover:border-error/40 hover:bg-error/10
                                   disabled:opacity-40 disabled:cursor-not-allowed
                                   transition-colors"
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </li>
                );
              })}
              {allRunsLoading && !allRuns && (
                <li className="px-3 py-6 text-center text-[12px] text-base-content/40 italic">
                  Loading…
                </li>
              )}
              {!allRunsLoading && (allRuns ?? runs).length === 0 && (
                <li className="px-3 py-6 text-center text-[12px] text-base-content/40 italic">
                  No runs to manage.
                </li>
              )}
            </ul>

            <div className="px-5 py-3 border-t border-base-300 flex justify-end">
              <button
                type="button"
                onClick={closeManage}
                disabled={!!deletingId || !!stoppingId}
                className="btn btn-sm btn-ghost"
              >
                Close
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </aside>
  );
}
