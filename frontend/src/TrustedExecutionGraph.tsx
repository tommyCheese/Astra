import { useEffect, useRef, useState } from 'react';
import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { getPlanVersion, getPlanVersionDiff } from './api';
import { useI18n } from './i18n';
import { layoutPlanGraph, nodeTraceAssociations, planProgress, unmetDependencies, type PlanGraphLayout, type PositionedPlanNode } from './planGraph';
import type { AgentExecutionView, NodeExecution, PlanGraphDiff, PlanGraphNode, PlanGraphSnapshot, PlanNodeStatus, RunView } from './types';

type GraphNodeData = {
  node: PlanGraphNode;
  status: PlanNodeStatus;
  diff?: PlanGraphDiff['nodes'][number]['change'];
  execution?: NodeExecution;
  dependencyProgress?: { satisfied: number; total: number };
  ariaLabel: string;
  onSelect: (id: string) => void;
};

export type TrustedExecutionGraphProps = {
  run: RunView;
  compact?: boolean;
  title?: string;
};

const statusLabels: Record<PlanNodeStatus, string> = {
  pending: '等待依赖',
  ready: '可执行',
  running: '正在执行',
  completed: '已完成',
  failed: '失败',
  blocked: '受阻',
  skipped: '已跳过',
  superseded: '历史节点',
};

const executionPhaseLabels: Record<NodeExecution['phase'], string> = {
  claimed: '已认领',
  running: '正在执行',
  waiting_resource: '等待资源',
  waiting_approval: '等待批准',
  committing: '正在提交',
  cancelling: '正在取消',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  result_unknown: '结果待确认',
};

export default function TrustedExecutionGraph(props: TrustedExecutionGraphProps) {
  return <ReactFlowProvider><GraphWorkbench {...props} /></ReactFlowProvider>;
}

