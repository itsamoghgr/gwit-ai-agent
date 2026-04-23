"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, Loader2 } from "lucide-react";

import { useRun } from "@/lib/RunContext";
import {
  listPhases, startPipeline, streamPipeline, getPipelineJob,
  type StreamSource,
} from "@/lib/pipeline";
import type {
  PhaseInfo, PhaseStatus, JobRunStatus, PipelineSSEEvent, PhaseActivityEntry,
} from "@/lib/types";

import PhaseSelector   from "@/components/pipeline/PhaseSelector";
import PhaseCard       from "@/components/pipeline/PhaseCard";
import PipelineSummary from "@/components/pipeline/PipelineSummary";

const ACTIVE_JOB_KEY = "pipeline:active";
const TERMINAL: readonly JobRunStatus[] = ["complete", "partial_failure", "failed"];
const MAX_BUFFER_PER_PHASE = 20;

function buildInitialPhases(catalog: PhaseInfo[], selected: Set<string>): PhaseStatus[] {
  return catalog
    .filter(p => selected.has(p.phase))
    .map(p => ({
      phase: p.phase, status: "pending",
      started_at: null, finished_at: null, duration_s: null, error: null, stats: null,
    }));
}

function nowHms(): string {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}

function logTextToEntry(line: string): PhaseActivityEntry {
  // Strip leading "[HH:MM:SS] " if present.
  const m = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s+(.*)$/);
  if (m) return { kind: "log", ts: m[1], label: m[2] };
  return { kind: "log", ts: nowHms(), label: line };
}

