import type { PlanGraphEdge, PlanGraphNode, PlanGraphSnapshot } from '../../src/types';

function node(
  id: string,
  index: number,
  title: string,
  depends_on: string[] = [],
  status: PlanGraphNode['status'] = 'pending',
): PlanGraphNode {
  return {
    id,
    plan_id: 'fixture-plan',
    plan_version: 1,
    node_key: id,
    index,
    title,
    intent: `${title}的固定视觉回归说明，用于验证节点卡片换行、状态和布局。`,
    status,
    depends_on,
    required_capabilities: [],
    success_criteria_refs: [`SC-${index}`],
    expected_outcome: {
      kind: 'text',
      success_condition: `完成${title}`,
      required_fields: ['content'],
    },
    risk_level: index % 4 === 0 ? 'medium' : 'low',
    optional: false,
    evidence_refs: status === 'completed' ? [`evidence-${id}`] : [],
  };
}

function edge(source: string, target: string): PlanGraphEdge {
  return {
    id: `${source}-${target}`,
    plan_id: 'fixture-plan',
    predecessor_node_id: source,
    successor_node_id: target,
    dependency_type: 'completion',
  };
}

function graph(id: string, nodes: PlanGraphNode[], edges: PlanGraphEdge[]): PlanGraphSnapshot {
  return {
    schema_version: 1,
    id,
    run_id: `run-${id}`,
    version: 1,
    status: 'active',
    nodes: nodes.map((item) => ({ ...item, plan_id: id })),
    edges: edges.map((item) => ({ ...item, plan_id: id })),
  };
}

const fanOutNodes = [
  node('scope', 1, '明确目标与约束', [], 'completed'),
  node('research', 2, '并行收集权威资料', ['scope'], 'running'),
  node('risk', 3, '并行识别风险与边界', ['scope']),
  node('examples', 4, '并行整理可验证案例', ['scope']),
  node('synthesis', 5, '汇合资料、风险与案例并形成结论', ['research', 'risk', 'examples']),
];

const largeNodes = Array.from({ length: 24 }, (_, index) => {
  const number = index + 1;
  const predecessor = number === 1 ? [] : [`large-${Math.max(1, Math.floor((number - 2) / 3) + 1)}`];
  return node(
    `large-${number}`,
    number,
    `大规模计划节点 ${number}：验证多层分支与稳定自动布局`,
    predecessor,
    number < 5 ? 'completed' : number === 5 ? 'running' : 'pending',
  );
});

export const planGraphVisualFixtures = {
  fanOutFanIn: graph(
    'fixture-fan-out-fan-in',
    fanOutNodes,
    [
      edge('scope', 'research'),
      edge('scope', 'risk'),
      edge('scope', 'examples'),
      edge('research', 'synthesis'),
      edge('risk', 'synthesis'),
      edge('examples', 'synthesis'),
    ],
  ),
  longLabels: graph(
    'fixture-long-labels',
    [
      node('long-1', 1, '确认包含多语言术语、日期、约束条件以及非常长描述的用户目标', [], 'completed'),
      node('long-2', 2, '在不隐藏依赖关系的前提下验证节点卡片省略与详情面板完整展示', ['long-1'], 'failed'),
      node('long-3', 3, '提供不会依赖颜色即可辨认的失败恢复和替代路径', ['long-2'], 'blocked'),
    ],
    [edge('long-1', 'long-2'), edge('long-2', 'long-3')],
  ),
  largePlan: graph(
    'fixture-large-plan',
    largeNodes,
    largeNodes.slice(1).map((item) => edge(item.depends_on[0], item.id)),
  ),
} satisfies Record<string, PlanGraphSnapshot>;