function GraphWorkbench({ run, compact = false, title = '执行图谱' }: TrustedExecutionGraphProps) {
  const { language, t } = useI18n();
  const flow = useReactFlow();
  const displayTitle = title ? t(title) : t('执行图谱');
  const rootGraph = run.plan_graph && 'id' in run.plan_graph ? run.plan_graph as PlanGraphSnapshot : null;
  const agents = flattenAgentTree(run.agent_executions ?? []);
  const rootAgent = agents.find((agent) => agent.execution_type === 'root');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(rootAgent?.id ?? null);
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) ?? rootAgent;
  const liveGraph = selectedAgent?.execution_type === 'child'
    ? selectedAgent.plan ?? null
    : rootGraph;
  const [selectedVersion, setSelectedVersion] = useState<number | null>(liveGraph?.version ?? null);
  const [historical, setHistorical] = useState<PlanGraphSnapshot | null>(null);
  const [diff, setDiff] = useState<PlanGraphDiff | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [layout, setLayout] = useState<PlanGraphLayout | undefined>(
    () => liveGraph ? layoutPlanGraph(liveGraph) : undefined,
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const historyCache = useRef(new Map<number, PlanGraphSnapshot>());
  const isCurrent = selectedVersion === null || selectedVersion === liveGraph?.version;
  const graph = isCurrent ? liveGraph : historical;

  useEffect(() => {
    if (!liveGraph) return;
    historyCache.current.set(liveGraph.version, liveGraph);
    if (selectedVersion === null || selectedVersion === liveGraph.version) {
      setSelectedVersion(liveGraph.version);
      setHistorical(null);
      setDiff(null);
    }
  }, [liveGraph?.id, liveGraph?.version, selectedVersion]);

  useEffect(() => {
    if (!graph) return;
    setLayout((previous) => layoutPlanGraph(graph, previous));
    if (!selectedNodeId || !graph.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(
        graph.nodes.find((node) => node.status === 'running')?.id
        ?? [...graph.nodes].sort((a, b) => a.index - b.index)[0]?.id
        ?? null,
      );
    }
  }, [graph, selectedNodeId]);

  useEffect(() => {
    if (!layout?.nodes.length) return;
    const frame = window.requestAnimationFrame(() => {
      void flow.fitView({ padding: 0.18, duration: 0 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [compact, flow, layout?.planId, layout?.topologyKey]);

  async function selectVersion(version: number) {
    if (!liveGraph || version === selectedVersion) return;
    setSelectedVersion(version);
    if (version === liveGraph.version) {
      setHistorical(null);
      setDiff(null);
      return;
    }
    setHistoryLoading(true);
    try {
      const cached = historyCache.current.get(version);
      const loaded = cached ?? await getPlanVersion(run.id, version);
      historyCache.current.set(version, loaded);
      setHistorical(loaded);
      setDiff(version > 1 ? await getPlanVersionDiff(run.id, version, version - 1) : null);
    } finally {
      setHistoryLoading(false);
    }
  }

  if (!graph || !layout) return null;
  const progress = planProgress(graph);
  const selected = layout.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const nodeDiff = new Map(diff?.nodes.map((item) => [item.node_id, item.change]) ?? []);
  const nodeStatus = new Map(layout.nodes.map((node) => [node.id, node.derivedStatus]));
  const edgeDiff = new Map(diff?.edges.map((item) => [
    `${item.predecessor_node_id}>${item.successor_node_id}`,
    item.change,
  ]) ?? []);
  const executionsByNode = new Map(
    (graph.active_executions ?? []).map((execution) => [
      execution.plan_node_id,
      execution,
    ]),
  );
  const activeNodeIds = new Set(executionsByNode.keys());
  const nodes: Node<GraphNodeData>[] = layout.nodes.map((node) => {
    const dependencies = graph.edges.filter((edge) => edge.successor_node_id === node.id);
    const dependencyProgress = {
      satisfied: dependencies.filter(
        (edge) => ['completed', 'skipped'].includes(
          graph.nodes.find((item) => item.id === edge.predecessor_node_id)?.status ?? '',
        ),
      ).length,
      total: dependencies.length,
    };
    const execution = executionsByNode.get(node.id);
    const ariaLabel = [
      t('节点 {index}：{title}').replace('{index}', String(node.index)).replace('{title}', node.title),
      t(execution ? executionPhaseLabels[execution.phase] : statusLabels[node.derivedStatus]),
      node.depends_on.length
        ? t('依赖 {dependencies}').replace('{dependencies}', node.depends_on.join(language === 'en' ? ', ' : '、'))
        : t('无前置依赖'),
    ].join(language === 'en' ? ', ' : '，');
    return {
      id: node.id,
      type: 'planNode',
      position: node.position,
      width: 236,
      height: 112,
      initialWidth: 236,
      initialHeight: 112,
      handles: [
        { type: 'target', position: Position.Top, x: 116, y: -4, width: 8, height: 8 },
        { type: 'source', position: Position.Bottom, x: 116, y: 108, width: 8, height: 8 },
      ],
      data: {
        node,
        status: node.derivedStatus,
        diff: nodeDiff.get(node.id),
        execution,
        dependencyProgress,
        ariaLabel,
        onSelect: setSelectedNodeId,
      },
      ariaLabel,
      focusable: false,
      selected: node.id === selectedNodeId,
      draggable: false,
    };
  });
  const edges: Edge[] = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.predecessor_node_id,
    target: edge.successor_node_id,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed, color: graphEdgeColor(nodeStatus.get(edge.successor_node_id)) },
    className: `plan-edge status-${nodeStatus.get(edge.successor_node_id) ?? 'pending'} ${activeNodeIds.has(edge.predecessor_node_id) || activeNodeIds.has(edge.successor_node_id) ? 'active-branch' : ''} diff-${edgeDiff.get(`${edge.predecessor_node_id}>${edge.successor_node_id}`) ?? 'none'}`,
    animated: false,
  }));
  const historicalMode = graph.id !== liveGraph?.id;
  const centerGraph = () => {
    const bounds = flow.getNodesBounds(nodes);
    void flow.setCenter(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height / 2,
      { zoom: flow.getZoom(), duration: 160 },
    );
  };
  return <section
    className={`trusted-graph-workbench ${compact ? 'compact' : ''}`}
    aria-label={displayTitle}
    data-plan-status={graph.status}
  >
    {agents.length > 1 && <nav className="trusted-agent-tree" aria-label={t('Agent 执行树')}>
      {(run.agent_executions ?? []).map((agent) => <AgentTreeButton
        agent={agent}
        selectedAgentId={selectedAgent?.id ?? null}
        onSelect={(next) => {
          setSelectedAgentId(next.id);
          setSelectedVersion(next.plan?.version ?? rootGraph?.version ?? null);
          setHistorical(null);
          setDiff(null);
        }}
        key={agent.id}
      />)}
    </nav>}
    <header className="trusted-graph-header">
      <div>
        <strong>{displayTitle}</strong>
        <span>
          {t('计划 v{version} · 已完成 {completed}/{total}').replace('{version}', String(graph.version)).replace('{completed}', String(progress.completed)).replace('{total}', String(progress.total))}
          {historicalMode ? ` · ${t('历史版本')}` : ` · ${t(planStatusLabel(graph.status))}`}
          {!historicalMode && graph.parallelism && ` · ${t('{count} 个活动节点').replace('{count}', String(graph.parallelism.active_count))} · ${t('并行 {used}/{total}').replace('{used}', String(graph.parallelism.used_slots)).replace('{total}', String(graph.parallelism.total_slots))}`}
        </span>
      </div>
      <div className="trusted-graph-header-actions">
        {selectedAgent?.execution_type !== 'child' && (run.plan_versions?.length ?? 0) > 1 && <label>
          <span className="sr-only">{t('计划版本')}</span>
          <select
            value={selectedVersion ?? graph.version}
            disabled={historyLoading}
            onChange={(event) => { void selectVersion(Number(event.target.value)); }}
          >
            {[...(run.plan_versions ?? [])].sort((a, b) => b.version - a.version).map((version) => (
              <option value={version.version} key={version.id}>
                v{version.version} · {t(planStatusLabel(version.status))}
              </option>
            ))}
          </select>
        </label>}
        <div className="trusted-graph-zoom-actions" role="group" aria-label={t('图谱缩放')}>
          <button type="button" aria-label={t('缩小图谱')} title={t('缩小图谱')} onClick={() => { void flow.zoomOut({ duration: 160 }); }}>−</button>
          <button type="button" aria-label={t('放大图谱')} title={t('放大图谱')} onClick={() => { void flow.zoomIn({ duration: 160 }); }}>+</button>
          <button className="trusted-graph-center-button" type="button" aria-label={t('定位中心')} title={t('定位中心（保持缩放）')} onClick={centerGraph}>
            <span aria-hidden="true">◎</span>{t('定位中心')}
          </button>
        </div>
      </div>
    </header>
    <div className="trusted-graph-progress" role="status" aria-live="polite" aria-label={t('已完成 {completed}，共 {total}；{active} 个节点活动中').replace('{completed}', String(progress.completed)).replace('{total}', String(progress.total)).replace('{active}', String(graph.parallelism?.active_count ?? 0))}>
      <span style={{ width: `${progress.ratio * 100}%` }} />
    </div>
    {historicalMode && <p className="trusted-graph-history-notice" role="status">
      {t('正在查看历史版本；当前状态请以 v{version} 为准。').replace('{version}', String(liveGraph?.version))}
    </p>}
    <div className="trusted-graph-body">
      <div className="trusted-graph-canvas" role="application" aria-label={t('计划 v{version} 执行图').replace('{version}', String(graph.version))}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={{ planNode: PlanNodeCard }}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          elementsSelectable
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.28}
          maxZoom={1.6}
          onNodeClick={(_event, node) => setSelectedNodeId(node.id)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} />
          <FocusCurrentButton graph={graph} onSelect={setSelectedNodeId} />
        </ReactFlow>
      </div>
      {selected && <NodeInspector run={run} graph={graph} node={selected} />}
    </div>
  </section>;
}

