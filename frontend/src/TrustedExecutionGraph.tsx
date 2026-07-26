import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  Background,
  Handle,
  MarkerType,
  MiniMap,
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
import type { PlanGraphDiff, PlanGraphNode, PlanGraphSnapshot, PlanNodeStatus, RunView } from './types';

type GraphNodeData = {
  node: PlanGraphNode;
  status: PlanNodeStatus;
  diff?: PlanGraphDiff['nodes'][number]['change'];
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
  const [fullscreen, setFullscreen] = useState(false);
  const [layout, setLayout] = useState<PlanGraphLayout | undefined>(
    () => liveGraph ? layoutPlanGraph(liveGraph) : undefined,
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const expandButtonRef = useRef<HTMLButtonElement>(null);
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
    if (!fullscreen) expandButtonRef.current?.focus();
  }, [fullscreen]);

  useEffect(() => {
    if (!layout?.nodes.length) return;
    const frame = window.requestAnimationFrame(() => {
      void flow.fitView({ padding: 0.18, duration: 0 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [compact, flow, fullscreen, layout?.planId, layout?.topologyKey]);

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

  function navigateNodeList(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const targetIndex = event.key === 'ArrowDown' || event.key === 'ArrowRight'
      ? Math.min(layout!.nodes.length - 1, index + 1)
      : event.key === 'ArrowUp' || event.key === 'ArrowLeft'
        ? Math.max(0, index - 1)
        : event.key === 'Home' ? 0
          : event.key === 'End' ? layout!.nodes.length - 1
            : null;
    if (targetIndex === null) return;
    event.preventDefault();
    const target = layout!.nodes[targetIndex];
    setSelectedNodeId(target.id);
    event.currentTarget.closest('ol')
      ?.querySelectorAll<HTMLButtonElement>('[data-plan-node-id]')
      .item(targetIndex)
      .focus();
  }

  if (!graph || !layout) return null;
  const progress = planProgress(graph);
  const selected = layout.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const nodeDiff = new Map(diff?.nodes.map((item) => [item.node_id, item.change]) ?? []);
  const edgeDiff = new Map(diff?.edges.map((item) => [
    `${item.predecessor_node_id}>${item.successor_node_id}`,
    item.change,
  ]) ?? []);
  const nodes: Node<GraphNodeData>[] = layout.nodes.map((node) => ({
    id: node.id,
    type: 'planNode',
    position: node.position,
    initialWidth: 236,
    initialHeight: 112,
    data: { node, status: node.derivedStatus, diff: nodeDiff.get(node.id) },
    selected: node.id === selectedNodeId,
    draggable: false,
  }));
  const edges: Edge[] = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.predecessor_node_id,
    target: edge.successor_node_id,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed },
    className: `plan-edge diff-${edgeDiff.get(`${edge.predecessor_node_id}>${edge.successor_node_id}`) ?? 'none'}`,
    animated: false,
  }));
  const historicalMode = graph.id !== liveGraph?.id;
  return <section
    className={`trusted-graph-workbench ${compact ? 'compact' : ''} ${fullscreen ? 'fullscreen' : ''}`}
    aria-label={title}
  >
    <header className="trusted-graph-header">
      <div>
        <strong>{title}</strong>
        <span>
          Plan v{graph.version} · {progress.completed}/{progress.total} 已完成
          {historicalMode ? ' · 历史版本' : ` · ${planStatusLabel(graph.status)}`}
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
          <button type="button" aria-label="适应视图" title="适应视图" onClick={() => { void flow.fitView({ padding: 0.18, duration: 160 }); }}>适应</button>
        </div>
        <button ref={expandButtonRef} type="button" onClick={() => setFullscreen((value) => !value)}>
          {fullscreen ? '退出全屏' : '展开图谱'}
        </button>
      </div>
    </header>
    <div className="trusted-graph-progress" role="status" aria-live="polite" aria-label={`已完成 ${progress.completed}，共 ${progress.total}`}>
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
          elementsSelectable
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.28}
          maxZoom={1.6}
          onNodeClick={(_event, node) => setSelectedNodeId(node.id)}
          onNodeDoubleClick={() => setFullscreen(true)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={20} size={1} />
          {!compact && <MiniMap pannable zoomable nodeStrokeWidth={3} />}
          <FocusCurrentButton graph={graph} onSelect={setSelectedNodeId} />
        </ReactFlow>
      </div>
      {selected && <NodeInspector run={run} graph={graph} node={selected} />}
    </div>
    <details className="trusted-graph-accessible-list">
      <summary>结构化节点列表</summary>
      <ol>
        {layout.nodes.map((node, index) => <li key={node.id}>
          <button
            type="button"
            data-plan-node-id={node.id}
            aria-current={node.id === selectedNodeId ? 'true' : undefined}
            onClick={() => setSelectedNodeId(node.id)}
            onKeyDown={(event) => navigateNodeList(event, index)}
          >
            <strong>{node.index}. {node.title}</strong>
            <span>{statusLabels[node.derivedStatus]}</span>
            <small>{node.depends_on.length ? `依赖：${node.depends_on.join('、')}` : '无前置依赖'}</small>
          </button>
        </li>)}
      </ol>
    </details>
  </section>;
}

function PlanNodeCard({ data, selected }: NodeProps<Node<GraphNodeData>>) {
  const { node, status, diff } = data;
  return <article
    className={`trusted-plan-node status-${status} ${selected ? 'selected' : ''} ${diff ? `diff-${diff}` : ''}`}
    aria-label={`${node.title}，${statusLabels[status]}`}
  >
    <Handle type="target" position={Position.Top} isConnectable={false} />
    <div className="trusted-plan-node-heading">
      <span>{node.index}</span>
      <em>{statusIcon(status)}</em>
      <small>{statusLabels[status]}</small>
    </div>
    <strong>{node.title}</strong>
    <p>{node.intent}</p>
    {diff && !['unchanged'].includes(diff) && <mark>{diffLabel(diff)}</mark>}
    <Handle type="source" position={Position.Bottom} isConnectable={false} />
  </article>;
}

function FocusCurrentButton({ graph, onSelect }: { graph: PlanGraphSnapshot; onSelect: (id: string) => void }) {
  const flow = useReactFlow();
  const current = graph.nodes.find((node) => node.status === 'running')
    ?? graph.nodes.find((node) => node.status === 'pending');
  if (!current) return null;
  return <button className="trusted-graph-focus" type="button" onClick={() => {
    onSelect(current.id);
    void flow.fitView({ nodes: [{ id: current.id }], duration: 250, padding: 1.5 });
  }}>定位当前节点</button>;
}

function NodeInspector({ run, graph, node }: { run: RunView; graph: PlanGraphSnapshot; node: PositionedPlanNode }) {
  const unmet = unmetDependencies(graph, node.id);
  const {
    turns,
    toolCalls: calls,
    artifacts,
    pendingApproval: approval,
  } = nodeTraceAssociations(run, node.id);
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

function diffLabel(change: PlanGraphDiff['nodes'][number]['change']) {
  return change === 'added' ? '新增'
    : change === 'removed' ? '移除'
      : change === 'modified' ? '已修改'
        : change === 'inherited_completed' ? '继承成果'
          : '未变化';
}
