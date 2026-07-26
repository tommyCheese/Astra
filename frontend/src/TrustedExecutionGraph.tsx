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
import { layoutPlanGraph, nodeTraceAssociations, planProgress, unmetDependencies, type PlanGraphLayout, type PositionedPlanNode } from './planGraph';
import type { NodeExecution, PlanGraphDiff, PlanGraphNode, PlanGraphSnapshot, PlanNodeStatus, RunView } from './types';

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
  const flow = useReactFlow();
  const liveGraph = run.plan_graph && 'id' in run.plan_graph ? run.plan_graph as PlanGraphSnapshot : null;
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
      `节点 ${node.index}：${node.title}`,
      execution ? executionPhaseLabels[execution.phase] : statusLabels[node.derivedStatus],
      node.depends_on.length ? `依赖 ${node.depends_on.join('、')}` : '无前置依赖',
    ].join('，');
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
    aria-label={title}
    data-plan-status={graph.status}
  >
    <header className="trusted-graph-header">
      <div>
        <strong>{title}</strong>
        <span>
          Plan v{graph.version} · {progress.completed}/{progress.total} 已完成
          {historicalMode ? ' · 历史版本' : ` · ${planStatusLabel(graph.status)}`}
          {!historicalMode && graph.parallelism && ` · ${graph.parallelism.active_count} 个活动节点 · 槽位 ${graph.parallelism.used_slots}/${graph.parallelism.total_slots}`}
        </span>
      </div>
      <div className="trusted-graph-header-actions">
        {(run.plan_versions?.length ?? 0) > 1 && <label>
          <span className="sr-only">计划版本</span>
          <select
            value={selectedVersion ?? graph.version}
            disabled={historyLoading}
            onChange={(event) => { void selectVersion(Number(event.target.value)); }}
          >
            {[...(run.plan_versions ?? [])].sort((a, b) => b.version - a.version).map((version) => (
              <option value={version.version} key={version.id}>
                v{version.version} · {planStatusLabel(version.status)}
              </option>
            ))}
          </select>
        </label>}
        <div className="trusted-graph-zoom-actions" role="group" aria-label="图谱缩放">
          <button type="button" aria-label="缩小图谱" title="缩小图谱" onClick={() => { void flow.zoomOut({ duration: 160 }); }}>−</button>
          <button type="button" aria-label="放大图谱" title="放大图谱" onClick={() => { void flow.zoomIn({ duration: 160 }); }}>+</button>
          <button className="trusted-graph-center-button" type="button" aria-label="定位中心" title="定位中心（保持缩放）" onClick={centerGraph}>
            <span aria-hidden="true">◎</span>定位中心
          </button>
        </div>
      </div>
    </header>
    <div className="trusted-graph-progress" role="status" aria-live="polite" aria-label={`已完成 ${progress.completed}，共 ${progress.total}；${graph.parallelism?.active_count ?? 0} 个节点活动中`}>
      <span style={{ width: `${progress.ratio * 100}%` }} />
    </div>
    {historicalMode && <p className="trusted-graph-history-notice" role="status">
      正在查看不可执行的历史版本；实时状态仍以 v{liveGraph?.version} 为准。
    </p>}
    <div className="trusted-graph-body">
      <div className="trusted-graph-canvas" role="application" aria-label={`Plan v${graph.version} 有向无环图`}>
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

function PlanNodeCard({ data, selected }: NodeProps<Node<GraphNodeData>>) {
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
      <small className="trusted-plan-node-status">{execution ? executionPhaseLabels[execution.phase] : statusLabels[status]}</small>
    </div>
    <strong>{node.title}</strong>
    <p>{node.intent}</p>
    {execution?.wait_reason && <span className="trusted-plan-node-wait">{safeWaitReason(execution.wait_reason)}</span>}
    {dependencyProgress && dependencyProgress.total > 1 && status !== 'completed' && <span className="trusted-plan-node-join">
      汇合 {dependencyProgress.satisfied}/{dependencyProgress.total}
    </span>}
    {diff && !['unchanged'].includes(diff) && <mark>{diffLabel(diff)}</mark>}
    <Handle type="source" position={Position.Bottom} isConnectable={false} />
  </article>;
}

