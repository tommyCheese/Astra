import { describe, expect, it } from 'vitest';
import {
  activePath,
  createPlanGraphStreamState,
  derivedNodeStatuses,
  diffPlanGraphs,
  layoutPlanGraph,
  nodeTraceAssociations,
  reducePlanGraphEvent,
  unmetDependencies,
} from '../src/planGraph';
import type { PlanGraphNode, PlanGraphSnapshot, RunView } from '../src/types';
import { planGraphVisualFixtures } from './fixtures/planGraphs';

function node(id: string, index: number, depends_on: string[] = []): PlanGraphNode {
  return {
    id,
    plan_id: 'plan-1',
    plan_version: 1,
    node_key: id,
    index,
    title: id.toUpperCase(),
    intent: `execute ${id}`,
    status: 'pending',
    depends_on,
    required_capabilities: [],
    success_criteria_refs: [],
    expected_outcome: { kind: 'result', success_condition: `${id} complete` },
    risk_level: 'low',
    optional: false,
    evidence_refs: [],
  };
}

function graph(): PlanGraphSnapshot {
  return {
    schema_version: 1,
    id: 'plan-1',
    run_id: 'run-1',
    version: 1,
    status: 'active',
    nodes: [node('a', 1), node('b', 2, ['a']), node('c', 3, ['a']), node('d', 4, ['b', 'c'])],
    edges: [
      { id: 'a-b', plan_id: 'plan-1', predecessor_node_id: 'a', successor_node_id: 'b', dependency_type: 'hard' },
      { id: 'a-c', plan_id: 'plan-1', predecessor_node_id: 'a', successor_node_id: 'c', dependency_type: 'hard' },
      { id: 'b-d', plan_id: 'plan-1', predecessor_node_id: 'b', successor_node_id: 'd', dependency_type: 'hard' },
      { id: 'c-d', plan_id: 'plan-1', predecessor_node_id: 'c', successor_node_id: 'd', dependency_type: 'hard' },
    ],
  };
}

function run(snapshot: PlanGraphSnapshot): RunView {
  return {
    id: 'run-1',
    task_id: 'task-1',
    status: 'executing',
    mode: 'web_agent',
    answer_mode: 'trusted',
    result: null,
    steps: [],
    tool_calls: [],
    artifacts: [],
    events: [],
    plan_graph: snapshot,
    plan_versions: [],
  };
}

describe('plan graph stream state', () => {
  it('deduplicates events and rejects stale plan versions', () => {
    const initial = createPlanGraphStreamState(run(graph()));
    const updated = reducePlanGraphEvent(initial, {
      id: 10,
      type: 'plan.node.updated',
      payload: { plan_version: 1, plan_node_id: 'a', status: 'completed', evidence_refs: ['e-1'] },
    });
    expect(updated.current?.nodes[0].status).toBe('completed');
    expect(reducePlanGraphEvent(updated, {
      id: 10,
      type: 'plan.node.updated',
      payload: { plan_version: 1, plan_node_id: 'a', status: 'failed' },
    })).toBe(updated);
    const stale = reducePlanGraphEvent(updated, {
      id: 11,
      type: 'plan.node.updated',
      payload: { plan_version: 0, plan_node_id: 'a', status: 'failed' },
    });
    expect(stale.current?.nodes[0].status).toBe('completed');
    expect(stale.needsRefresh).toBe(false);
    const gap = reducePlanGraphEvent(stale, {
      id: 12,
      type: 'plan.node.updated',
      payload: { plan_version: 2, plan_node_id: 'new', status: 'running' },
    });
    expect(gap.needsRefresh).toBe(true);
  });

  it('replaces the snapshot without allowing an older snapshot to win', () => {
    const initial = createPlanGraphStreamState(run(graph()));
    const version2 = { ...graph(), id: 'plan-2', version: 2 as const };
    const replaced = reducePlanGraphEvent(initial, {
      id: 2,
      type: 'plan.graph.snapshot',
      payload: { graph: version2 },
    });
    const stale = reducePlanGraphEvent(replaced, {
      id: 3,
      type: 'plan.graph.snapshot',
      payload: { graph: graph() },
    });
    expect(stale.current?.id).toBe('plan-2');
  });
});

