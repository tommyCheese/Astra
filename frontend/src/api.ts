import type { RunView } from './types';

export type ApiErrorPayload = { type: string; code: string; message: string; retryable: boolean; trace_id: string; details?: Record<string, unknown> };

export class AstraApiError extends Error {
  constructor(public readonly payload: ApiErrorPayload) { super(payload.message); }
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
  planning_strategy: 'direct' | 'adaptive' | 'plan_first';
  reflection_enabled: boolean;
  reflection_trigger: 'failure_only' | 'adaptive' | 'every_turn';
  execution_mode: 'plan_only' | 'request_approval' | 'auto_approval';
  verification_level: 'basic' | 'standard' | 'strict';
};

export type RunModelConfig = { provider: string; name: string; api_key: string; base_url: string };

export async function createRun(goal: string, taskId?: string, reasoningPolicy?: ReasoningPolicyRequest, model?: RunModelConfig): Promise<{ run_id: string; task_id: string; status: string }> {
  const response = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal, task_id: taskId, reasoning_policy: reasoningPolicy, model }),
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function getRun(runId: string): Promise<RunView> {
  const response = await fetch(`/api/runs/${runId}`);
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function listRuns(limit = 100): Promise<RunView[]> {
  const response = await fetch(`/api/runs?limit=${limit}`);
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

export async function resumeRun(runId: string, content: string, continuationToken?: string): Promise<{ run_id: string; task_id: string; status: string }> {
  const response = await fetch(`/api/runs/${runId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, continuation_token: continuationToken }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}
