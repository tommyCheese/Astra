import type { PlanGraphEdge, PlanGraphNode, PlanGraphSnapshot, RunView } from '../types';

const PLAN_ID = 'verification-complex-dag-v1';

function node(
  id: string,
  index: number,
  title: string,
  depends_on: string[] = [],
  status: PlanGraphNode['status'] = 'pending',
): PlanGraphNode {
  return {
    id,
    plan_id: PLAN_ID,
    plan_version: 1,
    node_key: id,
    index,
    title,
    intent: `${title}；用于验证复杂多路 DAG 的布局、状态传播和节点检查器。`,
    status,
    depends_on,
    required_capabilities: index % 3 === 0 ? ['web_search'] : [],
    success_criteria_refs: [`SC-${String(index).padStart(2, '0')}`],
    expected_outcome: {
      kind: 'structured_result',
      success_condition: `${title}已完成并产生可验证结果`,
      required_fields: ['summary', 'evidence'],
    },
    risk_level: index % 5 === 0 ? 'medium' : 'low',
    optional: false,
    evidence_refs: status === 'completed' ? [`evidence-${id}`] : [],
    failure: status === 'failed'
      ? { category: 'verification.source_unavailable', code: 'SOURCE_TIMEOUT', retryable: true }
      : null,
  };
}

function edge(source: string, target: string): PlanGraphEdge {
  return {
    id: `${source}--${target}`,
    plan_id: PLAN_ID,
    predecessor_node_id: source,
    successor_node_id: target,
    dependency_type: 'completion',
  };
}

const nodes = [
  node('scope', 1, '明确任务边界与验收标准', [], 'completed'),
  node('research', 2, '资料研究分支', ['scope'], 'completed'),
  node('data', 3, '数据分析分支', ['scope'], 'completed'),
  node('stakeholders', 4, '利益相关方分支', ['scope'], 'completed'),
  node('primary_sources', 5, '检索一手权威来源', ['research'], 'running'),
  node('comparative_sources', 6, '收集横向比较资料', ['research']),
  node('quality_check', 7, '清洗并校验数据质量', ['data']),
  node('interviews', 8, '汇总访谈与用户反馈', ['stakeholders']),
  node('policy_constraints', 9, '识别政策和执行约束', ['stakeholders'], 'failed'),
  node('evidence_merge', 10, '汇合来源与数据证据', ['primary_sources', 'quality_check']),
  node('context_merge', 11, '汇合比较、访谈与政策约束', ['comparative_sources', 'interviews', 'policy_constraints']),
  node('risk_review', 12, '跨分支风险复核', ['quality_check', 'policy_constraints']),
  node('draft', 13, '合并证据、背景与风险形成方案', ['evidence_merge', 'context_merge', 'risk_review']),
  node('fact_check', 14, '事实与引用验证', ['draft']),
  node('feasibility_check', 15, '可执行性与资源验证', ['draft']),
  node('finalize', 16, '双重验证汇合并形成最终交付', ['fact_check', 'feasibility_check']),
];

const edges = [
  edge('scope', 'research'),
  edge('scope', 'data'),
  edge('scope', 'stakeholders'),
  edge('research', 'primary_sources'),
  edge('research', 'comparative_sources'),
  edge('data', 'quality_check'),
  edge('stakeholders', 'interviews'),
  edge('stakeholders', 'policy_constraints'),
  edge('primary_sources', 'evidence_merge'),
  edge('quality_check', 'evidence_merge'),
  edge('comparative_sources', 'context_merge'),
  edge('interviews', 'context_merge'),
  edge('policy_constraints', 'context_merge'),
  edge('quality_check', 'risk_review'),
  edge('policy_constraints', 'risk_review'),
  edge('evidence_merge', 'draft'),
  edge('context_merge', 'draft'),
  edge('risk_review', 'draft'),
  edge('draft', 'fact_check'),
  edge('draft', 'feasibility_check'),
  edge('fact_check', 'finalize'),
  edge('feasibility_check', 'finalize'),
];

export const complexDagFixture: PlanGraphSnapshot = {
  schema_version: 1,
  id: PLAN_ID,
  run_id: 'verification-complex-dag-run',
  version: 1,
  status: 'active',
  nodes,
  edges,
  created_at: '2026-07-26T00:00:00Z',
  activated_at: '2026-07-26T00:01:00Z',
};

export const complexDagRunFixture: RunView = {
  id: complexDagFixture.run_id,
  task_id: 'verification-complex-dag-task',
  status: 'executing',
  mode: 'web_agent',
  answer_mode: 'trusted',
  result: null,
  steps: [],
  tool_calls: [{
    id: 'call-primary-sources',
    plan_node_id: 'primary_sources',
    tool_name: 'web_search',
    status: 'running',
    input: {},
  }],
  artifacts: [{
    id: 'artifact-scope',
    plan_node_id: 'scope',
    type: 'evidence_pack',
    metadata: { filename: 'scope-evidence.json' },
    created_at: '2026-07-26T00:02:00Z',
  }],
  events: [],
  turns: [{
    id: 'turn-primary-sources',
    run_id: complexDagFixture.run_id,
    plan_node_id: 'primary_sources',
    turn_index: 1,
    decision_type: 'call_tool',
    reasoning_summary: '选择权威来源检索并保留可公开的审计摘要。',
    decision: {},
    memory_reads: [],
    memory_writes: [],
    status: 'running',
    created_at: '2026-07-26T00:03:00Z',
    updated_at: '2026-07-26T00:03:00Z',
  }],
  plan_graph: complexDagFixture,
  plan_versions: [{
    id: PLAN_ID,
    run_id: complexDagFixture.run_id,
    version: 1,
    status: 'active',
    node_count: nodes.length,
    created_at: '2026-07-26T00:00:00Z',
    activated_at: '2026-07-26T00:01:00Z',
  }],
};
