import type { ChatMessage, CommandMessageView, PlanGraphSnapshot, RunView } from './types';

export const HISTORY_LIMIT = 100;

export type ConversationEntry = {
  id: string;
  run?: RunView;
  priorMessages: ChatMessage[];
  title?: string;
  last_run_status?: string | null;
  preferred_answer_mode?: 'standard' | 'trusted';
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
    claims: Array.isArray(run.result.claims) ? run.result.claims : [],
    citations: Array.isArray(run.result.citations) ? run.result.citations : [],
    sources: run.result.sources ?? [],
    caveats: run.result.caveats ?? [],
    verification_notes: run.result.verification_notes ?? [],
    audit_refs: {
      evidence_record_count: run.result.audit_refs?.evidence_record_count ?? 0,
      agent_turn_count: run.result.audit_refs?.agent_turn_count ?? 0,
      referenced_artifact_ids: run.result.audit_refs?.referenced_artifact_ids ?? [],
      evidence_pack_artifact_id: run.result.audit_refs?.evidence_pack_artifact_id,
      evidence_ledger_artifact_id: run.result.audit_refs?.evidence_ledger_artifact_id,
    },
  } : run.result;
  const rawGraph = run.answer_mode === 'trusted' && run.plan_graph && 'id' in run.plan_graph && run.plan_graph.schema_version === 2
    ? run.plan_graph
    : null;
  const planGraph: PlanGraphSnapshot | Record<string, never> = rawGraph ?? {};
  return {
    ...run,
    answer_mode: run.answer_mode ?? 'trusted',
    execution_profile: run.execution_profile,
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
    plan_graph: planGraph,
    plan_versions: run.answer_mode === 'trusted' && Array.isArray(run.plan_versions)
      ? run.plan_versions
      : [],
    node_executions: Array.isArray(run.node_executions) ? run.node_executions : [],
  };
}

function buildConversation(run: RunView | null): ChatMessage[] {
  if (!run) return [];
  return run.chat_messages ?? [];
}

export function buildPresentation(run: RunView | null): ChatMessage[] {
  if (!run) return [];
  const snapshot = normalizeRunView(run);
  const conversation = buildConversation(snapshot);
  const hasCurrentWaitingMessage = snapshot.status === 'waiting_user'
    && conversation.some((message) => message.role === 'assistant' && message.status === 'waiting_user');
  const presented: ChatMessage[] = conversation
    .filter((message) => message.role === 'user'
      || (message.role === 'assistant'
        && !hasCurrentWaitingMessage
        && message.status === 'ask_user'))
    .map((message) => ({
      ...message,
      metadata: message.role === 'user'
        ? { ...message.metadata, presentation: 'user' }
        : { ...message.metadata },
    }));
  const waitingMessages = hasCurrentWaitingMessage
    ? conversation
      .filter((message) => message.role === 'assistant' && message.status === 'waiting_user')
      .map((message) => ({ ...message, metadata: { ...message.metadata } }))
    : [];
  const hasProcessEvents = snapshot.events.some((event) => event.type.startsWith('fast.') || event.type.startsWith('reasoning.') || ['agent_turn.created', 'tool_call.started', 'tool_call.completed', 'reflection.created', 'verification.created'].includes(event.type));
  const isActive = !['completed', 'completed_with_warnings', 'failed', 'blocked', 'waiting_user', 'cancelled'].includes(snapshot.status);
  if (isActive || hasProcessEvents || (snapshot.turns?.length ?? 0) > 0 || snapshot.tool_calls.length > 0) {
    presented.push({
      id: `${run.id}-process`,
      role: 'process',
      content: '',
      status: run.status,
      metadata: { presentation: 'process', run_snapshot: snapshot },
    });
  }
  presented.push(...waitingMessages);
  if (snapshot.result) {
    presented.push({
      id: `${run.id}-answer`,
      role: 'assistant',
      content: snapshot.result.summary,
      status: run.status,
      metadata: { presentation: 'answer', run_snapshot: snapshot },
    });
  }
  const waitingRequest = snapshot.status === 'waiting_user'
    && !snapshot.result
    && !snapshot.pending_approval
    && snapshot.waiting_state?.kind !== 'plan_confirmation'
    && !presented.some((message) => message.role === 'assistant' && message.status === 'waiting_user')
    && typeof snapshot.waiting_state?.request === 'string'
    ? snapshot.waiting_state.request.trim()
    : '';
  if (waitingRequest) {
    presented.push({
      id: `${run.id}-waiting`,
      role: 'assistant',
      content: waitingRequest,
      status: 'waiting_user',
      metadata: { waiting_state: snapshot.waiting_state },
    });
  }
  return presented;
}

export function presentCommandMessage(message: CommandMessageView): ChatMessage {
  return {
    id: message.id,
    role: 'user',
    content: message.content,
    status: 'completed',
    metadata: { presentation: 'user', command: message.command, command_arguments: message.arguments },
  };
}

export function presentCommandMessages(message: CommandMessageView): ChatMessage[] {
  const presented = [presentCommandMessage(message)];
  if (message.assistant_content) {
    presented.push({
      id: `${message.id}-result`,
      role: 'assistant',
      content: message.assistant_content,
      status: 'completed',
      metadata: { presentation: 'command-result', command: message.command },
    });
  }
  return presented;
}
