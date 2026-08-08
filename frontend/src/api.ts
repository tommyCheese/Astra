import type { ConversationShare, ConversationShareSummary, ConversationSummary, ConversationView, PlanGraphDiff, PlanGraphSnapshot, RunView, SharedConversation } from './types';

export type ApiErrorPayload = { type: string; code: string; message: string; retryable: boolean; trace_id: string; details?: Record<string, unknown> };

export class AstraApiError extends Error {
  constructor(public readonly payload: ApiErrorPayload) { super(payload.message); }
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = 15000): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const abort = () => controller.abort();
  if (init.signal?.aborted) controller.abort(init.signal.reason);
  else init.signal?.addEventListener('abort', abort, { once: true });
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
    init.signal?.removeEventListener('abort', abort);
  }
}

async function responseError(response: Response): Promise<AstraApiError> {
  try {
    const body = await response.json() as { error?: ApiErrorPayload };
    if (body.error?.code && body.error.type) return new AstraApiError(body.error);
  } catch { /* use safe fallback */ }
  return new AstraApiError({ type: 'runtime.unclassified_response', code: 'UNCLASSIFIED_RESPONSE', message: '服务暂时无法完成请求，请稍后重试。若问题持续，请重新启动 Astra。', retryable: response.status >= 500, trace_id: '未提供' });
}

export type ReasoningPolicyRequest = {
  reasoning_effort: 'fast' | 'balanced' | 'deep';
  max_tool_calls: number | null;
  reflection_enabled: boolean;
  reflection_trigger: 'failure_only' | 'adaptive' | 'every_turn';
  execution_mode: 'request_approval' | 'auto_approval';
  verification_level: 'basic' | 'standard' | 'strict';
};

export type ModelThinkingDepth = 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
export type ModelThinkingSelection = {
  enabled: boolean;
  depth?: ModelThinkingDepth | null;
  capability_version: number;
};
export type ModelThinkingCapability = {
  provider: string;
  model: string;
  supported: boolean;
  toggle: 'optional' | 'always_on' | 'unavailable';
  depths: Array<{ id: ModelThinkingDepth; label: string }>;
  default_enabled: boolean;
  default_depth?: ModelThinkingDepth | null;
  reason?: string | null;
  adapter: string;
  capability_version: number;
};
export type RunModelConfig = {
  provider: string;
  name: string;
  api_key: string;
  base_url: string;
  thinking?: ModelThinkingSelection;
};
export type RuntimeDefaultModel = {
  provider: string;
  model: string;
  configured: boolean;
};
export type ModelConnectionTestResult = {
  connected: boolean;
  provider: string;
  model: string;
  message: string;
  latency_ms: number | null;
  error_code: string | null;
};
export type ModelContextCapability = {
  provider: string;
  model: string;
  window_tokens: number;
  max_output_tokens: number | null;
  source: 'catalog' | 'fallback';
  verified: boolean;
  documentation_url: string | null;
  capability_version: 2;
};
export type ContextWindowStatus = {
  provider: string;
  model: string;
  window_tokens: number;
  max_output_tokens: number | null;
  context_source: 'catalog' | 'fallback';
  context_verified: boolean;
  context_documentation_url: string | null;
  available_input_tokens: number;
  used_tokens: number;
  remaining_tokens: number;
  usage_ratio: number;
  auto_compact_ratio: number;
  status: 'normal' | 'warning' | 'compact_required' | 'overflow';
  estimated: boolean;
  summary_active: boolean;
  compaction_implementation?: 'astra_semantic' | 'deterministic_emergency' | null;
  compaction_failure_code?: string | null;
  checkpoint_status?: 'none' | 'active';
  window_number?: number;
  token_before?: number | null;
  token_after?: number | null;
  retained_run_count?: number;
  visible_run_count: number;
  folded_run_count: number;
  breakdown?: Array<{
    kind: 'system' | 'summary' | 'conversation' | 'draft' | 'output_reserve';
    tokens: number;
    item_count: number;
  }>;
  last_action: 'compact' | 'clear' | 'auto_compact' | null;
  last_action_at: string | null;
};
export type SlashSystemCommand = {
  name: 'compact' | 'clear' | 'schedule' | 'heartbeat' | 'subagent';
  command: string;
  description: string;
  effect: 'compact_context' | 'clear_context' | 'manage_schedules' | 'manage_heartbeat' | 'start_subagent_run';
  argument_mode: 'none' | 'optional' | 'required';
  default_arguments?: string;
  usage: string;
  side_effect: 'read' | 'write' | 'mixed';
  available: boolean;
  execution_mode?: 'host' | 'run';
  unavailable_reason?: string | null;
};
export type SlashCommandResult = {
  command: string;
  message: string;
  context: ContextWindowStatus;
  details: Record<string, unknown>;
  user_message: CommandMessage;
};

