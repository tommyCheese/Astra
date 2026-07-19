import type { ConversationShare, ConversationShareSummary, ConversationSummary, ConversationView, RunView, SharedConversation } from './types';

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
  return new AstraApiError({ type: 'runtime.unclassified_response', code: 'UNCLASSIFIED_RESPONSE', message: '服务返回了未分类错误，暂时无法判断具体故障来源。请重启后端服务后重试。', retryable: response.status >= 500, trace_id: '未提供' });
}

export type ReasoningPolicyRequest = {
  reasoning_effort: 'fast' | 'balanced' | 'deep';
  max_tool_calls: number | null;
  planning_strategy: 'adaptive' | 'plan_first';
  reflection_enabled: boolean;
  reflection_trigger: 'failure_only' | 'adaptive' | 'every_turn';
  execution_mode: 'plan_only' | 'request_approval' | 'auto_approval';
  verification_level: 'basic' | 'standard' | 'strict';
};

export type RunModelConfig = { provider: string; name: string; api_key: string; base_url: string };
export type RuntimeDependency = { name: string; version: string };
export type RuntimeImage = { image: string; dependency_digest: string; dependencies: RuntimeDependency[]; activated_at: string | null };
export type RuntimeBuildStatus = 'queued' | 'building' | 'succeeded' | 'failed' | 'cancelled';
export type RuntimeBuild = {
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
  images?: RuntimeImage[];
  image_policy?: { keep_recent: number; retention_days: number };
};

export type ToolSetting = {
  name: 'web_search' | 'web_fetch' | 'chart_render' | 'bash_execute';
  label: string;
  description: string;
  enabled: boolean;
  available: boolean;
  unavailable_reason?: string | null;
};

export type ToolSettings = { tools: ToolSetting[] };

export type ConversationStrategyPreferences = {
  preferred_answer_mode: 'standard' | 'trusted';
  reasoning_effort: 'fast' | 'balanced' | 'deep';
  max_tool_calls: number | null;
  planning_strategy: 'adaptive' | 'plan_first';
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

export async function updateToolSettings(tools: ToolSetting[]): Promise<ToolSettings> {
  const enabled = Object.fromEntries(tools.map((tool) => [tool.name, tool.enabled]));
  const response = await fetch('/api/tools', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(enabled),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function activatePlan(runId: string): Promise<{ task_id: string; run_id: string; status: string }> {
  const response = await fetch(`/api/runs/${runId}/activate-plan`, { method: 'POST' });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export type TokenTotals = { input: number; cached_input: number; output: number; reasoning: number; total: number };
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

export async function createRun(goal: string, taskId: string | undefined, answerMode: 'standard' | 'trusted', reasoningPolicy?: ReasoningPolicyRequest, model?: RunModelConfig): Promise<{ run_id: string; task_id: string; status: string; answer_mode?: 'standard' | 'trusted' }> {
  const response = await fetchWithTimeout('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal, task_id: taskId, answer_mode: answerMode, reasoning_policy: reasoningPolicy, model }),
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function getRun(runId: string, signal?: AbortSignal): Promise<RunView> {
  const response = await fetch(`/api/runs/${runId}`, { signal });
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

export type WorkspaceView = {
  id: string;
  task_id: string;
  status: string;
  quotas: Record<string, number>;
  files: Array<{ id: string; path: string; status: string; mime_type?: string | null; size_bytes: number; deliverable_candidate: boolean; content_url?: string | null; deleted_at?: string | null }>;
  changes: Array<{ id: string; run_id: string; path: string; kind: string; created_at: string }>;
  checkpoints: Array<{ id: string; run_id: string; manifest_hash: string; file_count: number; created_at: string }>;
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

export async function getTaskWorkspace(taskId: string): Promise<WorkspaceView> {
  const response = await fetch(`/api/tasks/${taskId}/workspace`);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function listLibraryFiles(): Promise<LibraryFile[]> {
  const response = await fetch('/api/library/files');
  if (!response.ok) throw await responseError(response);
  return response.json();
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

export async function getConversation(id: string, signal?: AbortSignal): Promise<ConversationView> {
  const response = await fetch(`/api/conversations/${id}`, { signal });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function updateConversation(id: string, patch: { title?: string; pinned?: boolean }): Promise<ConversationSummary> {
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

export type RunStreamEvent = { id?: number; type: string; payload: Record<string, unknown>; created_at?: string };

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
  const response = await fetchWithTimeout(`/api/runs/${runId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, continuation_token: continuationToken, model }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}
