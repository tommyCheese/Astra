import { useState, type FormEvent, type ReactNode } from 'react';
import type { Interrupt } from '@ag-ui/core';
import type { ProjectedActivity } from './store';

export type ActivityRenderer = (activity: ProjectedActivity) => ReactNode;
export type ActivityRendererRegistry = Partial<Record<string, ActivityRenderer>>;

function summary(activity: ProjectedActivity): string {
  const fallback = activity.content.fallbackText;
  return typeof fallback === 'string' ? fallback : `${activity.activityType} 已更新`;
}

function KnownActivity({ activity, label }: { activity: ProjectedActivity; label: string }) {
  const order = Array.isArray(activity.content.order) ? activity.content.order.filter((id): id is string => typeof id === 'string') : [];
  const byId = activity.content.byId && typeof activity.content.byId === 'object'
    ? activity.content.byId as Record<string, { status?: unknown; details?: Record<string, unknown> }>
    : {};
  const counts = activity.content.counts && typeof activity.content.counts === 'object'
    ? activity.content.counts as Record<string, unknown>
    : null;
  return (
    <section aria-label={label} data-activity-type={activity.activityType}>
      <strong>{label}</strong>
      <p>{summary(activity)}</p>
      {counts && <p aria-label="状态汇总">运行 {String(counts.active ?? 0)} · 等待 {String(counts.waiting ?? 0)} · 完成 {String(counts.completed ?? 0)} · 失败 {String(counts.failed ?? 0)}</p>}
      {order.length > 0 && (
        <ul>
          {order.map((id) => {
            const item = byId[id] ?? {};
            const details = item.details ?? {};
            const title = details.title ?? details.objective ?? details.name ?? id;
            const url = typeof details.url === 'string' && (details.url.startsWith('/api/artifacts/') || details.url.startsWith('https://'))
              ? details.url
              : null;
            return <li key={id}>{url ? <a href={url}>{String(title)}</a> : String(title)} <span>{String(item.status ?? '')}</span></li>;
          })}
        </ul>
      )}
    </section>
  );
}

export const defaultActivityRenderers: ActivityRendererRegistry = {
  'astra.plan': (activity) => <KnownActivity activity={activity} label="执行计划" />,
  'astra.agent_tree': (activity) => <KnownActivity activity={activity} label="Agent 协作" />,
  'astra.verification': (activity) => <KnownActivity activity={activity} label="结果验证" />,
  'astra.artifact': (activity) => <KnownActivity activity={activity} label="生成内容" />,
  'astra.tool_activity': (activity) => <KnownActivity activity={activity} label="工具执行" />,
};

export function GenericActivity({ activity }: { activity: ProjectedActivity }) {
  return (
    <section aria-label="Agent 活动" data-activity-type={activity.activityType}>
      <strong>Agent 活动</strong>
      <p>{summary(activity)}</p>
      {activity.error && <p role="status">{activity.error}</p>}
    </section>
  );
}

export function ActivityView({
  activity,
  registry = defaultActivityRenderers,
}: {
  activity: ProjectedActivity;
  registry?: ActivityRendererRegistry;
}) {
  const renderer = registry[activity.activityType];
  return <>{renderer && !activity.error ? renderer(activity) : <GenericActivity activity={activity} />}</>;
}

export interface InterruptResolution {
  interruptId: string;
  status: 'resolved' | 'cancelled';
  payload?: unknown;
}

const decisionLabels: Record<string, string> = {
  approve_once: '仅本次允许',
  reject: '拒绝',
  allow_similar: '允许同类操作',
};

export function InterruptView({
  interrupt,
  onResolve,
}: {
  interrupt: Interrupt;
  onResolve: (resolution: InterruptResolution) => void;
}) {
  const [value, setValue] = useState('');
  const decisionSchema = interrupt.responseSchema?.properties?.decision as { enum?: string[] } | undefined;
  const decisions = decisionSchema?.enum ?? [];
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const payload = interrupt.reason === 'tool_call' ? { decision: value } : value;
    onResolve({ interruptId: interrupt.id, status: 'resolved', payload });
  };
  return (
    <form aria-label="需要用户响应" onSubmit={submit}>
      <p>{interrupt.message ?? 'Astra 需要你的响应才能继续。'}</p>
      {decisions.length ? (
        <select aria-label="审批决定" required value={value} onChange={(event) => setValue(event.target.value)}>
          <option value="">请选择</option>
          {decisions.map((decision) => <option key={decision} value={decision}>{decisionLabels[decision] ?? decision}</option>)}
        </select>
      ) : interrupt.reason === 'confirmation' ? (
        <select aria-label="确认" required value={value} onChange={(event) => setValue(event.target.value)}>
          <option value="">请选择</option><option value="true">确认</option><option value="false">拒绝</option>
        </select>
      ) : (
        <textarea aria-label="响应内容" required value={value} onChange={(event) => setValue(event.target.value)} />
      )}
      <button type="submit">继续</button>
      <button type="button" onClick={() => onResolve({ interruptId: interrupt.id, status: 'cancelled' })}>取消</button>
    </form>
  );
}
