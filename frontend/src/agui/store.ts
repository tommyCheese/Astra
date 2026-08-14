import type {
  ActivityDeltaEvent,
  ActivitySnapshotEvent,
  AgentCapabilities,
  Interrupt,
  Message,
  MessagesSnapshotEvent,
  ReasoningEndEvent,
  ReasoningMessageContentEvent,
  ReasoningMessageEndEvent,
  ReasoningMessageStartEvent,
  ReasoningStartEvent,
  RunErrorEvent,
  RunFinishedEvent,
  RunStartedEvent,
  StateDeltaEvent,
  StateSnapshotEvent,
  TextMessageContentEvent,
  TextMessageEndEvent,
  TextMessageStartEvent,
  ToolCallArgsEvent,
  ToolCallEndEvent,
  ToolCallResultEvent,
  ToolCallStartEvent,
} from '@ag-ui/core';

export type AgUiProjectedEvent =
  | RunStartedEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | MessagesSnapshotEvent
  | StateSnapshotEvent
  | StateDeltaEvent
  | ActivitySnapshotEvent
  | ActivityDeltaEvent
  | ReasoningStartEvent
  | ReasoningMessageStartEvent
  | ReasoningMessageContentEvent
  | ReasoningMessageEndEvent
  | ReasoningEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | ToolCallResultEvent
  | RunFinishedEvent
  | RunErrorEvent;

export type AgUiConnectionState = 'idle' | 'streaming' | 'reconnecting' | 'finished' | 'cancelled' | 'error';

export interface ProjectedMessage {
  id: string;
  role: string;
  content: string;
  complete: boolean;
}

export interface ProjectedActivity {
  messageId: string;
  activityType: string;
  content: Record<string, unknown>;
  revision: number;
  schemaVersion: number;
  error?: string;
}

export interface ProjectedToolCall {
  id: string;
  name: string;
  arguments: string;
  result?: string;
  complete: boolean;
}

export interface AgUiProjectionStore {
  connection: AgUiConnectionState;
  threadId: string | null;
  runId: string | null;
  messageOrder: string[];
  messages: Record<string, ProjectedMessage>;
  reasoningOrder: string[];
  reasoning: Record<string, ProjectedMessage>;
  toolOrder: string[];
  tools: Record<string, ProjectedToolCall>;
  activityOrder: string[];
  activities: Record<string, ProjectedActivity>;
  sharedState: Record<string, unknown>;
  pendingInterrupts: Interrupt[];
  capabilities: AgentCapabilities | null;
  error: { message: string; code?: string } | null;
}

export const initialAgUiProjectionStore = (): AgUiProjectionStore => ({
  connection: 'idle',
  threadId: null,
  runId: null,
  messageOrder: [],
  messages: {},
  reasoningOrder: [],
  reasoning: {},
  toolOrder: [],
  tools: {},
  activityOrder: [],
  activities: {},
  sharedState: {},
  pendingInterrupts: [],
  capabilities: null,
  error: null,
});

function upsertMessage(
  store: AgUiProjectionStore,
  id: string,
  update: Partial<ProjectedMessage>,
): AgUiProjectionStore {
  const existing = store.messages[id] ?? { id, role: 'assistant', content: '', complete: false };
  return {
    ...store,
    messageOrder: store.messages[id] ? store.messageOrder : [...store.messageOrder, id],
    messages: { ...store.messages, [id]: { ...existing, ...update } },
  };
}

function upsertReasoning(
  store: AgUiProjectionStore,
  id: string,
  update: Partial<ProjectedMessage>,
): AgUiProjectionStore {
  const existing = store.reasoning[id] ?? { id, role: 'reasoning', content: '', complete: false };
  return {
    ...store,
    reasoningOrder: store.reasoning[id] ? store.reasoningOrder : [...store.reasoningOrder, id],
    reasoning: { ...store.reasoning, [id]: { ...existing, ...update } },
  };
}

function upsertTool(
  store: AgUiProjectionStore,
  id: string,
  update: Partial<ProjectedToolCall>,
): AgUiProjectionStore {
  const existing = store.tools[id] ?? { id, name: '', arguments: '', complete: false };
  return {
    ...store,
    toolOrder: store.tools[id] ? store.toolOrder : [...store.toolOrder, id],
    tools: { ...store.tools, [id]: { ...existing, ...update } },
  };
}

function messageContent(message: Message): string {
  return typeof message.content === 'string' ? message.content : '';
}

type PatchOperation = { op: string; path: string; value?: unknown };

function pointerSegments(path: string): string[] {
  if (!path.startsWith('/')) throw new Error('JSON Patch path must be absolute');
  return path.slice(1).split('/').map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'));
}

function applyPatch(document: Record<string, unknown>, operations: PatchOperation[]): Record<string, unknown> {
  const result = structuredClone(document);
  for (const operation of operations) {
    if (!['add', 'replace', 'remove'].includes(operation.op)) throw new Error('Unsupported JSON Patch operation');
    const segments = pointerSegments(operation.path);
    const key = segments.pop();
    let target: Record<string, unknown> = result;
    for (const segment of segments) {
      const next = target[segment];
      if (!next || typeof next !== 'object' || Array.isArray(next)) throw new Error('Unsafe JSON Patch path');
      target = next as Record<string, unknown>;
    }
    if (!key || key === '__proto__' || key === 'constructor' || key === 'prototype') throw new Error('Unsafe JSON Patch key');
    if (operation.op === 'remove') delete target[key];
    else target[key] = structuredClone(operation.value);
  }
  return result;
}