export default function PipelinePage() {
  const { setRunId, refreshRuns } = useRun();

  const [catalog, setCatalog]       = useState<PhaseInfo[]>([]);
  const [selected, setSelected]     = useState<Set<string>>(new Set());
  const [reuseRunId, setReuseRunId] = useState("");

  const [jobId, setJobId]             = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [jobStatus, setJobStatus]     = useState<JobRunStatus | "idle">("idle");
  const [phaseStates, setPhaseStates] = useState<PhaseStatus[]>([]);
  const [phaseEvents, setPhaseEvents] = useState<Record<string, PhaseActivityEntry[]>>({});
  const [rawLog, setRawLog]           = useState<string[]>([]);
  const [currentPhase, setCurrentPhase] = useState<string | null>(null);
  const [failedPhases, setFailedPhases] = useState<string[]>([]);
  const [totalDuration, setTotalDuration] = useState(0);
  const [errMsg, setErrMsg]           = useState<string | null>(null);
  const [streamSource, setStreamSource] = useState<StreamSource | null>(null);

  const cancelRef = useRef<(() => void) | null>(null);
  // Tracks the job_id we're currently subscribed to. Used to dedupe the
  // onRun→sessionStorage→restore-effect path under React Strict Mode's
  // double-invoke, which would otherwise open two EventSources for one job.
  const subscribedJobRef = useRef<string | null>(null);

  useEffect(() => {
    listPhases()
      .then(data => {
        setCatalog(data);
        setSelected(new Set(data.map(p => p.phase)));
      })
      .catch(err => setErrMsg(String(err)));
  }, []);

  useEffect(() => () => { cancelRef.current?.(); }, []);

  const describeMap = useMemo(
    () => Object.fromEntries(catalog.map(p => [p.phase, p.description] as const)),
    [catalog],
  );

  const isRunning = jobStatus === "running" || jobStatus === "queued";
  const isDone    = jobStatus === "complete" || jobStatus === "partial_failure" || jobStatus === "failed";

  function togglePhase(p: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
  }

  function appendEntry(phase: string, entry: PhaseActivityEntry) {
    setPhaseEvents(prev => {
      const existing = prev[phase] ?? [];
      const next = [...existing, entry];
      if (next.length > MAX_BUFFER_PER_PHASE) next.splice(0, next.length - MAX_BUFFER_PER_PHASE);
      return { ...prev, [phase]: next };
    });
  }

  const applyEvent = useCallback((event: PipelineSSEEvent) => {
    switch (event.type) {
      case "snapshot": {
        setJobStatus(event.job.status);
        setPhaseStates(event.job.phases);
        setRawLog(event.job.log_tail);
        // Seed per-phase buffers best-effort: snapshot carries combined log_tail
        // without per-phase attribution, so we leave phaseEvents empty here.
        const running = event.job.phases.find(p => p.status === "running");
        if (running) setCurrentPhase(running.phase);
        break;
      }
      case "job_start":
        setJobStatus("running");
        break;
      case "phase_start":
        setCurrentPhase(event.phase);
        setPhaseStates(prev => prev.map(p =>
          p.phase === event.phase
            ? { ...p, status: "running", started_at: new Date().toISOString() }
            : p
        ));
        break;
      case "phase_complete":
        setPhaseStates(prev => prev.map(p =>
          p.phase === event.phase
            ? { ...p, status: "complete", duration_s: event.duration_s, stats: event.stats }
            : p
        ));
        setCurrentPhase(prev => prev === event.phase ? null : prev);
        break;
      case "phase_failed":
        setPhaseStates(prev => prev.map(p =>
          p.phase === event.phase
            ? { ...p, status: "failed", duration_s: event.duration_s, error: event.error }
            : p
        ));
        setFailedPhases(prev => [...prev, event.phase]);
        setCurrentPhase(prev => prev === event.phase ? null : prev);
        break;
      case "progress": {
        const entry: PhaseActivityEntry = {
          kind: "progress",
          ts: (event.ts ?? new Date().toISOString()).slice(11, 19) || nowHms(),
          label: event.label,
          sub_phase: event.sub_phase,
          current: event.current,
          total: event.total,
          detail: event.detail,
        };
        appendEntry(event.phase, entry);
        break;
      }
      case "log": {
        const entry = logTextToEntry(event.line);
        setRawLog(prev => [...prev, event.line].slice(-500));
        // Route to the currently-running phase if known; otherwise drop into raw only.
        setCurrentPhase(curr => {
          if (curr) appendEntry(curr, entry);
          return curr;
        });
        break;
      }
      case "done":
        setJobStatus(event.status);
        setFailedPhases(event.failed_phases);
        setCurrentPhase(null);
        try { sessionStorage.removeItem(ACTIVE_JOB_KEY); } catch {}
        refreshRuns();
        break;
      case "error":
        setErrMsg(event.message);
        break;
    }
  }, [refreshRuns]);

  const subscribeAndPersist = useCallback((newJobId: string, newRunId: string) => {
    // Dedupe: if we're already subscribed to this job, leave it alone.
    // Without this, Strict Mode's double-mount opens a second EventSource for
    // the same job and the browser's close of the first one triggers onerror
    // → polling fallback before any events land.
    if (subscribedJobRef.current === newJobId) return;

    cancelRef.current?.();
    subscribedJobRef.current = newJobId;
    try {
      sessionStorage.setItem(
        ACTIVE_JOB_KEY,
        JSON.stringify({ job_id: newJobId, run_id: newRunId }),
      );
    } catch {}

    const startedAt = performance.now();
    cancelRef.current = streamPipeline(newJobId, {
      onEvent: (ev) => {
        applyEvent(ev);
        if (ev.type === "done") {
          setTotalDuration((performance.now() - startedAt) / 1000);
          subscribedJobRef.current = null;
        }
      },
      onError: (err) => setErrMsg(String(err)),
      onSource: (s) => setStreamSource(s),
    });
  }, [applyEvent]);

  // Resume an in-flight job after a page refresh.
  useEffect(() => {
    let raw: string | null = null;
    try { raw = sessionStorage.getItem(ACTIVE_JOB_KEY); } catch { return; }
    if (!raw) return;

    let parsed: { job_id?: string; run_id?: string } = {};
    try { parsed = JSON.parse(raw); } catch { sessionStorage.removeItem(ACTIVE_JOB_KEY); return; }
    const savedJobId = parsed.job_id;
    if (!savedJobId) { sessionStorage.removeItem(ACTIVE_JOB_KEY); return; }

    // If onRun just persisted this same job in the current render, don't
    // open a second stream — subscribeAndPersist's dedupe would block it,
    // but skipping the fetch is cleaner.
    if (subscribedJobRef.current === savedJobId) return;

    let disposed = false;
    (async () => {
      try {
        const job = await getPipelineJob(savedJobId);
        if (disposed) return;
        setJobId(job.job_id);
        setActiveRunId(job.run_id);
        applyEvent({ type: "snapshot", job });
        if (TERMINAL.includes(job.status)) {
          try { sessionStorage.removeItem(ACTIVE_JOB_KEY); } catch {}
          return;
        }
        subscribeAndPersist(job.job_id, job.run_id);
      } catch (err) {
        if (disposed) return;
        setErrMsg(String(err));
        try { sessionStorage.removeItem(ACTIVE_JOB_KEY); } catch {}
      }
    })();
    return () => { disposed = true; };
  }, [applyEvent, subscribeAndPersist]);

  async function onRun() {
    setErrMsg(null);
    setFailedPhases([]);
    setRawLog([]);
    setPhaseEvents({});
    setCurrentPhase(null);
    setTotalDuration(0);

    const phasesOrdered = catalog.map(p => p.phase).filter(p => selected.has(p));
    if (phasesOrdered.length === 0) {
      setErrMsg("Pick at least one phase.");
      return;
    }

    setPhaseStates(buildInitialPhases(catalog, selected));
    setJobStatus("queued");

    let job;
    try {
      job = await startPipeline(phasesOrdered, reuseRunId.trim() || null);
    } catch (err) {
      setErrMsg(String(err));
      setJobStatus("failed");
      return;
    }

    setJobId(job.job_id);
    setActiveRunId(job.run_id);
    subscribeAndPersist(job.job_id, job.run_id);
  }

  return (
    <div className="w-full">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-base-content tracking-tight">Run Pipeline</h1>
        <p className="text-sm text-base-content/45 mt-0.5">
          Kick off a pipeline run and watch phase-by-phase progress live.
        </p>
      </div>

      {errMsg && (
        <div className="mb-4 p-3 rounded-lg bg-error/10 border border-error/30 text-[12px] text-error">
          {errMsg}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-5">
        <div className="space-y-4">
          <PhaseSelector
            phases={catalog}
            selected={selected}
            onToggle={togglePhase}
            onSelectAll={() => setSelected(new Set(catalog.map(p => p.phase)))}
            onClear={() => setSelected(new Set())}
            disabled={isRunning}
          />

          <div className="bg-base-100 border border-base-300 rounded-xl p-5">
            <label className="block text-[11px] font-bold uppercase tracking-widest text-base-content/40 mb-2">
              Reuse run_id (optional)
            </label>
            <input
              type="text"
              value={reuseRunId}
              onChange={e => setReuseRunId(e.target.value)}
              disabled={isRunning}
              placeholder="leave empty for a new run"
              className="input input-bordered input-sm w-full text-[12px] font-mono bg-base-100"
            />
            <p className="text-[10px] text-base-content/40 mt-2 leading-relaxed">
              Leave empty to mint a new run. If a single phase is selected without a run_id,
              the backend reuses the latest run whose prerequisite table has rows.
            </p>
          </div>

          <button
            type="button"
            onClick={onRun}
            disabled={isRunning || selected.size === 0}
            className="btn btn-primary w-full"
          >
            {isRunning
              ? <><Loader2 size={14} className="animate-spin mr-1" />Running…</>
              : <><Play size={14} className="mr-1" />Run pipeline</>}
          </button>
        </div>

        <div className="space-y-4">
          <div className="bg-base-100 border border-base-300 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <p className="text-[11px] font-bold uppercase tracking-widest text-base-content/40">
                Progress
              </p>
              {isRunning && streamSource && (
                <span
                  className={
                    "text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded " +
                    (streamSource === "sse"
                      ? "bg-success/10 text-success"
                      : "bg-warning/10 text-warning")
                  }
                >
                  {streamSource === "sse" ? "Live" : "Polling"}
                </span>
              )}
            </div>
            {phaseStates.length === 0 ? (
              <p className="text-[12px] text-base-content/40 italic">
                Select phases and click <strong>Run pipeline</strong> to start.
              </p>
            ) : (
              <div className="space-y-2">
                {phaseStates.map(p => (
                  <PhaseCard
                    key={p.phase}
                    phase={p}
                    description={describeMap[p.phase] ?? ""}
                    events={phaseEvents[p.phase] ?? []}
                  />
                ))}
              </div>
            )}
          </div>

          {isDone && activeRunId && (
            <PipelineSummary
              status={jobStatus}
              runId={activeRunId}
              totalDurationS={totalDuration}
              failedPhases={failedPhases}
              onViewRun={() => setRunId(activeRunId)}
            />
          )}

          {jobId && (
            <p className="text-[10px] text-base-content/30 font-mono">
              job_id: {jobId}
            </p>
          )}

          {rawLog.length > 0 && (
            <details className="bg-base-100 border border-base-300 rounded-xl overflow-hidden">
              <summary className="cursor-pointer px-4 py-2 text-[11px] font-bold uppercase
                                  tracking-widest text-base-content/40 hover:bg-base-200">
                Show raw stream · {rawLog.length} lines
              </summary>
              <pre className="p-4 text-[11px] font-mono text-base-content/75 leading-relaxed
                             overflow-auto max-h-72 whitespace-pre-wrap break-all
                             border-t border-base-300">
                {rawLog.join("\n")}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
