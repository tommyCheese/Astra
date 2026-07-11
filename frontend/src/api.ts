import type { RunView } from './types';

export type ReasoningPolicyRequest = {
  reasoning_effort: 'fast' | 'balanced' | 'deep';
  planning_strategy: 'direct' | 'adaptive' | 'plan_first';
  reflection_enabled: boolean;
  reflection_trigger: 'failure_only' | 'adaptive' | 'every_turn';
  execution_mode: 'plan_only' | 'request_approval' | 'auto_approval';
  verification_level: 'basic' | 'standard' | 'strict';
};

export async function createRun(goal: string, taskId?: string, reasoningPolicy?: ReasoningPolicyRequest): Promise<{ run_id: string; task_id: string; status: string }> {
  const response = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal, task_id: taskId, reasoning_policy: reasoningPolicy }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || '创建 run 失败');
  }
  return response.json();
}

export async function getRun(runId: string): Promise<RunView> {
  const response = await fetch(`/api/runs/${runId}`);
  if (!response.ok) {
    throw new Error('读取 run 失败');
  }
  return response.json();
}

export async function resumeRun(runId: string, content: string, continuationToken?: string): Promise<{ run_id: string; task_id: string; status: string }> {
  const response = await fetch(`/api/runs/${runId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, continuation_token: continuationToken }),
  });
  if (!response.ok) throw new Error(await response.text() || '恢复 run 失败');
  return response.json();
}