describe('plan graph selectors and layout', () => {
  it('derives ready, blocked, unmet dependencies and the active path', () => {
    const snapshot = graph();
    expect(derivedNodeStatuses(snapshot).get('a')).toBe('ready');
    snapshot.nodes[0].status = 'completed';
    snapshot.nodes[1].status = 'running';
    expect(derivedNodeStatuses(snapshot).get('c')).toBe('ready');
    expect([...activePath(snapshot)]).toEqual(expect.arrayContaining(['a', 'b']));
    expect(unmetDependencies(snapshot, 'd').map((item) => item.id)).toEqual(['b', 'c']);
    snapshot.nodes[1].status = 'failed';
    expect(derivedNodeStatuses(snapshot).get('d')).toBe('blocked');
  });

  it('keeps coordinates stable when only statuses change', () => {
    const snapshot = graph();
    const first = layoutPlanGraph(snapshot);
    snapshot.nodes[0].status = 'completed';
    const second = layoutPlanGraph(snapshot, first);
    expect(second.nodes.map((item) => item.position)).toEqual(first.nodes.map((item) => item.position));
    expect(new Set(first.nodes.map((item) => item.position.x)).size).toBeGreaterThan(1);
  });

  it('keeps dense visual regression fixtures finite and deterministic', () => {
    for (const fixture of Object.values(planGraphVisualFixtures)) {
      const first = layoutPlanGraph(fixture);
      const second = layoutPlanGraph(fixture);
      expect(first.nodes).toHaveLength(fixture.nodes.length);
      expect(first.edges).toHaveLength(fixture.edges.length);
      expect(first.width).toBeGreaterThan(0);
      expect(first.height).toBeGreaterThan(0);
      expect(first.nodes.every((item) => Number.isFinite(item.position.x) && Number.isFinite(item.position.y))).toBe(true);
      expect(second.nodes.map((item) => item.position)).toEqual(first.nodes.map((item) => item.position));
    }
  });

  it('associates runtime trace and evidence by stable node id', () => {
    const snapshot = graph();
    const source = run(snapshot);
    source.turns = [{
      id: 'turn-1', run_id: source.id, plan_node_id: 'b', turn_index: 1,
      decision_type: 'tool', reasoning_summary: '查找', decision: {}, memory_reads: [],
      memory_writes: [], status: 'completed', created_at: '', updated_at: '',
    }];
    source.tool_calls = [{ id: 'call-1', plan_node_id: 'b', tool_name: 'search', status: 'succeeded', input: {} }];
    source.artifacts = [{ id: 'artifact-1', plan_node_id: 'b', type: 'file', metadata: {}, created_at: '' }];
    const associations = nodeTraceAssociations(source, 'b');
    expect(associations.turns.map((item) => item.id)).toEqual(['turn-1']);
    expect(associations.toolCalls.map((item) => item.id)).toEqual(['call-1']);
    expect(associations.artifacts.map((item) => item.id)).toEqual(['artifact-1']);
  });
});

describe('plan graph lineage diff', () => {
  it('does not match nodes by title when lineage is absent', () => {
    const before = graph();
    const after = {
      ...graph(),
      id: 'plan-2',
      version: 2,
      nodes: graph().nodes.map((item) => ({
        ...item,
        id: `new-${item.id}`,
        plan_id: 'plan-2',
        plan_version: 2,
      })),
      edges: [],
    } satisfies PlanGraphSnapshot;
    const diff = diffPlanGraphs(before, after);
    expect(diff.nodes.filter((item) => item.change === 'added')).toHaveLength(4);
    expect(diff.nodes.filter((item) => item.change === 'removed')).toHaveLength(4);
  });
});
