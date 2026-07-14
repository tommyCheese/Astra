import type { RunStreamEvent } from './api';
import type { RunView } from './types';

export type ProcessItemStatus = 'running' | 'completed' | 'failed';

export type ProcessStreamItem = {
  id: string;
  kind: 'phase' | 'reasoning' | 'tool' | 'reflection' | 'verification';
  title: string;
  detail?: string;
  status: ProcessItemStatus;
  turnIndex?: number;
  toolCallId?: string;
  groupId?: string;
};

export type ProcessStreamState = {
  runId: string;
  items: ProcessStreamItem[];
  seenEventIds: number[];
  active: boolean;
};

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked', 'waiting_user', 'cancelled']);

const phaseTitles: Record<string, string> = {
  planning: '正在理解任务并制定计划',
  executing: '正在执行计划',
  selecting_action: '正在分析下一步',
  synthesizing: '正在组织回答',
  verifying: '正在验证结果',
};

export function createOptimisticProcessState(runId: string): ProcessStreamState {
  return {
    runId,
    active: true,
    seenEventIds: [],
    items: [{ id: 'phase-planning-0', kind: 'phase', title: phaseTitles.planning, status: 'running' }],
  };
}

export function reconcileProcessSnapshot(state: ProcessStreamState | null, run: RunView): ProcessStreamState {
  let next = state?.runId === run.id ? state : createOptimisticProcessState(run.id);
  for (const event of [...(run.events ?? [])].sort((a, b) => a.id - b.id)) {
    next = reduceProcessEvent(next, event);
  }
  const active = !terminalStatuses.has(run.status);
  const snapshotItems: ProcessStreamItem[] = [...next.items];
  const toolGroupById = new Map<string, string>();
  for (const turn of [...(run.turns ?? [])].sort((a, b) => a.turn_index - b.turn_index)) {
    const groupId = decisionGroupId(turn.turn_index);
    if (turn.tool_call_id) toolGroupById.set(turn.tool_call_id, groupId);
    if (!snapshotItems.some((item) => item.id === groupId)) {
      snapshotItems.push({
        id: groupId,
        kind: 'phase',
        title: phaseTitles.selecting_action,
        status: 'completed',
        turnIndex: turn.turn_index,
      });
    }
    const id = `reasoning-${turn.turn_index}`;
    const existing = snapshotItems.findIndex((item) => item.id === id);
    const item: ProcessStreamItem = {
      id,
      kind: turn.decision_type === 'reflect' ? 'reflection' : 'reasoning',
      title: turn.decision_type === 'reflect' ? '反思' : '思考',
      detail: turn.reflection ? String(turn.reflection.summary ?? turn.reasoning_summary) : turn.reasoning_summary,
      status: turn.status === 'failed' ? 'failed' : 'completed',
      turnIndex: turn.turn_index,
      toolCallId: turn.tool_call_id ?? undefined,
      groupId,
    };
    if (existing >= 0) snapshotItems[existing] = item;
    else snapshotItems.push(item);
  }
  for (const call of run.tool_calls ?? []) {
    const id = `tool-${call.id}`;
    const existing = snapshotItems.findIndex((item) => item.id === id);
    const existingItem = existing >= 0 ? snapshotItems[existing] : undefined;
    const groupId = toolGroupById.get(call.id) ?? existingItem?.groupId ?? (active ? activeDecisionGroupId(snapshotItems) : undefined);
    if (!groupId && !active && existing < 0) continue;
    const item: ProcessStreamItem = {
      id,
      kind: 'tool',
      title: call.tool_name,
      status: call.status === 'running' ? 'running' : call.status === 'failed' ? 'failed' : 'completed',
      toolCallId: call.id,
      groupId,
    };
    if (existing >= 0) snapshotItems[existing] = { ...snapshotItems[existing], ...item };
    else snapshotItems.push(item);
  }
  return {
    ...next,
    active,
    items: active ? snapshotItems : snapshotItems.map((item) => item.status === 'running' ? { ...item, status: 'completed' } : item),
  };
}

