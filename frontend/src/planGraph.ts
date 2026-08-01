import dagre from '@dagrejs/dagre';
import type { RunStreamEvent } from './api';
import type { NodeExecution, PlanGraphDiff, PlanGraphEdge, PlanGraphNode, PlanGraphSnapshot, PlanNodeStatus, PlanVersionSummary, RunView } from './types';

export type PlanGraphStreamState = {
  current: PlanGraphSnapshot | null;
  versions: PlanVersionSummary[];
  seenEventIds: number[];
  needsRefresh: boolean;
};

export type PositionedPlanNode = PlanGraphNode & {
  derivedStatus: PlanNodeStatus;
  position: { x: number; y: number };
};

export type PlanGraphLayout = {
  planId: string;
  topologyKey: string;
  width: number;
  height: number;
  nodes: PositionedPlanNode[];
  edges: PlanGraphEdge[];
};

const terminalDependencyStatuses = new Set<PlanNodeStatus>(['completed', 'skipped']);
const failedDependencyStatuses = new Set<PlanNodeStatus>(['failed', 'blocked']);

function graphSnapshot(run: RunView): PlanGraphSnapshot | null {
  const graph = run.answer_mode === 'trusted' ? run.plan_graph : undefined;
  return graph && 'id' in graph && typeof graph.id === 'string' ? graph as PlanGraphSnapshot : null;
}

export function createPlanGraphStreamState(run: RunView): PlanGraphStreamState {
  return {
    current: graphSnapshot(run),
    versions: run.answer_mode === 'trusted' ? run.plan_versions ?? [] : [],
    seenEventIds: [],
    needsRefresh: false,
  };
}

export function reconcilePlanGraphSnapshot(
  state: PlanGraphStreamState | null,
  run: RunView,
): PlanGraphStreamState {
  const snapshot = createPlanGraphStreamState(run);
  return {
    ...snapshot,
    seenEventIds: state?.seenEventIds ?? [],
  };
}