function AgentTreeButton({ agent, selectedAgentId, onSelect }: {
  agent: AgentExecutionView;
  selectedAgentId: string | null;
  onSelect: (agent: AgentExecutionView) => void;
}) {
  const { t } = useI18n();
  const hasPlan = agent.execution_type === 'root' || Boolean(agent.plan);
  return <div className="trusted-agent-branch">
    <button
      type="button"
      className={selectedAgentId === agent.id ? 'selected' : ''}
      aria-current={selectedAgentId === agent.id ? 'true' : undefined}
      disabled={!hasPlan}
      title={!hasPlan ? t('此子系统尚未生成计划') : undefined}
      onClick={() => onSelect(agent)}
    >
      <strong>{agent.execution_type === 'root' ? t('主系统') : agent.objective || agent.request_id}</strong>
      <small>{agent.status}</small>
    </button>
    {agent.children.length > 0 && <div>{agent.children.map((child) => <AgentTreeButton agent={child} selectedAgentId={selectedAgentId} onSelect={onSelect} key={child.id} />)}</div>}
  </div>;
}

function flattenAgentTree(roots: AgentExecutionView[]): AgentExecutionView[] {
  return roots.flatMap((agent) => [agent, ...flattenAgentTree(agent.children)]);
}

function PlanNodeCard({ data, selected }: NodeProps<Node<GraphNodeData>>) {
  const { t } = useI18n();
  const { node, status, diff, execution, dependencyProgress, ariaLabel, onSelect } = data;
  return <article
    className={`trusted-plan-node status-${status} ${selected ? 'selected' : ''} ${diff ? `diff-${diff}` : ''}`}
    data-node-status={status}
    aria-label={ariaLabel}
    aria-pressed={selected}
    aria-current={status === 'running' ? 'step' : undefined}
    role="button"
    tabIndex={0}
    onClick={() => onSelect(node.id)}
    onKeyDown={(event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      onSelect(node.id);
    }}
  >
    <Handle type="target" position={Position.Top} isConnectable={false} />
    <div className="trusted-plan-node-heading">
      <span>{node.index}</span>
      <em aria-hidden="true">{statusIcon(status)}</em>
      <small className="trusted-plan-node-status">{t(execution ? executionPhaseLabels[execution.phase] : statusLabels[status])}</small>
    </div>
    <strong>{node.title}</strong>
    <p>{node.intent}</p>
    {execution?.wait_reason && <span className="trusted-plan-node-wait">{t(safeWaitReason(execution.wait_reason))}</span>}
    {dependencyProgress && dependencyProgress.total > 1 && status !== 'completed' && <span className="trusted-plan-node-join">
      {t('汇合 {satisfied}/{total}').replace('{satisfied}', String(dependencyProgress.satisfied)).replace('{total}', String(dependencyProgress.total))}
    </span>}
    {diff && !['unchanged'].includes(diff) && <mark>{t(diffLabel(diff))}</mark>}
    <Handle type="source" position={Position.Bottom} isConnectable={false} />
  </article>;
}

