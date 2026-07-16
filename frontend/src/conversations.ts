import type { ChatMessage, RunView } from './types';

export const HISTORY_LIMIT = 100;

export type ConversationEntry = {
  id: string;
  run?: RunView;
  priorMessages: ChatMessage[];
  title?: string;
  pinned_at?: string | null;
  updated_at?: string;
  has_active_share?: boolean;
};

export function normalizeRunView(run: RunView): RunView {
  const result = run.result ? {
    ...run.result,
    findings: (Array.isArray(run.result.findings) ? run.result.findings : []).map((finding) => ({
      ...finding,
      source_urls: Array.isArray(finding?.source_urls) ? finding.source_urls : [],
      artifact_ids: Array.isArray(finding?.artifact_ids)
        ? finding.artifact_ids.filter((artifactId): artifactId is string => typeof artifactId === 'string')
        : [],
    })),
    sources: run.result.sources ?? [],
    caveats: run.result.caveats ?? [],
    verification_notes: run.result.verification_notes ?? [],
  } : run.result;
  return {
    ...run,
    answer_mode: run.answer_mode ?? 'trusted',
    execution_profile: run.execution_profile ?? {},
    result,
    steps: Array.isArray(run.steps) ? run.steps : [],
    tool_calls: Array.isArray(run.tool_calls) ? run.tool_calls : [],
    artifacts: Array.isArray(run.artifacts) ? run.artifacts : [],
    events: Array.isArray(run.events) ? run.events : [],
    turns: Array.isArray(run.turns) ? run.turns : [],
    memories: Array.isArray(run.memories) ? run.memories : [],
    chat_messages: Array.isArray(run.chat_messages)
      ? run.chat_messages.map((message) => ({ ...message, metadata: message.metadata ?? {} }))
      : [],
  };
}

export function buildConversation(run: RunView | null): ChatMessage[] {
  if (!run) return [];
  if (run.chat_messages?.length) return run.chat_messages;

  const messages: ChatMessage[] = [{
    id: `${run.id}-user`,
    role: 'user',
    content: run.summary || '提交了一个任务',
    status: 'completed',
    metadata: {},
  }];
  for (const call of run.tool_calls) {
    messages.push({
      id: call.id,
      role: 'tool',
      content: call.tool_name,
      status: call.status,
      metadata: { selected_tool: call.tool_name, output: call.output },
    });
  }
  if (run.result) {
    messages.push({
      id: `${run.id}-answer`,
      role: 'assistant',
      content: run.result.summary,
      status: run.status,
      metadata: {},
    });
  }
  return messages;
}

export function buildPresentation(run: RunView | null): ChatMessage[] {
  if (!run) return [];
  const snapshot = normalizeRunView(run);
  const presented: ChatMessage[] = buildConversation(snapshot)
    .filter((message) => message.role === 'user')
    .map((message) => ({
      ...message,
      metadata: { ...message.metadata, presentation: 'user' },
    }));
  const hasProcessEvents = snapshot.events.some((event) => event.type.startsWith('reasoning.') || ['agent_turn.created', 'tool_call.started', 'tool_call.completed', 'reflection.created', 'verification.created'].includes(event.type));
  const isActive = !['completed', 'completed_with_warnings', 'failed', 'blocked', 'waiting_user'].includes(snapshot.status);
  if (isActive || hasProcessEvents || (snapshot.turns?.length ?? 0) > 0 || snapshot.tool_calls.length > 0) {
    presented.push({
      id: `${run.id}-process`,
      role: 'process',
      content: '',
      status: run.status,
      metadata: { presentation: 'process', run_snapshot: snapshot },
    });
  }
  if (snapshot.result) {
    presented.push({
      id: `${run.id}-answer`,
      role: 'assistant',
      content: snapshot.result.summary,
      status: run.status,
      metadata: { presentation: 'answer', run_snapshot: snapshot },
    });
  }
  return presented;
}