function FocusCurrentButton({ graph, onSelect }: { graph: PlanGraphSnapshot; onSelect: (id: string) => void }) {
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
  }}>定位活动节点{activeIds.length > 1 ? ` (${activeIds.length})` : ''}</button>;
}

function NodeInspector({ run, graph, node }: { run: RunView; graph: PlanGraphSnapshot; node: PositionedPlanNode }) {
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
  return <aside className="trusted-node-inspector" aria-label={`${node.title} 节点详情`}>
    <header><span>节点 {node.index}</span><strong>{node.title}</strong><small>{statusLabels[node.derivedStatus]}</small></header>
    <section>
      <h4>计划</h4>
      <p>{node.intent}</p>
      <dl>
        <div><dt>依赖</dt><dd>{node.depends_on.join('、') || '无'}</dd></div>
        <div><dt>预期结果</dt><dd>{node.expected_outcome?.success_condition ?? '未指定'}</dd></div>
        <div><dt>成功准则</dt><dd>{node.success_criteria_refs.join('、') || '无'}</dd></div>
        <div><dt>所需能力</dt><dd>{node.required_capabilities.join('、') || '无'}</dd></div>
        <div><dt>风险</dt><dd>{node.risk_level}{node.optional ? ' · 可选' : ''}</dd></div>
      </dl>
      {unmet.length > 0 && <p className="trusted-node-blocking">尚未满足：{unmet.map((item) => item.title).join('、')}</p>}
    </section>
    <section>
      <h4>运行轨迹</h4>
      {!turns.length && !calls.length && !approval && <p className="muted">此节点尚未执行。</p>}
      {turns.map((turn) => <div className="trusted-trace-item" key={turn.id}>
        <strong>第 {turn.turn_index} 轮 · {turn.decision_type}</strong>
        <span>{turn.reasoning_summary}</span>
        {turn.reflection && <small>反思：{String(turn.reflection.summary ?? '已调整策略')}</small>}
        {turn.evaluation && <small>验证：{String(turn.evaluation.summary ?? turn.evaluation.outcome ?? '已记录')}</small>}
      </div>)}
      {calls.map((call) => <div className="trusted-trace-item" key={call.id}>
        <strong>{call.tool_name}</strong><span>{call.status}</span>
      </div>)}
      {approval && <div className="trusted-trace-item approval"><strong>等待工具影响批准</strong><span>{approval.action_summary ?? approval.preview}</span></div>}
      {executions.map((execution) => <div className="trusted-trace-item execution" key={execution.execution_id}>
        <strong>Attempt {execution.attempt} · {executionPhaseLabels[execution.phase]}</strong>
        <span>批次 {execution.dispatch_batch_id?.slice(0, 8) ?? '—'} · 槽位 {execution.slot_index != null ? execution.slot_index + 1 : '已释放'}</span>
        {execution.started_at && <small>{formatExecutionTiming(execution)}</small>}
      </div>)}
    </section>
    <section>
      <h4>证据与产物</h4>
      {!node.evidence_refs.length && !artifacts.length && !node.failure && <p className="muted">暂无已验证证据。</p>}
      {node.evidence_refs.map((reference) => <code key={reference}>{reference}</code>)}
      {artifacts.map((artifact) => artifact.content_url
        ? <a href={artifact.content_url} target="_blank" rel="noreferrer" key={artifact.id}>{String(artifact.metadata.filename ?? artifact.type)}</a>
        : <span key={artifact.id}>{String(artifact.metadata.filename ?? artifact.type)}</span>)}
      {node.failure && <p className="trusted-node-failure">{String(node.failure.message ?? node.failure.category ?? '节点执行失败')}</p>}
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

function formatExecutionTiming(execution: NodeExecution) {
  const started = execution.started_at ? new Date(execution.started_at) : null;
  const finished = execution.finished_at ? new Date(execution.finished_at) : null;
  if (!started || Number.isNaN(started.getTime())) return '时间未记录';
  if (!finished || Number.isNaN(finished.getTime())) {
    return `开始于 ${started.toLocaleTimeString()}`;
  }
  return `${started.toLocaleTimeString()} · ${Math.max(0, finished.getTime() - started.getTime())} ms`;
}