function FocusCurrentButton({ graph, onSelect }: { graph: PlanGraphSnapshot; onSelect: (id: string) => void }) {
  const { t } = useI18n();
  const flow = useReactFlow();
  const indexRef = useRef(0);
  const activeIds = [
    ...[...(graph.active_executions ?? [])]
      .sort((left, right) => (left.slot_index ?? 10_000) - (right.slot_index ?? 10_000)
        || left.plan_node_id.localeCompare(right.plan_node_id))
      .map((execution) => execution.plan_node_id),
    ...graph.nodes.filter((node) => node.status === 'running').map((node) => node.id),
  ].filter((id, index, values) => values.indexOf(id) === index);
  const fallback = graph.nodes.find((node) => node.status === 'pending')?.id;
  const currentId = activeIds[0] ?? fallback;
  const current = graph.nodes.find((node) => node.id === currentId);
  if (!current) return null;
  return <button className="trusted-graph-focus" type="button" onClick={() => {
    const targetId = activeIds.length
      ? activeIds[indexRef.current++ % activeIds.length]
      : current.id;
    onSelect(targetId);
    void flow.fitView({ nodes: [{ id: targetId }], duration: 250, padding: 1.5 });
  }}>{t('定位活动节点')}{activeIds.length > 1 ? ` (${activeIds.length})` : ''}</button>;
}

