import type { ChatMessage, PlanGraphSnapshot, RunView } from './types';

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
  const rawGraph = run.answer_mode === 'trusted' && run.plan_graph && 'id' in run.plan_graph
    ? run.plan_graph
    : null;
  const planGraph: PlanGraphSnapshot | Record<string, never> = rawGraph ? {
    schema_version: 'schema_version' in rawGraph && rawGraph.schema_version === 2 ? 2 : 1,
    id: rawGraph.id,
    run_id: rawGraph.run_id ?? run.id,
    version: rawGraph.version ?? 1,
    status: rawGraph.status ?? 'planned',
    supersedes_plan_id: 'supersedes_plan_id' in rawGraph ? rawGraph.supersedes_plan_id : null,
    nodes: (rawGraph.nodes ?? []).map((node) => ({
      ...node,
      plan_id: 'plan_id' in node ? String(node.plan_id) : rawGraph.id,
      plan_version: 'plan_version' in node ? Number(node.plan_version) : rawGraph.version ?? 1,
      status: ['pending', 'running', 'completed', 'failed', 'blocked', 'skipped'].includes(node.status)
        ? node.status as 'pending' | 'running' | 'completed' | 'failed' | 'blocked' | 'skipped'
        : 'pending',
      required_capabilities: 'required_capabilities' in node && Array.isArray(node.required_capabilities) ? node.required_capabilities : [],
      success_criteria_refs: 'success_criteria_refs' in node && Array.isArray(node.success_criteria_refs) ? node.success_criteria_refs : [],
      risk_level: 'risk_level' in node ? String(node.risk_level) : 'low',
      optional: 'optional' in node ? Boolean(node.optional) : false,
      evidence_refs: 'evidence_refs' in node && Array.isArray(node.evidence_refs) ? node.evidence_refs : [],
    })),
    edges: rawGraph.edges ?? [],
    active_executions: 'active_executions' in rawGraph && Array.isArray(rawGraph.active_executions)
      ? rawGraph.active_executions
      : run.node_executions?.filter((execution) => ['active', 'waiting'].includes(execution.status)) ?? [],
    parallelism: 'parallelism' in rawGraph ? rawGraph.parallelism : run.parallelism ?? null,
  } : {};
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