export function reducePlanGraphEvent(
  state: PlanGraphStreamState,
  event: RunStreamEvent,
): PlanGraphStreamState {
  if (typeof event.id === 'number' && state.seenEventIds.includes(event.id)) return state;
  const seenEventIds = typeof event.id === 'number'
    ? [...state.seenEventIds, event.id].slice(-500)
    : state.seenEventIds;
  if (event.type === 'plan.graph.snapshot') {
    const graph = event.payload.graph as PlanGraphSnapshot | undefined;
    if (!graph?.id || graph.schema_version !== 2) {
      return { ...state, seenEventIds, needsRefresh: true };
    }
    if (state.current && graph.version < state.current.version) {
      return { ...state, seenEventIds };
    }
    return { ...state, current: graph, seenEventIds, needsRefresh: false };
  }
  if (event.type === 'plan.node.updated') {
    const version = Number(event.payload.plan_version);
    if (!state.current || version !== state.current.version) {
      return {
        ...state,
        seenEventIds,
        needsRefresh: version > (state.current?.version ?? 0),
      };
    }
    const nodeId = String(event.payload.plan_node_id ?? '');
    const status = String(event.payload.status ?? '') as PlanGraphNode['status'];
    const index = state.current.nodes.findIndex((node) => node.id === nodeId);
    if (index < 0) return { ...state, seenEventIds, needsRefresh: true };
    const nodes = state.current.nodes.map((node) => node.id === nodeId ? {
      ...node,
      status,
      evidence_refs: Array.isArray(event.payload.evidence_refs)
        ? event.payload.evidence_refs.map(String)
        : node.evidence_refs,
      failure: event.payload.failure as Record<string, unknown> | null | undefined,
    } : node);
    return { ...state, current: { ...state.current, nodes }, seenEventIds };
  }
  if (event.type === 'plan.nodes.dispatched' || event.type === 'plan.nodes.claimed') {
    return {
      ...state,
      seenEventIds,
      needsRefresh: true,
    };
  }
  if (
    event.type.startsWith('plan.node.execution_')
    || event.type === 'plan.node.waiting_resource'
    || event.type === 'plan.node.waiting_approval'
    || event.type === 'plan.node.result_unknown'
  ) {
    const version = Number(event.payload.plan_version);
    if (!state.current || version !== state.current.version) {
      return {
        ...state,
        seenEventIds,
        needsRefresh: version > (state.current?.version ?? 0),
      };
    }
    const executionId = String(event.payload.node_execution_id ?? '');
    const nodeId = String(event.payload.plan_node_id ?? '');
    const attempt = Number(event.payload.attempt ?? 0);
    if (!executionId || !nodeId || attempt < 1) {
      return { ...state, seenEventIds, needsRefresh: true };
    }
    const existing = (state.current.active_executions ?? []).find(
      (item) => item.plan_node_id === nodeId,
    );
    if (existing && existing.attempt > attempt) return { ...state, seenEventIds };
    const incoming: NodeExecution = {
      execution_id: executionId,
      plan_id: String(event.payload.plan_id ?? state.current.id),
      plan_node_id: nodeId,
      plan_version: version,
      attempt,
      dispatch_batch_id: event.payload.dispatch_batch_id
        ? String(event.payload.dispatch_batch_id)
        : null,
      slot_index: typeof event.payload.slot_index === 'number'
        ? event.payload.slot_index
        : null,
      phase: String(event.payload.phase ?? 'running') as NodeExecution['phase'],
      status: String(event.payload.status ?? 'active') as NodeExecution['status'],
      state_version: Number(event.payload.state_version ?? 1),
      wait_reason: event.payload.wait_reason ? String(event.payload.wait_reason) : null,
      started_at: event.payload.started_at ? String(event.payload.started_at) : null,
      heartbeat_at: event.payload.heartbeat_at ? String(event.payload.heartbeat_at) : null,
      finished_at: event.payload.finished_at ? String(event.payload.finished_at) : null,
    };
    const isTerminal = ['completed', 'failed', 'cancelled', 'blocked'].includes(incoming.status);
    const activeExecutions = isTerminal
      ? (state.current.active_executions ?? []).filter(
        (item) => item.execution_id !== executionId,
      )
      : [
        ...(state.current.active_executions ?? []).filter(
          (item) => item.plan_node_id !== nodeId,
        ),
        incoming,
      ];
    const nodeStatus: PlanGraphNode['status'] = incoming.status === 'completed'
      ? 'completed'
      : incoming.status === 'failed'
        ? 'failed'
        : incoming.status === 'cancelled' || incoming.status === 'blocked'
          ? 'blocked'
          : 'running';
    const nodes = state.current.nodes.map((node) => node.id === nodeId
      ? { ...node, status: nodeStatus }
      : node);
    const totalSlots = state.current.parallelism?.total_slots ?? 1;
    const usedSlots = activeExecutions.filter((item) => item.slot_index != null).length;
    const parallelism = {
      requested_slots: state.current.parallelism?.requested_slots ?? totalSlots,
      total_slots: totalSlots,
      used_slots: usedSlots,
      active_count: activeExecutions.filter((item) => item.status === 'active').length,
      waiting_count: activeExecutions.filter((item) => item.status === 'waiting').length,
    };
    return {
      ...state,
      current: { ...state.current, nodes, active_executions: activeExecutions, parallelism },
      seenEventIds,
    };
  }
  if (event.type === 'plan.parallelism.changed') {
    const version = Number(event.payload.plan_version);
    if (!state.current || version !== state.current.version) {
      return {
        ...state,
        seenEventIds,
        needsRefresh: version > (state.current?.version ?? 0),
      };
    }
    return {
      ...state,
      current: {
        ...state.current,
        parallelism: {
          requested_slots: Number(
            event.payload.requested_slots
            ?? state.current.parallelism?.requested_slots
            ?? event.payload.total_slots
            ?? 1,
          ),
          total_slots: Number(event.payload.total_slots ?? 1),
          used_slots: Number(event.payload.used_slots ?? 0),
          active_count: Number(event.payload.active_count ?? 0),
          waiting_count: Number(
            event.payload.waiting_count
            ?? state.current.parallelism?.waiting_count
            ?? 0,
          ),
        },
      },
      seenEventIds,
    };
  }
  if (event.type === 'plan.version.created' || event.type === 'plan.version.activated') {
    const version = Number(event.payload.plan_version);
    return {
      ...state,
      seenEventIds,
      needsRefresh: version >= (state.current?.version ?? 0),
    };
  }
  return { ...state, seenEventIds };
}