function NodeInspector({ run, graph, node }: { run: RunView; graph: PlanGraphSnapshot; node: PositionedPlanNode }) {
  const { language, t } = useI18n();
  const unmet = unmetDependencies(graph, node.id);
  const {
    turns,
    toolCalls: calls,
    artifacts,
    pendingApproval: approval,
  } = nodeTraceAssociations(run, node.id);
  const executions = (run.node_executions ?? [])
    .filter((execution) => execution.plan_node_id === node.id)
    .sort((left, right) => right.attempt - left.attempt);
  return <aside className="trusted-node-inspector" aria-label={t('{title} 节点详情').replace('{title}', node.title)}>
    <header><span>{t('节点 {index}').replace('{index}', String(node.index))}</span><strong>{node.title}</strong><small>{t(statusLabels[node.derivedStatus])}</small></header>
    <section>
      <h4>{t('计划')}</h4>
      <p>{node.intent}</p>
      <dl>
        <div><dt>{t('依赖')}</dt><dd>{node.depends_on.join(language === 'en' ? ', ' : '、') || t('无')}</dd></div>
        <div><dt>{t('预期结果')}</dt><dd>{node.expected_outcome?.success_condition ?? t('未指定')}</dd></div>
        <div><dt>{t('成功准则')}</dt><dd>{node.success_criteria_refs.join(language === 'en' ? ', ' : '、') || t('无')}</dd></div>
        <div><dt>{t('所需能力')}</dt><dd>{node.required_capabilities.join(language === 'en' ? ', ' : '、') || t('无')}</dd></div>
        <div><dt>{t('风险')}</dt><dd>{t(riskLabel(node.risk_level))}{node.optional ? ` · ${t('可选')}` : ''}</dd></div>
      </dl>
      {unmet.length > 0 && <p className="trusted-node-blocking">{t('尚未满足：{items}').replace('{items}', unmet.map((item) => item.title).join(language === 'en' ? ', ' : '、'))}</p>}
    </section>
    <section>
      <h4>{t('运行轨迹')}</h4>
      {!turns.length && !calls.length && !approval && <p className="muted">{t('此节点尚未执行。')}</p>}
      {turns.map((turn) => <div className="trusted-trace-item" key={turn.id}>
        <strong>{t('第 {index} 轮').replace('{index}', String(turn.turn_index))} · {t(decisionLabel(turn.decision_type))}</strong>
        <span>{turn.reasoning_summary}</span>
        {turn.reflection && <small>{t('反思：')}{String(turn.reflection.summary ?? t('已调整策略'))}</small>}
        {turn.evaluation && <small>{t('验证：')}{String(turn.evaluation.summary ?? turn.evaluation.outcome ?? t('已记录'))}</small>}
      </div>)}
      {calls.map((call) => <div className="trusted-trace-item" key={call.id}>
        <strong>{call.tool_name}</strong><span>{t(toolStatusLabel(call.status))}</span>
      </div>)}
      {approval && <div className="trusted-trace-item approval"><strong>{t('等待工具影响批准')}</strong><span>{approval.action_summary ?? approval.preview}</span></div>}
      {executions.map((execution) => <div className="trusted-trace-item execution" key={execution.execution_id}>
        <strong>{t('第 {attempt} 次尝试').replace('{attempt}', String(execution.attempt))} · {t(executionPhaseLabels[execution.phase])}</strong>
        <span>{execution.slot_index != null ? t('并行位置 {slot}').replace('{slot}', String(execution.slot_index + 1)) : t('已结束')}</span>
        {execution.started_at && <small>{formatExecutionTiming(execution, language, t)}</small>}
      </div>)}
    </section>
    <section>
      <h4>{t('证据与产物')}</h4>
      {!node.evidence_refs.length && !artifacts.length && !node.failure && <p className="muted">{t('暂无已验证证据。')}</p>}
      {node.evidence_refs.map((reference) => <code key={reference}>{reference}</code>)}
      {artifacts.map((artifact) => artifact.content_url
        ? <a href={artifact.content_url} target="_blank" rel="noreferrer" key={artifact.id}>{String(artifact.metadata.filename ?? artifact.type)}</a>
        : <span key={artifact.id}>{String(artifact.metadata.filename ?? artifact.type)}</span>)}
      {node.failure && <p className="trusted-node-failure">{String(node.failure.message ?? node.failure.category ?? t('节点执行失败'))}</p>}
    </section>
  </aside>;
}

