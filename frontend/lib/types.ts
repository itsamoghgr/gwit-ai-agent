// Shared TypeScript types for the GW IT Dashboard

export interface Run {
  run_id: string;
  started_at: string;
  status: string;
  hidden?: boolean;
}

export interface Cluster {
  cluster_id: number;
  cluster_label: string;
  size: number;
  gap_flag: string;
  max_kb_sim: number;
  silhouette_score: number | null;
  pca_x: number | null;
  pca_y: number | null;
  summary: string | null;
  llm_kb_match: string | null;
  llm_confidence: string | null;
  llm_kb_reasoning: string | null;
  canonical_cluster_id: number | null;
  wo_tickets: number;
  inc_tickets: number;
}

export interface Ticket {
  cluster_id: number;
  source: string;
  ticket_number: string;
  service_type: string;
  assigned_group: string;
  problem_text: string;
  resolution_text: string;
}

export interface KBArticle {
  cluster_id: number;
  title: string;
  category: string;
  quality_score: number;
  confidence: string;
  needs_review: boolean;
  problem_statement: string;
  symptoms: string[] | null;
  resolution_steps: string[] | null;
  additional_notes: string | null;
  is_duplicate_of: string | null;
  wo_in_cluster: number;
}

export interface ExistingKB {
  id: string;
  title: string;
  issue: string;
  solution: string;
  util_status?: string;
  util_count?: number;
}

export interface SweepRow {
  k: number;
  inertia: number;
  silhouette: number;
  is_best_k: boolean;
}

export interface ServiceBreakdown {
  service_type: string;
  gap_flag: string;
  tickets: number;
}

export interface SourceMix {
  cluster_id: number;
  cluster_label: string;
  source: string;
  tickets: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: KBSource[];
}

export interface KBSource {
  title:        string;
  category:     string;
  snippet:      string;   // backend sends `snippet`, not `content`
  is_generated: boolean;
  similarity:   number;
}

// Alias used by page components
export type ClusterOut = Cluster;


export const GAP_COLORS: Record<string, string> = {
  CRITICAL:  "#f87171",  // red-400
  PARTIAL:   "#FFBE00",  // amber (original)
  COVERED:   "#4ade80",  // green-400
  DUPLICATE: "#94a3b8",  // slate-400
};




export const CONF_COLORS: Record<string, { fg: string; bg: string }> = {
  HIGH:   { fg: "#4ade80", bg: "rgba(34,197,94,0.12)"   },  // green-400
  MEDIUM: { fg: "#FFBE00", bg: "rgba(255,190,0,0.12)"   },  // amber
  LOW:    { fg: "#f87171", bg: "rgba(248,113,113,0.12)" },  // red-400
  NONE:   { fg: "#94a3b8", bg: "rgba(148,163,184,0.10)" },  // slate-400
};

export const UTIL_COLORS: Record<string, { fg: string; bg: string }> = {
  ACTIVE:       { fg: "#22c55e", bg: "rgba(34,197,94,0.15)"   },
  ORPHAN:       { fg: "#6b7280", bg: "rgba(107,114,128,0.15)" },
  "OVER-RELIED":{ fg: "#ef4444", bg: "rgba(239,68,68,0.15)"   },
  PERIPHERAL:   { fg: "#f59e0b", bg: "rgba(245,158,11,0.15)"  },
};

// ── Run Pipeline types ─────────────────────────────────────────────────────

export type PhaseRunStatus = "pending" | "running" | "complete" | "failed";
export type JobRunStatus   = "queued" | "running" | "complete" | "partial_failure" | "failed";

export interface PhaseInfo {
  phase: string;
  description: string;
}

export interface PhaseStatus {
  phase: string;
  status: PhaseRunStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  error: string | null;
  stats: Record<string, unknown> | null;
}

export interface JobStatus {
  job_id: string;
  run_id: string;
  status: JobRunStatus;
  phases: PhaseStatus[];
  started_at: string | null;
  finished_at: string | null;
  log_tail: string[];
}

export interface ProgressEvent {
  type: "progress";
  phase: string;
  label: string;
  sub_phase?: string;
  current?: number;
  total?: number;
  detail?: string;
  ts?: string;
}

export type PipelineSSEEvent =
  | { type: "snapshot"; job: JobStatus }
  | { type: "job_start"; job_id: string; run_id: string }
  | { type: "phase_start"; phase: string }
  | { type: "phase_complete"; phase: string; duration_s: number; stats: Record<string, unknown> | null }
  | { type: "phase_failed"; phase: string; duration_s: number; error: string }
  | { type: "log"; line: string }
  | ProgressEvent
  | { type: "done"; status: JobRunStatus; failed_phases: string[] }
  | { type: "error"; message: string };

// Per-phase activity feed entries — both free-form log lines and structured
// progress events are normalized into this shape inside the pipeline page.
export interface PhaseActivityEntry {
  kind: "log" | "progress";
  ts: string;                 // HH:MM:SS
  label: string;              // human-readable text
  sub_phase?: string;
  current?: number;
  total?: number;
  detail?: string;
}