export function derivedNodeStatuses(graph: PlanGraphSnapshot): Map<string, PlanNodeStatus> {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const predecessors = new Map<string, string[]>();
  for (const edge of graph.edges) {
    predecessors.set(edge.successor_node_id, [
      ...(predecessors.get(edge.successor_node_id) ?? []),
      edge.predecessor_node_id,
    ]);
  }
  const statuses = new Map<string, PlanNodeStatus>();
  const executions = new Map(
    (graph.active_executions ?? []).map((execution) => [
      execution.plan_node_id,
      execution,
    ]),
  );
  const visiting = new Set<string>();
  const resolve = (nodeId: string): PlanNodeStatus => {
    const cached = statuses.get(nodeId);
    if (cached) return cached;
    const node = byId.get(nodeId);
    if (!node) return 'pending';
    if (executions.has(nodeId)) {
      statuses.set(nodeId, 'running');
      return 'running';
    }
    if (node.status !== 'pending') {
      statuses.set(nodeId, node.status);
      return node.status;
    }
    if (visiting.has(nodeId)) return 'pending';
    visiting.add(nodeId);
    const dependencyStatuses = (predecessors.get(nodeId) ?? []).map(resolve);
    visiting.delete(nodeId);
    const status = dependencyStatuses.some((dependency) => failedDependencyStatuses.has(dependency))
      ? 'blocked'
      : dependencyStatuses.every((dependency) => terminalDependencyStatuses.has(dependency))
        ? 'ready'
        : 'pending';
    statuses.set(nodeId, status);
    return status;
  };
  for (const node of graph.nodes) resolve(node.id);
  return statuses;
}

export function planProgress(graph: PlanGraphSnapshot) {
  const completed = graph.nodes.filter((node) => terminalDependencyStatuses.has(node.status)).length;
  return { completed, total: graph.nodes.length, ratio: graph.nodes.length ? completed / graph.nodes.length : 0 };
}

export function unmetDependencies(graph: PlanGraphSnapshot, nodeId: string): PlanGraphNode[] {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  return graph.edges
    .filter((edge) => edge.successor_node_id === nodeId)
    .flatMap((edge) => byId.get(edge.predecessor_node_id) ?? [])
    .filter((node) => !terminalDependencyStatuses.has(node.status));
}

export function activePath(graph: PlanGraphSnapshot): Set<string> {
  const activeIds = new Set([
    ...graph.nodes.filter((node) => node.status === 'running').map((node) => node.id),
    ...(graph.active_executions ?? []).map((execution) => execution.plan_node_id),
  ]);
  if (!activeIds.size) return new Set();
  const predecessors = new Map<string, string[]>();
  for (const edge of graph.edges) {
    predecessors.set(edge.successor_node_id, [
      ...(predecessors.get(edge.successor_node_id) ?? []),
      edge.predecessor_node_id,
    ]);
  }
  const path = new Set(activeIds);
  const visit = (id: string) => {
    for (const predecessor of predecessors.get(id) ?? []) {
      if (!path.has(predecessor)) {
        path.add(predecessor);
        visit(predecessor);
      }
    }
  };
  for (const activeId of activeIds) visit(activeId);
  return path;
}

export function nodeTraceAssociations(run: RunView, nodeId: string) {
  const turns = (run.turns ?? []).filter((turn) => turn.plan_node_id === nodeId);
  const toolCalls = run.tool_calls.filter((call) => call.plan_node_id === nodeId);
  const artifacts = run.artifacts.filter((artifact) => artifact.plan_node_id === nodeId);
  const pendingApproval = toolCalls.some((call) => call.id === run.pending_approval?.tool_call_id)
    ? run.pending_approval ?? null
    : null;
  return {
    turns,
    toolCalls,
    artifacts,
    pendingApproval,
    reflections: turns.flatMap((turn) => turn.reflection ? [turn.reflection] : []),
    evaluations: turns.flatMap((turn) => turn.evaluation ? [turn.evaluation] : []),
  };
}

