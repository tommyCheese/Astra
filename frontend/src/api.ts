import type { RunView } from './types';

export async function createRun(goal: string): Promise<{ run_id: string; task_id: string; status: string }> {
  const response = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal }),
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
