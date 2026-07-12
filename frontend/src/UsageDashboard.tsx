import { useEffect, useMemo, useState } from 'react';
import { AstraApiError, getUsageSummary, type UsageSummary } from './api';

type Range = 'all' | '7d' | '30d' | 'task' | 'run';

export function UsageDashboard({ taskId, runId, onClose }: { taskId?: string; runId?: string; onClose: () => void }) {
  const [range, setRange] = useState<Range>(taskId ? 'task' : 'all');
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const request = useMemo(() => {
    if (range === 'task') return { scope: 'task' as const, taskId };
    if (range === 'run') return { scope: 'run' as const, runId };
    if (range === '7d' || range === '30d') {
      const days = range === '7d' ? 7 : 30;
      return { scope: 'all' as const, from: new Date(Date.now() - days * 86400000).toISOString() };
    }
    return { scope: 'all' as const };
  }, [range, runId, taskId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void getUsageSummary(request, controller.signal)
      .then(setData)
      .catch((reason) => { if (reason?.name !== 'AbortError') setError(reason instanceof AstraApiError ? reason.payload.message : '无法加载用量数据。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [request, reload]);

  const format = (value: number) => value.toLocaleString('zh-CN');
  const coverage = data ? Math.round(data.coverage.ratio * 100) : 0;
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="usage-dashboard" role="dialog" aria-modal="true" aria-label="用量统计" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span>持久化统计</span><h2>用量看板</h2><p>数据来自数据库与模型供应商，不使用前端估算。</p></div><button className="close-button" type="button" aria-label="关闭用量统计" onClick={onClose}>×</button></header>
      <nav className="usage-ranges" aria-label="统计范围">
        {([['all', '全部历史'], ['7d', '最近 7 天'], ['30d', '最近 30 天'], ['task', '当前对话'], ['run', '当前运行']] as const).map(([value, label]) => <button type="button" className={range === value ? 'active' : ''} disabled={(value === 'task' && !taskId) || (value === 'run' && !runId)} onClick={() => setRange(value)} key={value}>{label}</button>)}
      </nav>
      {loading ? <div className="usage-state">正在读取持久化用量…</div> : error ? <div className="usage-state error"><strong>加载失败</strong><span>{error}</span><button type="button" onClick={() => setReload((value) => value + 1)}>重试</button></div> : !data || (!data.overview.model_invocations && !data.overview.tool_calls && !data.overview.agent_turns) ? <div className="usage-state"><strong>所选范围暂无用量记录</strong><span>完成一次任务后，模型、工具与产物用量会在此显示。</span></div> : <>
        <div className="usage-metrics">
          <Metric label="模型调用" value={format(data.overview.model_invocations)} detail={`${data.overview.successful_invocations} 成功 · ${data.overview.failed_invocations} 失败`} />
          <Metric label="Token 总量" value={format(data.tokens.total)} detail={`${data.coverage.reported_invocations}/${data.coverage.total_invocations} 次已报告`} />
          <Metric label="工具调用" value={format(data.overview.tool_calls)} detail={data.overview.tool_success_rate == null ? '暂无已完成调用' : `${Math.round(data.overview.tool_success_rate * 100)}% 成功率`} />
          <Metric label="Agent 轮次" value={format(data.overview.agent_turns)} detail={`${data.overview.memories} 条 Memory`} />
        </div>
        <section className={`usage-coverage ${data.coverage.complete ? 'complete' : ''}`}><div><strong>Token 报告覆盖率 {coverage}%</strong><span>{data.coverage.complete ? '供应商已为全部调用返回精确用量' : '未报告的调用保持未知，不计为 0 或估算值'}</span></div><div className="coverage-track"><i style={{ width: `${coverage}%` }} /></div></section>
        <div className="usage-columns">
          <section><h3>Token 构成</h3><dl className="usage-token-list"><Row label="输入" value={format(data.tokens.input)} /><Row label="缓存输入" value={format(data.tokens.cached_input)} /><Row label="输出" value={format(data.tokens.output)} /><Row label="推理" value={format(data.tokens.reasoning)} /></dl></section>
          <section><h3>模型明细</h3>{data.models.length ? <div className="usage-table">{data.models.map((item) => <div key={`${item.provider}:${item.model}`}><span><strong>{item.model}</strong><small>{item.provider}</small></span><span>{format(item.tokens.total)} tokens<small>{item.reported_invocations}/{item.invocations} 已报告</small></span></div>)}</div> : <p className="usage-muted">暂无模型调用</p>}</section>
          <section><h3>工具明细</h3>{data.tools.length ? <div className="usage-table">{data.tools.map((item) => <div key={item.tool_name}><span><strong>{item.tool_name}</strong><small>{item.succeeded} 成功 · {item.failed} 失败</small></span><span>{item.calls} 次<small>{item.success_rate == null ? '—' : `${Math.round(item.success_rate * 100)}%`}</small></span></div>)}</div> : <p className="usage-muted">暂无工具调用</p>}</section>
        </div>
        {data.trend.length > 0 && <section className="usage-trend"><h3>按日趋势</h3><div>{data.trend.map((item) => { const max = Math.max(...data.trend.map((point) => point.tokens), 1); return <div key={item.date}><span>{item.date}</span><i><b style={{ width: `${Math.max(3, item.tokens / max * 100)}%` }} /></i><strong>{format(item.tokens)} tokens</strong><small>{item.invocations} 次模型 · {item.tool_calls} 次工具</small></div>; })}</div></section>}
      </>}
    </section>
  </div>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>; }
function Row({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