export function reduceProcessEvent(state: ProcessStreamState, event: RunStreamEvent): ProcessStreamState {
  if (typeof event.id === 'number' && state.seenEventIds.includes(event.id)) return state;
  const seenEventIds = typeof event.id === 'number' ? [...state.seenEventIds.slice(-199), event.id] : state.seenEventIds;
  const payload = event.payload;
  const turnIndex = numeric(payload.turn_index);
  let items = [...state.items];
  let active = state.active;

  if (event.type === 'reasoning.phase.started') {
    items = items.map((item) => item.kind === 'phase' && item.status === 'running' ? { ...item, status: 'completed' } : item);
    const phase = safeString(payload.phase) || 'working';
    const id = `phase-${phase}-${turnIndex ?? 0}`;
    items = upsert(items, {
      id,
      kind: 'phase',
      title: phaseTitles[phase] ?? '正在处理',
      status: 'running',
      turnIndex,
    });
  } else if (event.type === 'reasoning.summary.delta') {
    const id = `reasoning-${turnIndex ?? 0}`;
    const existing = items.find((item) => item.id === id);
    if (turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    items = upsert(items, {
      id,
      kind: 'reasoning',
      title: '思考',
      detail: `${existing?.detail ?? ''}${safeString(payload.delta)}`.slice(0, 4000),
      status: 'running',
      turnIndex,
      groupId: turnIndex === undefined ? undefined : decisionGroupId(turnIndex),
    });
  } else if (event.type === 'reasoning.summary.completed' || event.type === 'agent_turn.created') {
    const id = `reasoning-${turnIndex ?? 0}`;
    const existing = items.find((item) => item.id === id);
    if (turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    items = upsert(items, {
      id,
      kind: safeString(payload.decision_type) === 'reflect' ? 'reflection' : 'reasoning',
      title: safeString(payload.decision_type) === 'reflect' ? '反思' : '思考',
      detail: safeString(payload.summary) || safeString(payload.reasoning_summary) || existing?.detail,
      status: 'completed',
      turnIndex,
      toolCallId: safeString(payload.tool_call_id) || existing?.toolCallId,
      groupId: turnIndex === undefined ? existing?.groupId : decisionGroupId(turnIndex),
    });
  } else if (event.type === 'tool_call.started') {
    const toolCallId = safeString(payload.tool_call_id);
    if (turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    const groupId = turnIndex === undefined ? activeDecisionGroupId(items) : decisionGroupId(turnIndex);
    if (toolCallId) items = upsert(items, {
      id: `tool-${toolCallId}`,
      kind: 'tool',
      title: safeString(payload.tool_name) || '工具调用',
      status: 'running',
      toolCallId,
      groupId,
    });
  } else if (event.type === 'tool_call.completed') {
    const toolCallId = safeString(payload.tool_call_id);
    const existing = items.find((item) => item.id === `tool-${toolCallId}`);
    if (turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    if (toolCallId) items = upsert(items, {
      id: `tool-${toolCallId}`,
      kind: 'tool',
      title: safeString(payload.tool_name) || items.find((item) => item.id === `tool-${toolCallId}`)?.title || '工具调用',
      detail: safeString(payload.status),
      status: payload.status === 'failed' ? 'failed' : 'completed',
      toolCallId,
      groupId: existing?.groupId ?? (turnIndex === undefined ? activeDecisionGroupId(items) : decisionGroupId(turnIndex)),
    });
  } else if (event.type === 'reflection.created') {
    const summary = safeString(payload.summary);
    if (turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    items = upsert(items, {
      id: `reflection-${turnIndex ?? items.length}`,
      kind: 'reflection',
      title: '反思',
      detail: summary,
      status: 'completed',
      turnIndex,
      groupId: turnIndex === undefined ? activeDecisionGroupId(items) : decisionGroupId(turnIndex),
    });
  } else if (event.type === 'verification.created') {
    const notes = Array.isArray(payload.notes) ? payload.notes.filter((note): note is string => typeof note === 'string') : [];
    items = upsert(items, {
      id: 'verification',
      kind: 'verification',
      title: '验证',
      detail: notes.join('；') || safeString(payload.status),
      status: payload.status === 'failed' ? 'failed' : 'completed',
    });
  }

  const status = safeString(payload.status);
  if (terminalStatuses.has(status) || ['run.completed', 'run.failed', 'run.blocked', 'run.cancelled'].includes(event.type)) {
    active = false;
    items = items.map((item) => item.status === 'running' ? { ...item, status: 'completed' } : item);
  }
  return { ...state, items, active, seenEventIds };
}

export function isDecisionGroup(item: ProcessStreamItem) {
  return item.kind === 'phase' && item.id.startsWith('phase-selecting_action-');
}

function decisionGroupId(turnIndex: number) {
  return `phase-selecting_action-${turnIndex}`;
}

function activeDecisionGroupId(items: ProcessStreamItem[]) {
  return [...items].reverse().find(isDecisionGroup)?.id;
}

function ensureDecisionGroup(items: ProcessStreamItem[], turnIndex: number) {
  const id = decisionGroupId(turnIndex);
  if (items.some((item) => item.id === id)) return items;
  return [...items, {
    id,
    kind: 'phase' as const,
    title: phaseTitles.selecting_action,
    status: 'running' as const,
    turnIndex,
  }];
}

function upsert(items: ProcessStreamItem[], item: ProcessStreamItem) {
  const index = items.findIndex((candidate) => candidate.id === item.id);
  if (index < 0) return [...items, item];
  const next = [...items];
  next[index] = { ...items[index], ...item };
  return next;
}

function safeString(value: unknown) {
  return typeof value === 'string' ? value : '';
}

function numeric(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}