const SUPPORTED_ACTIVITIES = new Set([
  'astra.plan',
  'astra.agent_tree',
  'astra.verification',
  'astra.artifact',
  'astra.tool_activity',
]);

function activitySnapshot(store: AgUiProjectionStore, event: ActivitySnapshotEvent): AgUiProjectionStore {
  const schemaVersion = Number(event.content.schemaVersion ?? 0);
  const revision = Number(event.content.revision ?? 0);
  const error = SUPPORTED_ACTIVITIES.has(event.activityType) && schemaVersion === 1
    ? undefined
    : '不支持的 Activity 类型或 schema 版本';
  const activity = { messageId: event.messageId, activityType: event.activityType, content: event.content, revision, schemaVersion, error };
  return {
    ...store,
    activityOrder: store.activities[event.messageId] ? store.activityOrder : [...store.activityOrder, event.messageId],
    activities: { ...store.activities, [event.messageId]: activity },
  };
}

function activityDelta(store: AgUiProjectionStore, event: ActivityDeltaEvent): AgUiProjectionStore {
  const existing = store.activities[event.messageId];
  const metadata = (event as ActivityDeltaEvent & { metadata?: Record<string, unknown> }).metadata ?? {};
  const baseRevision = Number(metadata.baseRevision ?? -1);
  const revision = Number(metadata.revision ?? -1);
  if (!existing || existing.activityType !== event.activityType || existing.revision !== baseRevision) {
    return isolateActivityError(store, event.messageId, 'Activity revision 不连续，等待替换快照');
  }
  try {
    const content = applyPatch(existing.content, event.patch as PatchOperation[]);
    return {
      ...store,
      activities: { ...store.activities, [event.messageId]: { ...existing, content, revision, error: undefined } },
    };
  } catch {
    return isolateActivityError(store, event.messageId, 'Activity patch 无效，等待替换快照');
  }
}

function isolateActivityError(store: AgUiProjectionStore, messageId: string, error: string): AgUiProjectionStore {
  const existing = store.activities[messageId];
  if (!existing) return store;
  return { ...store, activities: { ...store.activities, [messageId]: { ...existing, error } } };
}

export function markAgUiDisconnected(store: AgUiProjectionStore): AgUiProjectionStore {
  return store.connection === 'streaming' ? { ...store, connection: 'reconnecting' } : store;
}

export function withAgUiCapabilities(
  store: AgUiProjectionStore,
  capabilities: AgentCapabilities,
): AgUiProjectionStore {
  return { ...store, capabilities };
}

export function reduceAgUiEvent(store: AgUiProjectionStore, event: AgUiProjectedEvent): AgUiProjectionStore {
  switch (event.type) {
    case 'RUN_STARTED': return {
      ...store,
      connection: 'streaming',
      threadId: event.threadId,
      runId: event.runId,
      pendingInterrupts: [],
      error: null,
    };
    case 'TEXT_MESSAGE_START': return upsertMessage(store, event.messageId, { role: event.role, complete: false });
    case 'TEXT_MESSAGE_CONTENT': return upsertMessage(store, event.messageId, { content: (store.messages[event.messageId]?.content ?? '') + event.delta, complete: false });
    case 'TEXT_MESSAGE_END': return upsertMessage(store, event.messageId, { complete: true });
    case 'MESSAGES_SNAPSHOT': {
      const messages = Object.fromEntries(event.messages.map((message) => [message.id, { id: message.id, role: message.role, content: messageContent(message), complete: true }]));
      return { ...store, messageOrder: event.messages.map((message) => message.id), messages };
    }
    case 'STATE_SNAPSHOT': return { ...store, sharedState: event.snapshot as Record<string, unknown> };
    case 'STATE_DELTA': return { ...store, sharedState: applyPatch(store.sharedState, event.delta as PatchOperation[]) };
    case 'ACTIVITY_SNAPSHOT': return activitySnapshot(store, event);
    case 'ACTIVITY_DELTA': return activityDelta(store, event);
    case 'REASONING_START':
    case 'REASONING_MESSAGE_START': return upsertReasoning(store, event.messageId, { complete: false });
    case 'REASONING_MESSAGE_CONTENT': return upsertReasoning(store, event.messageId, { content: (store.reasoning[event.messageId]?.content ?? '') + event.delta });
    case 'REASONING_MESSAGE_END':
    case 'REASONING_END': return upsertReasoning(store, event.messageId, { complete: true });
    case 'TOOL_CALL_START': return upsertTool(store, event.toolCallId, { name: event.toolCallName });
    case 'TOOL_CALL_ARGS': return upsertTool(store, event.toolCallId, { arguments: (store.tools[event.toolCallId]?.arguments ?? '') + event.delta });
    case 'TOOL_CALL_END': return upsertTool(store, event.toolCallId, { complete: true });
    case 'TOOL_CALL_RESULT': return upsertTool(store, event.toolCallId, { result: event.content, complete: true });
    case 'RUN_FINISHED': {
      const pendingInterrupts = event.outcome?.type === 'interrupt' ? event.outcome.interrupts : [];
      const status = (event.result as { status?: string } | undefined)?.status;
      return { ...store, connection: status === 'cancelled' ? 'cancelled' : 'finished', pendingInterrupts };
    }
    case 'RUN_ERROR': return { ...store, connection: 'error', error: { message: event.message, code: event.code } };
    default: return store;
  }
}
