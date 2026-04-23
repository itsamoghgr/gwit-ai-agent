"use client";

import { useEffect, useState } from "react";
import { Circle, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight } from "lucide-react";
import type { PhaseStatus, PhaseActivityEntry } from "@/lib/types";

interface Props {
  phase: PhaseStatus;
  description: string;
  events: PhaseActivityEntry[];
}

const FEED_VISIBLE = 5;

function formatStats(stats: Record<string, unknown> | null | undefined): string {
  if (!stats) return "";
  return Object.entries(stats)
    .filter(([k]) => k !== "placeholder")
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(" · ");
}

function useRunningSeconds(active: boolean, startedAt: string | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [active]);
  if (!startedAt) return 0;
  return Math.max(0, (now - new Date(startedAt).getTime()) / 1000);
}

export default function PhaseCard({ phase, description, events }: Props) {
  const { status, duration_s, error, stats, started_at } = phase;
  const isRunning = status === "running";
  const isFailed  = status === "failed";
  const isDone    = status === "complete";

  const [expanded, setExpanded] = useState(false);
  const shouldExpand = isRunning || isFailed || expanded;

  const runningSecs = useRunningSeconds(isRunning, started_at);
  const current = events[events.length - 1];

  const icon =
    isRunning ? <Loader2 size={14} className="animate-spin text-primary" />
    : isDone  ? <CheckCircle2 size={14} className="text-success" />
    : isFailed ? <XCircle size={14} className="text-error" />
    : <Circle size={14} className="text-base-content/25" />;

  const statusLabel =
    isRunning ? `${runningSecs.toFixed(0)}s`
    : isDone  ? `${duration_s?.toFixed(1)}s`
    : isFailed ? `Failed · ${duration_s?.toFixed(1)}s`
    : "Pending";

  const toneBorder =
    isRunning ? "bg-primary/5 border-primary/30"
    : isDone  ? "bg-success/5 border-success/20"
    : isFailed ? "bg-error/5 border-error/30"
    : "bg-base-100 border-base-300";

  const hasBar = !!(current && current.current != null && current.total != null && current.total > 0);
  const barPct = hasBar ? Math.min(100, ((current!.current! / current!.total!) * 100)) : 0;

  const visibleEvents = events.slice(-FEED_VISIBLE).reverse();

  return (
    <div className={`rounded-lg border ${toneBorder}`}>
      {/* Header row */}
      <div className="flex items-start gap-3 p-3">
        <div className="mt-0.5">{icon}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-[12px] font-semibold text-base-content">
              Phase {phase.phase} · {description}
            </p>
            <span className="text-[11px] text-base-content/45 font-mono whitespace-nowrap">
              {statusLabel}
            </span>
          </div>

          {/* Completed phases: stats line + optional expand toggle */}
          {isDone && (
            <div className="flex items-center justify-between gap-2 mt-1">
              {formatStats(stats) ? (
                <p className="text-[11px] text-base-content/50 font-mono truncate">
                  {formatStats(stats)}
                </p>
              ) : <span />}
              {events.length > 0 && (
                <button
                  type="button"
                  onClick={() => setExpanded(v => !v)}
                  className="flex items-center gap-1 text-[11px] text-base-content/50 hover:text-base-content shrink-0"
                >
                  {expanded
                    ? <><ChevronDown size={11} />Hide activity</>
                    : <><ChevronRight size={11} />Show activity</>}
                </button>
              )}
            </div>
          )}

          {/* Failed phases: error message */}
          {isFailed && error && (
            <p className="text-[11px] text-error mt-1 break-all">
              {error}
            </p>
          )}
        </div>
      </div>

      {/* Expanded body: current-step + progress bar + activity feed */}
      {shouldExpand && events.length > 0 && (
        <div className="px-3 pb-3 pt-0 border-t border-base-300/40">
          {/* Current step line */}
          {current && (
            <div className="mt-2 flex items-start gap-2">
              {current.sub_phase && (
                <span className="shrink-0 text-[10px] font-mono font-bold uppercase
                                 text-primary bg-primary/10 border border-primary/20
                                 rounded px-1.5 py-0.5 mt-0.5">
                  {current.sub_phase}
                </span>
              )}
              <div className="flex-1 min-w-0">
                <p className="text-[12.5px] text-base-content font-medium truncate">
                  {current.label}
                  {current.detail && (
                    <span className="text-base-content/55 font-normal"> · {current.detail}</span>
                  )}
                </p>
                {hasBar && (
                  <div className="mt-1.5 h-1 rounded-full bg-base-300 overflow-hidden">
                    <div
                      className="h-full bg-primary transition-[width] duration-200"
                      style={{ width: `${barPct}%` }}
                    />
                  </div>
                )}
                {hasBar && (
                  <p className="text-[10px] text-base-content/45 font-mono mt-0.5">
                    {current.current}/{current.total}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Activity feed */}
          {visibleEvents.length > 1 && (
            <ul className="mt-3 space-y-0.5">
              {visibleEvents.slice(1).map((ev, idx) => (
                <li
                  key={idx}
                  className="flex items-baseline gap-2 text-[11px] text-base-content/55 leading-snug"
                  style={{ opacity: 1 - idx * 0.15 }}
                >
                  <span className="font-mono text-base-content/35 shrink-0">{ev.ts}</span>
                  <span className="truncate">
                    {ev.label}
                    {ev.detail && <span className="text-base-content/40"> · {ev.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
