import type { RunStreamEvent } from './api';
import type { RunView } from './types';

type ProcessItemStatus = 'running' | 'completed' | 'failed' | 'cancelled';

export type ProcessStreamItem = {
  id: string;
  kind: 'phase' | 'reasoning' | 'model_thinking' | 'tool' | 'reflection' | 'verification';
  title: string;
  detail?: string;
  status: ProcessItemStatus;
  turnIndex?: number;
  toolCallId?: string;
  groupId?: string;
  provider?: string;
  operation?: string;
  contentLevel?: 'reasoning' | 'summary' | 'unavailable';
  truncated?: boolean;
  unavailableReason?: string;
};

export type ProcessStreamState = {
  runId: string;
  answerMode: 'standard' | 'trusted';
  items: ProcessStreamItem[];
  seenEventIds: number[];
  runCursor: number;
  agentCursors: Record<string, number>;
  cursorGap: boolean;
  active: boolean;
};

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked', 'waiting_user', 'cancelled']);

const phaseTitles: Record<string, string> = {
  planning: '正在理解任务并制定计划',
  executing: '正在执行计划',
  selecting_action: '正在分析下一步',
  processing_result: '正在评估执行结果',
  synthesizing: '正在组织回答',
  verifying: '正在验证结果',
};

export function createOptimisticProcessState(
  runId: string,
  answerMode: 'standard' | 'trusted' = 'trusted',
): ProcessStreamState {
  return {
    runId,
    answerMode,
    active: true,
    seenEventIds: [],
    runCursor: 0,
    agentCursors: {},
    cursorGap: false,
    items: answerMode === 'standard'
      ? [{ id: 'reasoning-0', kind: 'reasoning', title: '思考', status: 'running' }]
      : [{ id: 'phase-planning-0', kind: 'phase', title: phaseTitles.planning, status: 'running' }],
  };
}