export function layoutPlanGraph(
  graph: PlanGraphSnapshot,
  previous?: PlanGraphLayout,
): PlanGraphLayout {
  const topologyKey = [
    ...graph.nodes.map((node) => `${node.id}:${node.index}:${node.node_key}`).sort(),
    ...graph.edges.map((edge) => `${edge.predecessor_node_id}>${edge.successor_node_id}`).sort(),
  ].join('|');
  const statuses = derivedNodeStatuses(graph);
  if (previous?.planId === graph.id && previous.topologyKey === topologyKey) {
    const positions = new Map(previous.nodes.map((node) => [node.id, node.position]));
    return {
      ...previous,
      nodes: graph.nodes.map((node) => ({
        ...node,
        derivedStatus: statuses.get(node.id) ?? node.status,
        position: positions.get(node.id) ?? { x: 0, y: 0 },
      })),
      edges: graph.edges,
    };
  }
  const layout = new dagre.graphlib.Graph();
  layout.setGraph({ rankdir: 'TB', ranksep: 58, nodesep: 34, marginx: 24, marginy: 24 });
  layout.setDefaultEdgeLabel(() => ({}));
  for (const node of [...graph.nodes].sort(nodeOrder)) {
    layout.setNode(node.id, { width: 236, height: 112 });
  }
  for (const edge of [...graph.edges].sort(edgeOrder)) {
    layout.setEdge(edge.predecessor_node_id, edge.successor_node_id);
  }
  dagre.layout(layout);
  const graphSize = layout.graph();
  return {
    planId: graph.id,
    topologyKey,
    width: Number(graphSize.width ?? 0),
    height: Number(graphSize.height ?? 0),
    nodes: [...graph.nodes].sort(nodeOrder).map((node) => {
      const point = layout.node(node.id);
      return {
        ...node,
        derivedStatus: statuses.get(node.id) ?? node.status,
        position: { x: point.x - 118, y: point.y - 56 },
      };
    }),
    edges: [...graph.edges].sort(edgeOrder),
  };
}

export function diffPlanGraphs(before: PlanGraphSnapshot, after: PlanGraphSnapshot): PlanGraphDiff {
  const beforeById = new Map(before.nodes.map((node) => [node.id, node]));
  const linkedPreviousIds = new Set(after.nodes.flatMap((node) => node.lineage_node_id ?? []));
  const nodes: PlanGraphDiff['nodes'] = after.nodes.map((node) => {
    const previous = node.lineage_node_id ? beforeById.get(node.lineage_node_id) : undefined;
    const changed = previous && [
      previous.node_key === node.node_key,
      previous.title === node.title,
      previous.intent === node.intent,
      JSON.stringify(previous.required_capabilities) === JSON.stringify(node.required_capabilities),
      JSON.stringify(previous.success_criteria_refs) === JSON.stringify(node.success_criteria_refs),
      JSON.stringify(previous.expected_outcome) === JSON.stringify(node.expected_outcome),
      previous.risk_level === node.risk_level,
      previous.optional === node.optional,
    ].some((equal) => !equal);
    return {
      node_id: node.id,
      node_key: node.node_key,
      previous_node_id: previous?.id,
      change: !previous ? 'added'
        : previous.status === 'completed' && node.status === 'completed' ? 'inherited_completed'
          : changed ? 'modified' : 'unchanged',
    };
  });
  for (const node of before.nodes) {
    if (!linkedPreviousIds.has(node.id)) {
      nodes.push({ node_id: node.id, node_key: node.node_key, previous_node_id: node.id, change: 'removed' });
    }
  }
  const beforeEdges = new Set(before.edges.map((edge) => `${edge.predecessor_node_id}>${edge.successor_node_id}`));
  const afterById = new Map(after.nodes.map((node) => [node.id, node]));
  const retainedEdges = new Set<string>();
  const edges: PlanGraphDiff['edges'] = after.edges.map((edge) => {
    const predecessor = afterById.get(edge.predecessor_node_id)?.lineage_node_id;
    const successor = afterById.get(edge.successor_node_id)?.lineage_node_id;
    const previousKey = `${predecessor}>${successor}`;
    if (predecessor && successor) retainedEdges.add(previousKey);
    return {
      predecessor_node_id: edge.predecessor_node_id,
      successor_node_id: edge.successor_node_id,
      change: beforeEdges.has(previousKey) ? 'unchanged' : 'added',
    };
  });
  for (const edge of before.edges) {
    const key = `${edge.predecessor_node_id}>${edge.successor_node_id}`;
    if (!retainedEdges.has(key)) {
      edges.push({
        predecessor_node_id: edge.predecessor_node_id,
        successor_node_id: edge.successor_node_id,
        change: 'removed',
      });
    }
  }
  return {
    from_plan_id: before.id,
    to_plan_id: after.id,
    from_version: before.version,
    to_version: after.version,
    nodes,
    edges,
  };
}

function nodeOrder(left: PlanGraphNode, right: PlanGraphNode) {
  return left.index - right.index || left.node_key.localeCompare(right.node_key) || left.id.localeCompare(right.id);
}

function edgeOrder(left: PlanGraphEdge, right: PlanGraphEdge) {
  return left.predecessor_node_id.localeCompare(right.predecessor_node_id)
    || left.successor_node_id.localeCompare(right.successor_node_id)
    || left.id.localeCompare(right.id);
}
