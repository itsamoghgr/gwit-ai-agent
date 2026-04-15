"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  GitBranch, FileText, BookOpen, MessageSquare,
  Sun, Moon, ChevronDown, CheckCircle, Clock,
} from "lucide-react";
import { useRun } from "@/lib/RunContext";
import { useTheme } from "@/lib/theme";
import type { Run } from "@/lib/types";

const NAV = [
  { href: "/clusters",    Icon: GitBranch,    label: "Clusters"    },
  { href: "/kb-articles", Icon: FileText,     label: "KB Articles" },
  { href: "/existing-kb", Icon: BookOpen,     label: "Existing KB" },
  { href: "/chat",        Icon: MessageSquare,label: "AI Chat"     },
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
  const pathname      = usePathname();
  const { runId, setRunId } = useRun();
  const { isDark, toggle }  = useTheme();
  const [runs, setRuns]     = useState<Run[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch("/api/runs")
      .then(r => r.json())
      .then((data: Run[]) => {
        if (!Array.isArray(data)) return;
        setRuns(data);
        if (data.length > 0 && !runId) setRunId(data[0].run_id);
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedRun = runs.find(r => r.run_id === runId);

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
    </aside>
  );
}