function planStatusLabel(status: PlanGraphSnapshot['status']) {
  return status === 'planned' ? '等待确认'
    : status === 'active' ? '执行中'
      : status === 'completed' ? '已完成'
        : '已被替代';
}

function statusIcon(status: PlanNodeStatus) {
  return status === 'completed' ? '✓'
    : status === 'running' ? '▶'
      : status === 'ready' ? '→'
        : status === 'failed' ? '!'
          : status === 'blocked' ? '×'
            : status === 'skipped' ? '–'
              : '·';
}

function graphEdgeColor(status: PlanNodeStatus | undefined) {
  return status === 'completed' ? '#2f8f73'
    : status === 'running' ? '#16866a'
      : status === 'ready' ? '#4f8fc8'
        : status === 'failed' || status === 'blocked' ? '#bd5360'
          : '#8797a6';
}

function diffLabel(change: PlanGraphDiff['nodes'][number]['change']) {
  return change === 'added' ? '新增'
    : change === 'removed' ? '移除'
      : change === 'modified' ? '已修改'
        : change === 'inherited_completed' ? '继承成果'
          : '未变化';
}

function safeWaitReason(reason: string) {
  return reason === 'resource_conflict' ? '等待资源释放'
    : reason === 'approval_required' ? '等待用户批准'
      : reason === 'provider_limit' ? '等待服务槽位'
        : reason === 'budget_exhausted' ? '预算不足'
          : '暂时等待';
}

function riskLabel(risk: string) {
  return risk === 'low' ? '低风险' : risk === 'medium' ? '中风险' : risk === 'high' ? '高风险' : risk;
}

function decisionLabel(decision: string) {
  return decision === 'act' ? '执行' : decision === 'reflect' ? '反思' : decision === 'answer' ? '回答' : decision;
}

function toolStatusLabel(status: string) {
  return status === 'pending' ? '等待执行'
    : status === 'running' ? '正在执行'
      : status === 'completed' || status === 'succeeded' ? '已完成'
        : status === 'failed' ? '失败'
          : status === 'cancelled' ? '已取消'
            : status;
}

function formatExecutionTiming(execution: NodeExecution, language: string, t: (key: string) => string) {
  const started = execution.started_at ? new Date(execution.started_at) : null;
  const finished = execution.finished_at ? new Date(execution.finished_at) : null;
  if (!started || Number.isNaN(started.getTime())) return t('时间未记录');
  if (!finished || Number.isNaN(finished.getTime())) {
    return t('开始于 {time}').replace('{time}', started.toLocaleTimeString(language));
  }
  return t('{time} · {duration} 毫秒').replace('{time}', started.toLocaleTimeString(language)).replace('{duration}', String(Math.max(0, finished.getTime() - started.getTime())));
}
