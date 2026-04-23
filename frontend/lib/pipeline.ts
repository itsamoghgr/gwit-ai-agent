// lib/pipeline.ts — API client for the Run Pipeline feature.
// Handles SSE streaming with a polling fallback when EventSource errors,
// never opens, or opens but never delivers a single event (silent buffer).

import type { PhaseInfo, JobStatus, PipelineSSEEvent } from "@/lib/types";

export async function listPhases(): Promise<PhaseInfo[]> {
  const res = await fetch("/api/pipeline/phases");
  if (!res.ok) throw new Error(`listPhases: ${res.status}`);
  return res.json();
}

export async function startPipeline(
  phases: string[] | null,
  runId: string | null,
): Promise<{ job_id: string; run_id: string }> {
  const res = await fetch("/api/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      phases: phases && phases.length ? phases : null,
      run_id: runId || null,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`startPipeline: ${res.status} ${text}`);
  }
  return res.json();
}

export async function getPipelineJob(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/api/pipeline/jobs/${jobId}`);
  if (!res.ok) throw new Error(`getPipelineJob: ${res.status}`);
  return res.json();
}

export async function cancelPipelineByRunId(runId: string): Promise<void> {
  const res = await fetch(
    `/api/pipeline/jobs/by-run/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`cancelPipeline: ${res.status} ${body || res.statusText}`);
  }
}

export type StreamSource = "sse" | "poll";

export interface StreamHandlers {
  onEvent: (event: PipelineSSEEvent) => void;
  onError?: (err: unknown) => void;
  onSource?: (source: StreamSource) => void;
}

const SSE_WATCHDOG_MS = 5000;
const POLL_INTERVAL_MS = 2000;

/**
 * Subscribe to a job's SSE stream. Returns a cancel function.
 * Falls back to polling getPipelineJob every 2s if EventSource errors out,
 * fails to open, or opens but delivers no bytes within SSE_WATCHDOG_MS.
 */
export function streamPipeline(jobId: string, handlers: StreamHandlers): () => void {
  let cancelled = false;
  let es: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let watchdog: ReturnType<typeof setTimeout> | null = null;
  let gotFirstEvent = false;
  let currentSource: StreamSource | null = null;

  const setSource = (s: StreamSource) => {
    if (currentSource === s) return;
    currentSource = s;
    handlers.onSource?.(s);
  };

  const clearWatchdog = () => {
    if (watchdog) { clearTimeout(watchdog); watchdog = null; }
  };

  const stopPolling = () => {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  };

  const startPolling = (reason: string) => {
    if (cancelled) return;
    console.log(`[pipeline-sse] falling back to polling (${reason})`);
    setSource("poll");
    const tick = async () => {
      if (cancelled) return;
      try {
        const job = await getPipelineJob(jobId);
        handlers.onEvent({ type: "snapshot", job });
        const terminal = ["complete", "partial_failure", "failed"].includes(job.status);
        if (terminal) {
          handlers.onEvent({ type: "done", status: job.status, failed_phases: [] });
          return;
        }
      } catch (err) {
        handlers.onError?.(err);
      }
      pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
    };
    tick();
  };

  console.log(`[pipeline-sse] opening stream for job ${jobId}`);
  try {
    es = new EventSource(`/api/pipeline/stream/${jobId}`);
    es.onopen = () => {
      console.log("[pipeline-sse] open");
      setSource("sse");
      watchdog = setTimeout(() => {
        if (!gotFirstEvent && !cancelled) {
          console.log("[pipeline-sse] watchdog timeout — no events after open, falling back");
          es?.close();
          es = null;
          startPolling("watchdog");
        }
      }, SSE_WATCHDOG_MS);
    };
    es.onmessage = (ev) => {
      if (!gotFirstEvent) {
        gotFirstEvent = true;
        clearWatchdog();
        console.log("[pipeline-sse] first event");
        setSource("sse");
      }
      if (ev.data === "[DONE]") {
        es?.close();
        return;
      }
      try {
        const parsed = JSON.parse(ev.data) as PipelineSSEEvent;
        handlers.onEvent(parsed);
      } catch (err) {
        handlers.onError?.(err);
      }
    };
    es.onerror = () => {
      // EventSource fires onerror for both fatal errors AND transient reconnects,
      // and always on close. Use readyState to distinguish:
      //   CONNECTING (0) → browser is retrying on its own; don't fall back.
      //   CLOSED    (2) → terminal; only fall back if we never received anything
      //                   AND the caller didn't cancel us first.
      const state = es?.readyState;
      if (cancelled) {
        console.log("[pipeline-sse] error after cancel — ignoring");
        return;
      }
      if (state === EventSource.CONNECTING) {
        console.log("[pipeline-sse] transient error — browser reconnecting");
        return;
      }
      if (state === EventSource.CLOSED) {
        clearWatchdog();
        es = null;
        if (gotFirstEvent) {
          console.log("[pipeline-sse] closed after events received — stopping");
          return;
        }
        console.log("[pipeline-sse] closed before first event — falling back");
        startPolling("sse error");
      }
    };
  } catch (err) {
    console.log("[pipeline-sse] constructor threw", err);
    handlers.onError?.(err);
    startPolling("sse constructor");
  }

  return () => {
    console.log("[pipeline-sse] cancelled");
    cancelled = true;
    clearWatchdog();
    es?.close();
    stopPolling();
  };
}
