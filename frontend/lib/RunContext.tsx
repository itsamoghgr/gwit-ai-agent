"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import type { Run } from "@/lib/types";

interface RunCtx {
  runId: string;
  setRunId: (id: string) => void;
  runs: Run[];
  runsLoaded: boolean;
  refreshRuns: () => Promise<void>;
  deleteRun: (id: string) => Promise<void>;
}

const RunContext = createContext<RunCtx>({
  runId: "",
  setRunId: () => {},
  runs: [],
  runsLoaded: false,
  refreshRuns: async () => {},
  deleteRun: async () => {},
});

export function RunProvider({ children }: { children: ReactNode }) {
  const [runId, setRunId] = useState("");
  const [runs, setRuns]   = useState<Run[]>([]);
  const [runsLoaded, setRunsLoaded] = useState(false);

  const refreshRuns = useCallback(async () => {
    try {
      const res = await fetch("/api/runs");
      const data = await res.json();
      if (!Array.isArray(data)) return;
      setRuns(data);
      setRunId(prev => prev || (data[0]?.run_id ?? ""));
    } catch {
      // swallow — sidebar will show "No runs available"
    } finally {
      setRunsLoaded(true);
    }
  }, []);

  const deleteRun = useCallback(async (id: string) => {
    const res = await fetch(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`Delete failed (${res.status}): ${body || res.statusText}`);
    }
    setRuns(prev => {
      const next = prev.filter(r => r.run_id !== id);
      setRunId(curr => (curr === id ? (next[0]?.run_id ?? "") : curr));
      return next;
    });
  }, []);

  useEffect(() => {
    refreshRuns();
  }, [refreshRuns]);

  return (
    <RunContext.Provider value={{ runId, setRunId, runs, runsLoaded, refreshRuns, deleteRun }}>
      {children}
    </RunContext.Provider>
  );
}

export const useRun = () => useContext(RunContext);
