import { useEffect, useMemo, useState } from 'react';
import { AstraApiError, getUsageSummary, type UsageSummary } from './api';
import { CloseButton } from './CloseButton';
import { useI18n } from './i18n';

type Range = 'all' | '7d' | '30d' | 'task' | 'run';

export function UsageDashboard({ taskId, runId, onClose }: { taskId?: string; runId?: string; onClose: () => void }) {
  const { language, t } = useI18n();
  const [range, setRange] = useState<Range>(taskId ? 'task' : 'all');
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const request = useMemo(() => {
    if (range === 'task') return { scope: 'task' as const, taskId };
    if (range === 'run') return { scope: 'run' as const, runId };
    if (range === '7d' || range === '30d') return { scope: 'all' as const, from: new Date(Date.now() - (range === '7d' ? 7 : 30) * 86400000).toISOString() };
    return { scope: 'all' as const };
  }, [range, runId, taskId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void getUsageSummary(request, controller.signal)
      .then(setData)
      .catch((reason) => { if (reason?.name !== 'AbortError') setError(reason instanceof AstraApiError ? reason.payload.message : t('无法加载用量数据。')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [request, reload, t]);

  const format = (value: number) => value.toLocaleString(language);
  const coverage = data ? Math.round(data.coverage.ratio * 100) : 0;
  const interpolate = (key: string, values: Record<string, string | number>) => Object.entries(values).reduce((text, [name, value]) => text.replace(`{${name}}`, String(value)), t(key));
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="usage-dashboard" role="dialog" aria-modal="true" aria-label={t('用量统计')} onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span>{t('使用情况')}</span><h2>{t('用量看板')}</h2><p>{t('查看模型、工具和任务活动的用量。')}</p></div><CloseButton label={t('关闭用量统计')} onClick={onClose} /></header>
      <nav className="usage-ranges" aria-label={t('统计范围')}>
        {([['all', '全部历史'], ['7d', '最近 7 天'], ['30d', '最近 30 天'], ['task', '当前对话'], ['run', '当前运行']] as const).map(([value, label]) => <button type="button" className={range === value ? 'active' : ''} disabled={(value === 'task' && !taskId) || (value === 'run' && !runId)} onClick={() => setRange(value)} key={value}>{t(label)}</button>)}
      </nav>
      {loading ? <div className="usage-state">{t('正在读取用量…')}</div> : error ? <div className="usage-state error"><strong>{t('加载失败')}</strong><span>{error}</span><button type="button" onClick={() => setReload((value) => value + 1)}>{t('重试')}</button></div> : !data || (!data.overview.model_invocations && !data.overview.tool_calls && !data.overview.agent_turns) ? <div className="usage-state"><strong>{t('所选范围暂无用量记录')}</strong><span>{t('完成一次任务后，模型、工具与产物用量会在此显示。')}</span></div> : <>
        <div className="usage-metrics">
          <Metric label={t('模型调用')} value={format(data.overview.model_invocations)} detail={interpolate('{success} 成功 · {failed} 失败', { success: data.overview.successful_invocations, failed: data.overview.failed_invocations })} />
          <Metric label={t('Token 总量')} value={format(data.tokens.total)} detail={interpolate('{reported}/{total} 次已报告', { reported: data.coverage.reported_invocations, total: data.coverage.total_invocations })} />
          <Metric label={t('工具调用')} value={format(data.overview.tool_calls)} detail={data.overview.tool_success_rate == null ? t('暂无已完成调用') : interpolate('{rate}% 成功率', { rate: Math.round(data.overview.tool_success_rate * 100) })} />
          <Metric label={t('Agent 轮次')} value={format(data.overview.agent_turns)} detail={interpolate('{count} 条 Memory', { count: data.overview.memories })} />
        </div>
        <section className={`usage-coverage ${data.coverage.complete ? 'complete' : ''}`}><div><strong>{interpolate('用量数据完整度 {coverage}%', { coverage })}</strong><span>{t(data.coverage.complete ? '全部调用均包含用量数据' : '部分调用暂未提供用量数据')}</span></div><div className="coverage-track"><i style={{ width: `${coverage}%` }} /></div></section>
        <div className="usage-columns">
          <section><h3>{t('Token 构成')}</h3><dl className="usage-token-list"><Row label={t('输入')} value={format(data.tokens.input)} /><Row label={t('缓存输入')} value={format(data.tokens.cached_input)} /><Row label={t('输出')} value={format(data.tokens.output)} /><Row label={t('推理')} value={format(data.tokens.reasoning)} /></dl></section>
          <section><h3>{t('模型明细')}</h3>{data.models.length ? <div className="usage-table">{data.models.map((item) => <div key={`${item.provider}:${item.model}`}><span><strong>{item.model}</strong><small>{item.provider}</small></span><span>{interpolate('{count} Token', { count: format(item.tokens.total) })}<small>{interpolate('{reported}/{total} 已报告', { reported: item.reported_invocations, total: item.invocations })}</small></span></div>)}</div> : <p className="usage-muted">{t('暂无模型调用')}</p>}</section>
          <section><h3>{t('工具明细')}</h3>{data.tools.length ? <div className="usage-table">{data.tools.map((item) => <div key={item.tool_name}><span><strong>{item.tool_name}</strong><small>{interpolate('{success} 成功 · {failed} 失败', { success: item.succeeded, failed: item.failed })}</small></span><span>{interpolate('{count} 次', { count: item.calls })}<small>{item.success_rate == null ? '—' : `${Math.round(item.success_rate * 100)}%`}</small></span></div>)}</div> : <p className="usage-muted">{t('暂无工具调用')}</p>}</section>
        </div>
        {data.trend.length > 0 && <section className="usage-trend"><h3>{t('按日趋势')}</h3><div>{data.trend.map((item) => { const max = Math.max(...data.trend.map((point) => point.tokens), 1); return <div key={item.date}><span>{item.date}</span><i><b style={{ width: `${Math.max(3, item.tokens / max * 100)}%` }} /></i><strong>{interpolate('{count} Token', { count: format(item.tokens) })}</strong><small>{interpolate('{models} 次模型 · {tools} 次工具', { models: item.invocations, tools: item.tool_calls })}</small></div>; })}</div></section>}
      </>}
    </section>
  </div>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>; }
function Row({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
