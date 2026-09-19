export type Row = { id: string; created_at: string; [key: string]: unknown };
export interface Incident extends Row {
  title: string;
  description: string;
  status: string;
  component: string;
  severity: string;
  task_id: string | null;
  worker: string | null;
  queue: string | null;
  symptoms: Record<string, unknown>;
  recovered_at: string | null;
  occurrence_count: number;
  investigation_requested: boolean;
}
export interface Task extends Row {
  name: string;
  status: string;
  worker: string | null;
  queue: string;
  retry_count: number;
  idempotent: boolean;
  quarantined: boolean;
  exception: string | null;
  started_at: string | null;
  completed_at: string | null;
}
export interface Detail {
  incident: Incident;
  investigations: Row[];
  evidence: Row[];
  steps: Row[];
  diagnoses: Row[];
  verifications: Row[];
  remediations: Row[];
  executions: Row[];
  events: Row[];
  usage: Row[];
  approvals: Row[];
}
export interface Sample {
  at: string;
  tasks_per_minute: number;
  failure_rate: number | null;
  mean_latency_seconds: number | null;
  queue_depths: Record<string, number>;
  active_workers: number;
  active_incidents: number;
}
export interface Summary {
  latest: Sample | null;
  task_counts: Record<string, number>;
  incident_count: number;
  recovered_incidents: number;
  worker_count: number;
}
export interface Statistic {
  n: number;
  mean: number | null;
  median: number | null;
  sample_sd: number | null;
}
export interface Comparison {
  configuration: string;
  runs_with_metrics: number;
  metrics: Record<string, Statistic>;
  detection: Record<string, number | null>;
}
export interface Results {
  runs: Row[];
  comparisons: Comparison[];
  total_runs: number;
  skipped_runs: number;
  summary: Omit<Comparison, "configuration">;
}
export const configurations = [
  "RULE_BASED",
  "LLM_ONLY",
  "LLM_RAG",
  "AGENT_TOOLS",
  "AGENT_RAG",
  "FULL_SYSTEM",
];
export const faults = [
  "API_TIMEOUT",
  "DEPENDENCY_ERROR",
  "WORKER_FAILURE",
  "TASK_EXCEPTION",
  "POISON_TASK",
  "BROKER_DISRUPTION",
  "DATABASE_FAILURE",
  "OVERLOAD",
  "BAD_CONFIGURATION",
  "DEPLOYMENT_REGRESSION",
  "INTERMITTENT_FAILURE",
  "HEALTHY",
];
export const taskNames = [
  "send_email",
  "process_report",
  "resize_image",
  "call_external_api",
  "process_database_record",
  "data_processing_task",
];