export function reconcileProcessSnapshot(state: ProcessStreamState | null, run: RunView): ProcessStreamState {
  const answerMode: ProcessStreamState['answerMode'] = run.answer_mode === 'standard' ? 'standard' : 'trusted';
  let next: ProcessStreamState = state?.runId === run.id
    ? {
      ...state,
      answerMode,
      seenEventIds: [],
      runCursor: 0,
      agentCursors: {},
      cursorGap: false,
    }
    : createOptimisticProcessState(run.id, answerMode);
  for (const event of [...(run.events ?? [])].sort((a, b) => a.id - b.id)) {
    next = reduceProcessEvent(next, event);
  }
  const active = !terminalStatuses.has(run.status);
  const snapshotItems: ProcessStreamItem[] = [...next.items];
  const toolGroupById = new Map<string, string>();
  const linkedToolCallIds = new Set(
    (run.turns ?? []).flatMap((turn) => turn.tool_call_id ? [turn.tool_call_id] : []),
  );
  if (answerMode === 'standard' && (run.turns ?? []).length > 0) {
    const placeholderIndex = snapshotItems.findIndex((item) => item.id === 'reasoning-0' && !item.detail);
    if (placeholderIndex >= 0) snapshotItems.splice(placeholderIndex, 1);
  }
  for (const turn of [...(run.turns ?? [])].sort((a, b) => a.turn_index - b.turn_index)) {
    const groupId = answerMode === 'trusted' ? decisionGroupId(turn.turn_index) : undefined;
    if (turn.tool_call_id && groupId) toolGroupById.set(turn.tool_call_id, groupId);
    if (groupId && !snapshotItems.some((item) => item.id === groupId)) {
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
      status: turn.status === 'failed' ? 'failed' : turn.status === 'cancelled' ? 'cancelled' : 'completed',
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
    if (!active && existing < 0) {
      const hasTimelineContext = answerMode === 'standard'
        ? linkedToolCallIds.has(call.id)
        : Boolean(groupId);
      if (!hasTimelineContext) continue;
    }
    const item: ProcessStreamItem = {
      id,
      kind: 'tool',
      title: call.tool_name,
      status: call.status === 'running' ? 'running' : call.status === 'failed' ? 'failed' : call.status === 'cancelled' ? 'cancelled' : 'completed',
      toolCallId: call.id,
      groupId,
    };
    if (existing >= 0) snapshotItems[existing] = { ...snapshotItems[existing], ...item };
    else snapshotItems.push(item);
  }
  const visibleSnapshotItems = active ? snapshotItems : snapshotItems.filter((item) => !isProcessingResultHandoff(item));
  return {
    ...next,
    active,
    items: active ? visibleSnapshotItems : visibleSnapshotItems.map((item) => item.status === 'running' ? { ...item, status: run.status === 'cancelled' ? 'cancelled' : 'completed' } : item),
  };
}

export function reduceProcessEvent(state: ProcessStreamState, event: RunStreamEvent): ProcessStreamState {
  if (typeof event.id === 'number' && state.seenEventIds.includes(event.id)) return state;
  const runSequence = numeric(event.run_sequence);
  if (runSequence !== undefined && runSequence <= state.runCursor) return state;
  const agentExecutionId = safeString(event.agent_execution_id);
  const agentSequence = numeric(event.agent_sequence);
  if (agentExecutionId && agentSequence !== undefined && agentSequence <= (state.agentCursors[agentExecutionId] ?? 0)) return state;
  const cursorGap = state.cursorGap
    || (runSequence !== undefined && state.runCursor > 0 && runSequence > state.runCursor + 1)
    || Boolean(agentExecutionId && agentSequence !== undefined && (state.agentCursors[agentExecutionId] ?? 0) > 0 && agentSequence > (state.agentCursors[agentExecutionId] ?? 0) + 1);
  const runCursor = runSequence ?? state.runCursor;
  const agentCursors = agentExecutionId && agentSequence !== undefined
    ? { ...state.agentCursors, [agentExecutionId]: agentSequence }
    : state.agentCursors;
  const seenEventIds = typeof event.id === 'number' ? [...state.seenEventIds.slice(-199), event.id] : state.seenEventIds;
  const payload = event.payload;
  const turnIndex = numeric(payload.turn_index);
  let items = [...state.items];
  let active = state.active;
  const quickMode = state.answerMode === 'standard';

  if (event.type === 'reasoning.phase.started') {
    if (quickMode) return { ...state, active, seenEventIds, runCursor, agentCursors, cursorGap };
    items = items
      .filter((item) => !isProcessingResultHandoff(item))
      .map((item) => item.kind === 'phase' && item.status === 'running' ? { ...item, status: 'completed' } : item);
    const phase = safeString(payload.phase) || 'working';
    const id = `phase-${phase}-${turnIndex ?? 0}`;
    items = upsert(items, {
      id,
      kind: 'phase',
      title: phaseTitles[phase] ?? '正在处理',
      status: 'running',
      turnIndex,
    });
  } else if (
    event.type === 'reasoning.summary.delta'
    || event.type === 'reasoning.summary.completed'
    || event.type === 'agent_turn.created'
  ) {
    const completed = event.type !== 'reasoning.summary.delta';
    const id = `reasoning-${turnIndex ?? 0}`;
    if (quickMode && id !== 'reasoning-0') {
      items = items.filter((item) => item.id !== 'reasoning-0' || Boolean(item.detail));
    }
    const existing = items.find((item) => item.id === id);
    if (!quickMode && turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    const reflected = completed && safeString(payload.decision_type) === 'reflect';
    items = upsert(items, {
      id,
      kind: reflected ? 'reflection' : 'reasoning',
      title: reflected ? '反思' : '思考',
      detail: completed
        ? safeString(payload.summary) || safeString(payload.reasoning_summary) || existing?.detail
        : `${existing?.detail ?? ''}${safeString(payload.delta)}`.slice(0, 4000),
      status: completed ? 'completed' : 'running',
      turnIndex,
      groupId: quickMode
        ? undefined
        : turnIndex === undefined
          ? completed ? existing?.groupId : undefined
          : decisionGroupId(turnIndex),
      ...(completed && { toolCallId: safeString(payload.tool_call_id) || existing?.toolCallId }),
    });
  } else if (event.type.startsWith('model_thinking.')) {
    const streamId = safeString(payload.stream_id);
    const id = `model-thinking-${streamId}`;
    const existing = items.find((item) => item.id === id);
    const contentLevel = payload.content_level === 'summary'
      ? 'summary'
      : payload.content_level === 'reasoning'
        ? 'reasoning'
        : 'unavailable';
    const title = contentLevel === 'summary' ? '供应商思考摘要' : '模型思考';
    if (streamId && event.type === 'model_thinking.started') {
      items = upsert(items, {
        id,
        kind: 'model_thinking',
        title,
        detail: existing?.detail ?? '',
        status: 'running',
        provider: safeString(payload.provider),
        operation: safeString(payload.operation),
        contentLevel,
      });
    } else if (streamId && event.type === 'model_thinking.delta') {
      items = upsert(items, {
        id,
        kind: 'model_thinking',
        title,
        detail: `${existing?.detail ?? ''}${safeString(payload.delta)}`,
        status: 'running',
        provider: safeString(payload.provider) || existing?.provider,
        operation: safeString(payload.operation) || existing?.operation,
        contentLevel: contentLevel === 'unavailable' ? existing?.contentLevel : contentLevel,
      });
    } else if (streamId && event.type === 'model_thinking.completed') {
      items = upsert(items, {
        id,
        kind: 'model_thinking',
        title: existing?.title ?? title,
        detail: existing?.detail ?? '',
        status: payload.status === 'failed' ? 'failed' : 'completed',
        provider: safeString(payload.provider) || existing?.provider,
        operation: safeString(payload.operation) || existing?.operation,
        contentLevel: contentLevel === 'unavailable' ? existing?.contentLevel : contentLevel,
        truncated: payload.truncated === true,
      });
    } else if (streamId && event.type === 'model_thinking.unavailable') {
      items = upsert(items, {
        id,
        kind: 'model_thinking',
        title: '模型思考不可见',
        status: 'completed',
        provider: safeString(payload.provider),
        operation: safeString(payload.operation),
        contentLevel: 'unavailable',
        unavailableReason: safeString(payload.reason),
      });
    }
  } else if (event.type === 'tool_call.started') {
    const toolCallId = safeString(payload.tool_call_id);
    if (!quickMode && turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    const groupId = quickMode
      ? undefined
      : turnIndex === undefined ? activeDecisionGroupId(items) : decisionGroupId(turnIndex);
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
    if (!quickMode && turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    const groupId = quickMode
      ? undefined
      : existing?.groupId ?? (turnIndex === undefined ? activeDecisionGroupId(items) : decisionGroupId(turnIndex));
    if (toolCallId) items = upsert(items, {
      id: `tool-${toolCallId}`,
      kind: 'tool',
      title: safeString(payload.tool_name) || items.find((item) => item.id === `tool-${toolCallId}`)?.title || '工具调用',
      detail: safeString(payload.status),
      status: payload.status === 'failed' ? 'failed' : 'completed',
      toolCallId,
      groupId,
    });
    if (!quickMode && toolCallId && groupId) {
      items = items.map((item) => (item.id === groupId || item.groupId === groupId) && item.status === 'running' ? { ...item, status: 'completed' } : item);
      items = upsert(items, {
        id: `phase-processing_result-${toolCallId}`,
        kind: 'phase',
        title: phaseTitles.processing_result,
        status: 'running',
        turnIndex: turnIndex ?? items.find((item) => item.id === groupId)?.turnIndex,
        groupId,
      });
    }
  } else if (event.type === 'reflection.created') {
    const summary = safeString(payload.summary);
    if (!quickMode && turnIndex !== undefined) items = ensureDecisionGroup(items, turnIndex);
    items = upsert(items, {
      id: `reflection-${turnIndex ?? items.length}`,
      kind: 'reflection',
      title: '反思',
      detail: summary,
      status: 'completed',
      turnIndex,
      groupId: quickMode
        ? undefined
        : turnIndex === undefined ? activeDecisionGroupId(items) : decisionGroupId(turnIndex),
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
    items = items
      .filter((item) => !isProcessingResultHandoff(item))
      .map((item) => item.status === 'running' ? { ...item, status: event.type === 'run.cancelled' || status === 'cancelled' ? 'cancelled' : 'completed' } : item);
  }
  return { ...state, items, active, seenEventIds, runCursor, agentCursors, cursorGap };
}

export function isDecisionGroup(item: ProcessStreamItem) {
  return item.kind === 'phase' && item.id.startsWith('phase-selecting_action-');
}

function isProcessingResultHandoff(item: ProcessStreamItem) {
  return item.id.startsWith('phase-processing_result-');
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
