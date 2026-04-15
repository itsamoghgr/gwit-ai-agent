"use client";

import { createContext, useContext, useState, ReactNode } from "react";

interface RunCtx {
  runId: string;
  setRunId: (id: string) => void;
}

const RunContext = createContext<RunCtx>({ runId: "", setRunId: () => {} });

export function RunProvider({ children }: { children: ReactNode }) {
  const [runId, setRunId] = useState("");
  return (
    <RunContext.Provider value={{ runId, setRunId }}>
      {children}
    </RunContext.Provider>
  );
}

export const useRun = () => useContext(RunContext);
