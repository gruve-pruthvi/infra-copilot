// Shared API contract types for infra-copilot.

export type Tier = "read" | "mutating" | "destructive";

// ---------- Sessions / chat ----------

export interface SessionSummary {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ToolCallRecord {
  tool: string;
  server: string;
  tier: Tier;
  args: Record<string, unknown>;
  tool_call_id: string;
  result_summary?: string | null;
}

export interface ChatMessage {
  id: string | number;
  role: "user" | "assistant";
  content: string;
  tool_calls: ToolCallRecord[] | null;
  usage?: unknown;
  created_at: string;
}

export interface PendingApproval {
  id: string;
  session_id: string;
  tool: string;
  server: string;
  tier: Tier;
  args: Record<string, unknown>;
  plan_summary: string;
  expires_at: string;
}

export interface SessionDetail extends SessionSummary {
  messages: ChatMessage[];
  pending_approvals: Array<Omit<PendingApproval, "session_id"> & { session_id?: string }>;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  requests: number;
}

// ---------- Chat SSE stream events ----------

export type ChatEvent =
  | { type: "status"; status: string }
  | { type: "text_delta"; text: string }
  | {
      type: "tool_call_started";
      tool: string;
      server: string;
      tier: Tier;
      args: Record<string, unknown>;
      tool_call_id: string;
    }
  | { type: "tool_result"; tool: string; tool_call_id: string; result_summary: string }
  | { type: "approval_required"; approval: PendingApproval }
  | { type: "turn_paused" }
  | { type: "done"; usage: Usage }
  | { type: "error"; message: string };

// ---------- WS /ws/events snapshots ----------

/** Raw Docker port map: {"6443/tcp": [{HostIp, HostPort}], "80/tcp": null} */
export type PortsMap = Record<string, Array<{ HostIp?: string; HostPort?: string }> | null>;

export interface ContainerInfo {
  id: string;
  name: string;
  image: string;
  status: string;
  restart_count: number;
  exit_code: number | null;
  started_at: string | null;
  ports: PortsMap | null;
  cpu_percent: number | null;
  memory_usage_bytes: number | null;
  memory_percent: number | null;
}

export interface ContainersSnapshot {
  available: boolean;
  error: string | null;
  containers: ContainerInfo[];
}

export interface SystemMetrics {
  ts: number | string;
  cpu_percent: number;
  cpu_count: number;
  memory_percent: number;
  memory_used_bytes: number;
  memory_total_bytes: number;
  disk_percent: number;
  disk_used_bytes: number;
  disk_total_bytes: number;
}

export interface HistoryPoint {
  ts: number | string;
  cpu_percent: number;
  memory_percent: number;
}

export interface K8sPod {
  name: string;
  namespace: string;
  phase: string;
  restarts: number;
}

export interface K8sDeployment {
  name: string;
  namespace: string;
  ready: string;
}

export interface K8sSnapshot {
  available: boolean;
  reason?: string;
  namespaces?: string[];
  pods?: K8sPod[];
  deployments?: K8sDeployment[];
}

// ---------- Actions / history ----------

export type ActionStatus = "executed" | "denied" | "failed" | "dry_run";

export interface ActionRecord {
  id: string | number;
  created_at: string;
  session_id: string | null;
  server: string;
  tool: string;
  tier: Tier;
  args?: Record<string, unknown> | null;
  status: ActionStatus;
  result_summary?: string | null;
  error?: string | null;
  duration_ms: number | null;
  approval_id?: string | null;
}

// ---------- Settings ----------

export interface SettingsValues {
  active_provider: string;
  active_model: string;
  dry_run_mode: "true" | "false";
  anthropic_api_key: string;
  openai_api_key: string;
  google_api_key: string;
}

export interface ProviderCatalogEntry {
  label: string;
  models: string[];
  default: string;
}

export interface SettingsResponse {
  settings: SettingsValues;
  catalog: Record<string, ProviderCatalogEntry>;
}

export interface TestProviderResult {
  ok: boolean;
  reply?: string;
  error?: string;
}

// ---------- Health ----------

export interface HealthView {
  reachable: boolean;
  docker: boolean;
  k8s: boolean;
  providerConfigured: boolean;
}