export type CommandMessage = {
  id: string;
  command: string;
  content: string;
  arguments: string;
  assistant_content?: string;
  after_run_count: number;
  created_at: string;
};

export type ScheduleSpec =
  | { type: 'once'; at: string }
  | { type: 'interval'; interval_seconds: number; anchor_at?: string | null }
  | { type: 'cron'; expression: string };
type ScheduledTaskBase = {
  id: string;
  name: string;
  owner_principal: string | null;
  prompt: string;
  timezone: string;
  enabled: boolean;
  misfire_policy: 'skip' | 'fire_once';
  misfire_grace_seconds: number;
  overlap_policy: 'skip';
  execution: Record<string, unknown>;
  next_fire_at: string | null;
  last_fire_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};
type HeartbeatSettings = { active_hours?: { start: string; end: string } | null; prompt?: string };
export type ScheduledTask = ScheduledTaskBase & (
  | {
    kind: 'agent';
    system_managed: false;
    schedule_type: 'once' | 'interval' | 'cron';
    schedule: ScheduleSpec;
    heartbeat: Record<string, never>;
    target_task_id: string | null;
  }
  | {
    kind: 'heartbeat';
    system_managed: true;
    schedule_type: 'interval';
    schedule: Extract<ScheduleSpec, { type: 'interval' }>;
    heartbeat: HeartbeatSettings;
    target_task_id: string | null;
  }
);
export type ScheduledTaskRun = {
  id: string;
  job_id: string;
  scheduled_for: string;
  trigger_type: string;
  status: string;
  task_id: string | null;
  run_id: string | null;
  outcome: Record<string, unknown>;
  claimed_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type Deliverable = {
  id: string;
  job_id: string | null;
  job_name?: string | null;
  job_kind?: 'agent' | 'heartbeat' | null;
  schedule_run_id: string | null;
  trigger_type?: string | null;
  run_id: string | null;
  task_id: string;
  conversation_title?: string;
  kind: 'result' | 'file' | 'data' | 'receipt';
  title: string;
  summary: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  content_url: string | null;
  external_url: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};
export type ScheduledDeliverable = Deliverable;

export async function listScheduledTasks(signal?: AbortSignal): Promise<ScheduledTask[]> {
  const response = await fetchWithTimeout('/api/schedules', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTask[]>;
}

export async function createScheduledTask(
  payload: Record<string, unknown>,
): Promise<ScheduledTask> {
  const response = await fetchWithTimeout('/api/schedules', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTask>;
}

export async function updateScheduledTask(
  id: string,
  payload: Record<string, unknown>,
): Promise<ScheduledTask> {
  const response = await fetchWithTimeout(`/api/schedules/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTask>;
}

export async function setScheduledTaskEnabled(task: ScheduledTask, enabled: boolean): Promise<ScheduledTask> {
  const response = await fetchWithTimeout(`/api/schedules/${task.id}/${enabled ? 'resume' : 'pause'}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: task.version }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTask>;
}

export async function deleteScheduledTask(task: ScheduledTask): Promise<void> {
  const response = await fetchWithTimeout(`/api/schedules/${task.id}?version=${task.version}`, { method: 'DELETE' });
  if (!response.ok) throw await responseError(response);
}

export async function runScheduledTask(id: string): Promise<ScheduledTaskRun> {
  const response = await fetchWithTimeout(`/api/schedules/${id}/run`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTaskRun>;
}

export async function listScheduledTaskRuns(id: string, signal?: AbortSignal): Promise<ScheduledTaskRun[]> {
  const response = await fetchWithTimeout(`/api/schedules/${id}/runs`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTaskRun[]>;
}

export async function listScheduledDeliverables(id: string, signal?: AbortSignal): Promise<ScheduledDeliverable[]> {
  const response = await fetchWithTimeout(`/api/schedules/${id}/deliverables`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledDeliverable[]>;
}

export async function updateHeartbeat(payload: Record<string, unknown>): Promise<ScheduledTask> {
  const response = await fetchWithTimeout('/api/heartbeat', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTask>;
}

export async function disableHeartbeat(): Promise<ScheduledTask> {
  const response = await fetchWithTimeout('/api/heartbeat/disable', { method: 'POST' });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ScheduledTask>;
}

export async function getRuntimeDefaultModel(signal?: AbortSignal): Promise<RuntimeDefaultModel> {
  const response = await fetchWithTimeout('/api/models/default', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<RuntimeDefaultModel>;
}

export async function testModelConnection(
  model: RunModelConfig,
  signal?: AbortSignal,
): Promise<ModelConnectionTestResult> {
  const response = await fetchWithTimeout('/api/models/test-connection', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider: model.provider,
      model: model.name,
      api_key: model.api_key,
      base_url: model.base_url,
    }),
    signal,
  }, 20000);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<ModelConnectionTestResult>;
}

export async function resolveModelThinkingCapabilities(
  models: Array<{ provider: string; model: string }>,
  signal?: AbortSignal,
): Promise<ModelThinkingCapability[]> {
  if (!models.length) return [];
  const response = await fetchWithTimeout('/api/models/thinking-capabilities/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ models }),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  const body = await response.json() as { capabilities: ModelThinkingCapability[] };
  return body.capabilities;
}

export async function resolveModelContextCapabilities(
  models: Array<{ provider: string; model: string }>,
  signal?: AbortSignal,
): Promise<ModelContextCapability[]> {
  if (!models.length) return [];
  const response = await fetch('/api/models/context-capabilities/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ models }),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  const body = await response.json() as { capabilities: ModelContextCapability[] };
  return body.capabilities;
}
export type RuntimeDependency = { name: string; version: string };
export type AgentProfileDocuments = { identity: string; soul: string; memory: string; autodream: string };
export type RuntimeAgentProfile = { source: 'default' | 'user'; version: string; documents: AgentProfileDocuments; default_documents?: AgentProfileDocuments };
export type MemoryRuntimeSettings = {
  write_enabled: boolean;
  recall_enabled: boolean;
  retrieval_max_items: number;
  retrieval_max_tokens: number;
  retrieval_min_confidence: number;
  retrieval_min_score: number;
  autodream_enabled: boolean;
  autodream_scan_seconds: number;
  autodream_min_candidates: number;
};
type RuntimeImage = { image: string; dependency_digest: string; dependencies: RuntimeDependency[]; activated_at: string | null };
type RuntimeBuildStatus = 'queued' | 'building' | 'succeeded' | 'failed' | 'cancelled';
type RuntimeBuild = {
  id: string;
  status: RuntimeBuildStatus;
  phase?: string;
  progress?: number;
  log: string;
  image?: string | null;
};
export type RuntimeProfile = {
  dependencies: RuntimeDependency[];
  core_dependencies: RuntimeDependency[];
  active_image: string;
  dependency_digest: string;
  build: RuntimeBuild | null;
  agent_profile?: RuntimeAgentProfile;
  memory_settings?: MemoryRuntimeSettings;
  images?: RuntimeImage[];
  image_policy?: { keep_recent: number; retention_days: number };
};

export type ToolSetting = {
  name: string;
  provider_id: string;
  version: string;
  label: string;
  description: string;
  enabled: boolean;
  available: boolean;
  health: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  unavailable_reason?: string | null;
};

export type ToolProviderSetting = {
  provider_id: string;
  label: string;
  version: string;
  enabled: boolean;
  state: string;
  health: string;
  available: boolean;
  unavailable_reason?: string | null;
  configuration_schema: Record<string, unknown>;
  configuration: Record<string, unknown>;
  configuration_revision: string;
};

export type ToolSettings = { tools: ToolSetting[]; providers: ToolProviderSetting[] };

export type SkillDiagnostic = {
  code: string;
  message: string;
  severity: 'info' | 'warning' | 'error' | 'critical' | string;
  path?: string | null;
  line?: number | null;
  column?: number | null;
  details?: Record<string, unknown>;
};
export type SkillRevision = {
  id: string;
  version: number;
  digest: string;
  published_at?: string | null;
  revoked_at?: string | null;
  test_only: boolean;
  diagnostics: SkillDiagnostic[];
};
export type SkillRevisionDetail = SkillRevision & {
  files: SkillFile[];
};
export type SkillRevisionDiff = {
  skill_id: string;
  base_revision_id: string;
  target_revision_id: string;
  base_version: number;
  target_version: number;
  patch: string;
  files: Array<{ path: string; status: string; patch: string }>;
};
export type SkillFile = {
  path: string;
  uri: string;
  digest: string;
  size_bytes: number;
  media_type: string;
  kind: string;
  text: boolean;
  readonly: boolean;
};
export type SkillSummary = {
  id: string;
  name: string;
  qualified_identity: string;
  origin: 'builtin' | 'custom';
  description: string;
  enabled: boolean;
  readonly: boolean;
  lifecycle_state: string;
  active_revision?: SkillRevision | null;
  draft_revision_token?: string | null;
  diagnostics: SkillDiagnostic[];
  created_at: string;
  updated_at: string;
};
export type SkillDetail = SkillSummary & {
  files: SkillFile[];
  requested_tool_patterns: string[];
  compatibility?: string | null;
};
export type SkillFiles = {
  skill_id: string;
  revision_token: string;
  readonly: boolean;
  files: SkillFile[];
  diagnostics: SkillDiagnostic[];
};
export type SkillFileContent = SkillFile & {
  content?: string | null;
  content_base64?: string | null;
};

async function skillJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export const listSkills = (signal?: AbortSignal) =>
  skillJson<SkillSummary[]>('/api/skills', { signal });
export const getSkill = (skillId: string, signal?: AbortSignal) =>
  skillJson<SkillDetail>(`/api/skills/${skillId}`, { signal });
export const getSkillFile = (skillId: string, path: string, signal?: AbortSignal) =>
  skillJson<SkillFileContent>(`/api/skills/${skillId}/draft/file?path=${encodeURIComponent(path)}`, { signal });
export const createSkill = (name: string, description: string) =>
  skillJson<SkillDetail>('/api/skills', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, description }),
  });
export const importSkill = (filename: string, contentBase64: string) =>
  skillJson<SkillDetail>('/api/skills/import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename, content_base64: contentBase64 }),
  });
export const cloneSkill = (skillId: string, name: string) =>
  skillJson<SkillDetail>(`/api/skills/${skillId}/clone`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
export const updateSkillFiles = (
  skillId: string,
  revisionToken: string,
  operations: Array<{ action: 'write' | 'delete' | 'move'; path: string; target?: string; content?: string; content_base64?: string }>,
) => skillJson<SkillFiles>(`/api/skills/${skillId}/draft/files`, {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision_token: revisionToken, operations }),
});
export const validateSkill = (skillId: string) =>
  skillJson<{ valid: boolean; publishable: boolean; digest?: string | null; diagnostics: SkillDiagnostic[] }>(
    `/api/skills/${skillId}/validate`, { method: 'POST' },
  );
export const publishSkill = (skillId: string, revisionToken: string) =>
  skillJson<SkillRevision>(`/api/skills/${skillId}/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision_token: revisionToken }),
  });
export const listSkillRevisions = (skillId: string) =>
  skillJson<SkillRevision[]>(`/api/skills/${skillId}/revisions`);
export const getSkillRevision = (skillId: string, revisionId: string) =>
  skillJson<SkillRevisionDetail>(`/api/skills/${skillId}/revisions/${revisionId}`);
export const getSkillRevisionFile = (skillId: string, revisionId: string, path: string) =>
  skillJson<SkillFileContent>(
    `/api/skills/${skillId}/revisions/${revisionId}/file?path=${encodeURIComponent(path)}`,
  );
export const getSkillRevisionDiff = (skillId: string, revisionId: string) =>
  skillJson<SkillRevisionDiff>(`/api/skills/${skillId}/revisions/${revisionId}/diff`);
export const restoreSkillRevision = (skillId: string, revisionId: string) =>
  skillJson<SkillFiles>(`/api/skills/${skillId}/revisions/${revisionId}/restore`, { method: 'POST' });
export const getSkillDiff = (skillId: string) =>
  skillJson<{ files: Array<{ path: string; status: string; patch?: string | null }> }>(`/api/skills/${skillId}/diff`);
export const setSkillEnabled = (skillId: string, enabled: boolean) =>
  skillJson<SkillSummary>(`/api/skills/${skillId}/state`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
  });
export async function removeSkill(skillId: string): Promise<void> {
  const response = await fetch(`/api/skills/${skillId}`, { method: 'DELETE' });
  if (!response.ok) throw await responseError(response);
}
export const testSkillDraft = (
  skillId: string, revisionToken: string, goal: string, answerMode: 'standard' | 'trusted',
) => skillJson<{ run_id: string; task_id: string; status: string; answer_mode: string }>(
  `/api/skills/${skillId}/test-runs`,
  { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision_token: revisionToken, goal, answer_mode: answerMode }) },
);
export type RunSkillsAudit = {
  run_id: string;
  catalog_digest: string;
  answer_mode: string;
  draft_test: boolean;
  catalog: Array<Record<string, unknown>>;
  activations: Array<Record<string, unknown>>;
  resource_reads: Array<Record<string, unknown>>;
  attributed_actions: Array<Record<string, unknown>>;
  plan_bindings: Array<Record<string, unknown>>;
};
export const getRunSkills = (runId: string) =>
  skillJson<RunSkillsAudit>(`/api/runs/${runId}/skills`);

export type ConversationStrategyPreferences = {
  preferred_answer_mode: 'standard' | 'trusted';
  reasoning_effort: 'fast' | 'balanced' | 'deep';
  max_tool_calls: number | null;
  reflection_enabled: boolean;
  reflection_trigger: 'failure_only' | 'adaptive' | 'every_turn';
};

export async function getConversationStrategy(signal?: AbortSignal): Promise<ConversationStrategyPreferences> {
  const response = await fetch('/api/preferences/conversation-strategy', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateConversationStrategy(strategy: ConversationStrategyPreferences): Promise<ConversationStrategyPreferences> {
  const response = await fetch('/api/preferences/conversation-strategy', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(strategy),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function getToolSettings(signal?: AbortSignal): Promise<ToolSettings> {
  const response = await fetch('/api/tools', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateToolState(name: string, enabled: boolean): Promise<ToolSettings> {
  const response = await fetch(`/api/tools/${encodeURIComponent(name)}/state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateToolProviderState(providerId: string, enabled: boolean): Promise<ToolSettings> {
  const response = await fetch(`/api/tool-providers/${encodeURIComponent(providerId)}/state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateToolProviderConfiguration(providerId: string, configuration: Record<string, unknown>): Promise<ToolSettings> {
  const response = await fetch(`/api/tool-providers/${encodeURIComponent(providerId)}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ configuration }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

type TokenTotals = { input: number; cached_input: number; output: number; reasoning: number; total: number };
export type UsageSummary = {
  scope: 'all' | 'task' | 'run';
  from?: string | null;
  to?: string | null;
  overview: {
    model_invocations: number; successful_invocations: number; failed_invocations: number; interrupted_invocations: number;
    agent_turns: number; tool_calls: number; successful_tool_calls: number; failed_tool_calls: number;
    tool_success_rate: number | null; memories: number; sandbox_jobs: number; artifacts: number; artifact_bytes: number;
  };
  tokens: TokenTotals;
  coverage: { reported_invocations: number; total_invocations: number; ratio: number; complete: boolean };
  trend: Array<{ date: string; invocations: number; tokens: number; tool_calls: number }>;
  models: Array<{ provider: string; model: string; invocations: number; reported_invocations: number; tokens: TokenTotals }>;
  tools: Array<{ tool_name: string; calls: number; succeeded: number; failed: number; success_rate: number | null }>;
};

export async function getUsageSummary(params: { scope: 'all' | 'task' | 'run'; taskId?: string; runId?: string; from?: string }, signal?: AbortSignal): Promise<UsageSummary> {
  const query = new URLSearchParams({ scope: params.scope });
  if (params.taskId) query.set('task_id', params.taskId);
  if (params.runId) query.set('run_id', params.runId);
  if (params.from) query.set('from', params.from);
  const response = await fetch(`/api/usage/summary?${query}`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function getRuntimeProfile(signal?: AbortSignal): Promise<RuntimeProfile> {
  const response = await fetch('/api/runtime', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function buildRuntime(dependencies: RuntimeDependency[]): Promise<RuntimeProfile> {
  const response = await fetch('/api/runtime/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dependencies }) });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function cancelRuntimeBuild(buildId: string): Promise<RuntimeProfile> {
  const response = await fetch('/api/runtime/build/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ build_id: buildId }) });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateRuntimeAgentProfile(documents: AgentProfileDocuments): Promise<RuntimeAgentProfile> {
  const response = await fetch('/api/runtime/agent-profile', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ documents }) });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function resetRuntimeAgentProfile(): Promise<RuntimeAgentProfile> {
  const response = await fetch('/api/runtime/agent-profile/reset', { method: 'POST' });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateRuntimeMemorySettings(settings: MemoryRuntimeSettings): Promise<MemoryRuntimeSettings> {
  const response = await fetch('/api/runtime/memory-settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export type CreatedRun = { run_id: string; task_id: string; status: string; answer_mode?: 'standard' | 'trusted' };
const createdRunStreams = new Map<string, RunStreamHandle>();
const MEMORY_SESSION_STORAGE_KEY = 'astra.memory-session-id.v1';

function memorySessionId(): string {
  try {
    const existing = globalThis.sessionStorage?.getItem(MEMORY_SESSION_STORAGE_KEY);
    if (existing) return existing;
    const generated = globalThis.crypto?.randomUUID?.() ?? `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    globalThis.sessionStorage?.setItem(MEMORY_SESSION_STORAGE_KEY, generated);
    return generated;
  } catch {
    return `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function createRunBody(goal: string, taskId: string | undefined, answerMode: 'standard' | 'trusted', reasoningPolicy?: ReasoningPolicyRequest, model?: RunModelConfig, planExecution?: 'auto' | 'confirm', skillIds: string[] | undefined = [], subagentMode: 'auto' | 'required' = 'auto'): string {
  return JSON.stringify({ goal, task_id: taskId, session_id: memorySessionId(), answer_mode: answerMode, reasoning_policy: reasoningPolicy, model, skill_ids: skillIds ?? [], subagent_mode: subagentMode, ...(answerMode === 'trusted' ? { plan_execution: planExecution ?? 'confirm' } : {}) });
}

export async function createRun(goal: string, taskId: string | undefined, answerMode: 'standard' | 'trusted', reasoningPolicy?: ReasoningPolicyRequest, model?: RunModelConfig, planExecution?: 'auto' | 'confirm', skillIds: string[] | undefined = [], subagentMode: 'auto' | 'required' = 'auto'): Promise<CreatedRun> {
  const stream = createRunStream(goal, taskId, answerMode, reasoningPolicy, model, planExecution, skillIds, subagentMode);
  try {
    const created = await stream.created;
    createdRunStreams.set(created.run_id, stream);
    return created;
  } catch (error) {
    stream.close();
    throw error;
  }
}

export async function getRun(runId: string, signal?: AbortSignal, detail: 'full' | 'initial' = 'full'): Promise<RunView> {
  const query = detail === 'initial' ? '?detail=initial' : '';
  const response = await fetch(`/api/runs/${runId}${query}`, { signal });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function cancelRun(runId: string): Promise<RunView> {
  const response = await fetch(`/api/runs/${runId}/cancel`, { method: 'POST' });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function cancelSubagent(runId: string, agentExecutionId: string): Promise<RunView> {
  const response = await fetch(`/api/runs/${runId}/agents/${agentExecutionId}/cancel`, { method: 'POST' });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function decideToolApproval(
  runId: string,
  approvalId: string,
  decision: 'approve_once' | 'allow_similar' | 'allow_task' | 'reject',
  continuationToken: string,
  model?: RunModelConfig,
): Promise<{ run_id: string; task_id: string; status: string }> {
  const response = await fetchWithTimeout(`/api/runs/${runId}/approvals/${approvalId}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, continuation_token: continuationToken, model }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export type PermissionCenterView = {
  grants: Array<{ id: string; scope: string; tool_name: string; tool_version?: string | null; effect_kinds: string[]; resource_matcher: Record<string, unknown>; invocation_constraints?: Record<string, unknown>; status: string; use_count: number; max_uses?: number | null; expires_at?: string | null; created_at?: string | null }>;
  identities: Array<{ id: string; type: string; principal: string; trust_level: string; task_id?: string | null; run_id?: string | null; parent_identity_id?: string | null; attributes?: Record<string, unknown>; created_at?: string | null; revoked_at?: string | null }>;
  delegations: Array<{ id: string; parent_identity_id: string; child_identity_id: string; delegated_scope: Record<string, unknown>; expires_at?: string | null; revoked_at?: string | null }>;
  credentials: Array<{ id: string; service: string; scopes: string[]; expires_at: string; revoked_at?: string | null }>;
  data_flow?: Record<string, unknown> | null;
  tool_catalog?: { digest: string; catalog: Array<Record<string, unknown>> } | null;
  policy_explanations?: Array<{ id: number; type: string; payload: Record<string, unknown>; created_at: string }>;
};

export type LibraryFile = {
  id: string;
  task_id: string;
  conversation_title: string;
  path: string;
  mime_type?: string | null;
  size_bytes: number;
  security_status: string;
  deliverable_candidate: boolean;
  content_url?: string | null;
  created_at: string;
  updated_at: string;
};

export async function getPermissionCenter(runId: string): Promise<PermissionCenterView> {
  const response = await fetch(`/api/runs/${runId}/permissions`);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function revokePermissionGrant(grantId: string): Promise<void> {
  const response = await fetch(`/api/permission-grants/${grantId}`, { method: 'DELETE' });
  if (!response.ok) throw await responseError(response);
}

export async function listLibraryFiles(): Promise<LibraryFile[]> {
  const response = await fetch('/api/library/files');
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function listLibraryDeliverables(signal?: AbortSignal): Promise<Deliverable[]> {
  const response = await fetch('/api/library/deliverables', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<Deliverable[]>;
}

export async function listRuns(limit = 100): Promise<RunView[]> {
  const response = await fetch(`/api/runs?limit=${limit}`);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function listConversations(limit = 100): Promise<ConversationSummary[]> {
  const response = await fetch(`/api/conversations?limit=${limit}`);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function createConversation(
  title: string,
  preferredAnswerMode: 'standard' | 'trusted' = 'standard',
): Promise<ConversationSummary> {
  const response = await fetch('/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, preferred_answer_mode: preferredAnswerMode }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function getConversation(id: string, signal?: AbortSignal): Promise<ConversationView> {
  const response = await fetch(`/api/conversations/${id}`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function listSystemCommands(signal?: AbortSignal): Promise<SlashSystemCommand[]> {
  const response = await fetch('/api/system-commands', { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function getConversationContext(
  id: string,
  provider: string,
  model: string,
  draft = '',
  signal?: AbortSignal,
): Promise<ContextWindowStatus> {
  const query = new URLSearchParams({ provider, model });
  if (draft) query.set('draft', draft);
  const response = await fetchWithTimeout(
    `/api/conversations/${encodeURIComponent(id)}/context?${query}`,
    { signal },
  );
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function executeConversationCommand(
  id: string,
  command: string,
  provider: string,
  model: string,
  argumentsText = '',
): Promise<SlashCommandResult> {
  const query = new URLSearchParams({ provider, model });
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(id)}/commands/${encodeURIComponent(command)}?${query}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ arguments: argumentsText }),
    },
  );
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateConversation(id: string, patch: { title?: string; pinned?: boolean; preferred_answer_mode?: 'standard' | 'trusted' }): Promise<ConversationSummary> {
  const response = await fetch(`/api/conversations/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const response = await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
  if (!response.ok) throw await responseError(response);
}

export async function createConversationShare(id: string, refresh = false): Promise<ConversationShare> {
  const response = await fetch(`/api/conversations/${id}/share`, { method: refresh ? 'PUT' : 'POST' });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function listConversationShares(): Promise<ConversationShareSummary[]> {
  const response = await fetch('/api/conversation-shares');
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function revokeConversationShare(id: string): Promise<void> {
  const response = await fetch(`/api/conversations/${id}/share`, { method: 'DELETE' });
  if (!response.ok) throw await responseError(response);
}

export async function getSharedConversation(token: string, signal?: AbortSignal): Promise<SharedConversation> {
  const response = await fetch(`/api/shared-conversations/${encodeURIComponent(token)}`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export type RunStreamEvent = {
  id?: number;
  run_sequence?: number | null;
  agent_execution_id?: string | null;
  agent_sequence?: number | null;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};
export type RunStreamHandle = {
  created: Promise<CreatedRun>;
  subscribe: (onEvent: (event: RunStreamEvent) => void, onError?: () => void) => () => void;
  close: () => void;
};

export function takeCreatedRunStream(runId: string): RunStreamHandle | undefined {
  const stream = createdRunStreams.get(runId);
  createdRunStreams.delete(runId);
  return stream;
}

async function consumeSse(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: RunStreamEvent) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (data) onEvent(JSON.parse(data) as RunStreamEvent);
      boundary = buffer.indexOf('\n\n');
    }
    if (done) break;
  }
}

export function createRunStream(goal: string, taskId: string | undefined, answerMode: 'standard' | 'trusted', reasoningPolicy?: ReasoningPolicyRequest, model?: RunModelConfig, planExecution?: 'auto' | 'confirm', skillIds: string[] | undefined = [], subagentMode: 'auto' | 'required' = 'auto'): RunStreamHandle {
  const controller = new AbortController();
  const readyTimer = window.setTimeout(() => controller.abort(), 15000);
  const queued: RunStreamEvent[] = [];
  let listener: ((event: RunStreamEvent) => void) | undefined;
  let errorListener: (() => void) | undefined;
  let closed = false;
  let ready = false;
  let streamEnded = false;
  let resolveCreated!: (created: CreatedRun) => void;
  let rejectCreated!: (error: unknown) => void;
  const created = new Promise<CreatedRun>((resolve, reject) => {
    resolveCreated = resolve;
    rejectCreated = reject;
  });

  void (async () => {
    try {
      const response = await fetch('/api/runs/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: createRunBody(goal, taskId, answerMode, reasoningPolicy, model, planExecution, skillIds, subagentMode),
        signal: controller.signal,
      });
      if (!response.ok) throw await responseError(response);
      if (!response.body) throw new Error('Streaming response body is unavailable');
      await consumeSse(response.body, (event) => {
        if (!ready && event.type === 'stream.ready') {
          const payload = event.payload as Partial<CreatedRun>;
          if (payload.run_id && payload.task_id && payload.status) {
            ready = true;
            window.clearTimeout(readyTimer);
            resolveCreated(payload as CreatedRun);
          }
        }
        if (listener) listener(event);
        else queued.push(event);
      });
      streamEnded = true;
      window.clearTimeout(readyTimer);
      if (!ready && !closed) rejectCreated(new Error('Run stream ended before ready'));
      else if (!closed) errorListener?.();
    } catch (error) {
      window.clearTimeout(readyTimer);
      if (closed) return;
      streamEnded = true;
      if (!ready) rejectCreated(error);
      errorListener?.();
    }
  })();

  return {
    created,
    subscribe(onEvent, onError) {
      listener = onEvent;
      errorListener = onError;
      for (const event of queued.splice(0)) onEvent(event);
      if (streamEnded) onError?.();
      return () => {
        if (listener === onEvent) listener = undefined;
        if (errorListener === onError) errorListener = undefined;
      };
    },
    close() {
      closed = true;
      window.clearTimeout(readyTimer);
      controller.abort();
    },
  };
}

export function streamRunEvents(runId: string, onEvent: (event: RunStreamEvent) => void, onError?: () => void): () => void {
  if (typeof EventSource === 'undefined') return () => undefined;
  const source = new EventSource(`/api/runs/${runId}/events`);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as RunStreamEvent);
    } catch {
      onError?.();
    }
  };
  source.onerror = () => {
    source.close();
    onError?.();
  };
  return () => source.close();
}

export async function resumeRun(runId: string, content: string, continuationToken?: string, model?: RunModelConfig): Promise<{ run_id: string; task_id: string; status: string }> {
  return postRunResume(runId, { content, continuation_token: continuationToken, model });
}

type RunResumeResult = { run_id: string; task_id: string; status: string };

async function postRunResume(runId: string, body: object, timeout?: number): Promise<RunResumeResult> {
  const response = await fetchWithTimeout(`/api/runs/${runId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, timeout);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function confirmPlanExecution(
  runId: string,
  confirmation: { continuationToken: string; planId: string; planVersion: number; stateVersion: number },
  model?: RunModelConfig,
): Promise<{ run_id: string; task_id: string; status: string }> {
  return postRunResume(runId, {
    action: 'execute_plan',
    continuation_token: confirmation.continuationToken,
    plan_id: confirmation.planId,
    expected_plan_version: confirmation.planVersion,
    expected_state_version: confirmation.stateVersion,
    model,
  });
}

export async function revisePlan(
  runId: string,
  request: string,
  confirmation: { continuationToken: string; planId: string; planVersion: number; stateVersion: number },
  model?: RunModelConfig,
): Promise<{ run_id: string; task_id: string; status: string }> {
  return postRunResume(runId, {
    action: 'revise_plan',
    content: request,
    continuation_token: confirmation.continuationToken,
    plan_id: confirmation.planId,
    expected_plan_version: confirmation.planVersion,
    expected_state_version: confirmation.stateVersion,
    model,
  }, 60000);
}

export async function getPlanVersion(runId: string, version: number, signal?: AbortSignal): Promise<PlanGraphSnapshot> {
  const response = await fetch(`/api/runs/${runId}/plans/${version}`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function getPlanVersionDiff(runId: string, version: number, fromVersion: number, signal?: AbortSignal): Promise<PlanGraphDiff> {
  const response = await fetch(`/api/runs/${runId}/plans/${version}/diff?from_version=${fromVersion}`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}
