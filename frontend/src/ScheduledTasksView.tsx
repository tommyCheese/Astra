import { useEffect, useMemo, useState } from 'react';

import { CloseButton } from './CloseButton';
import {
  AstraApiError,
  deleteScheduledTask,
  disableHeartbeat,
  listConversations,
  listScheduledTaskRuns,
  listScheduledTasks,
  runScheduledTask,
  setScheduledTaskEnabled,
  updateHeartbeat,
  updateScheduledTask,
  type ScheduledTask,
  type ScheduledTaskRun,
} from './api';
import { useI18n } from './i18n';
import type { ConversationSummary } from './types';

type Props = {
  onClose: () => void;
  onOpenConversation: (id: string, title: string) => void;
};

function scheduleSummary(task: ScheduledTask) {
  if (task.schedule.type === 'cron') return `Cron · ${task.schedule.expression}`;
  if (task.schedule.type === 'once') return `一次 · ${new Date(task.schedule.at).toLocaleString()}`;
  const seconds = task.schedule.interval_seconds;
  if (seconds % 3600 === 0) return `每 ${seconds / 3600} 小时`;
  if (seconds % 60 === 0) return `每 ${seconds / 60} 分钟`;
  return `每 ${seconds} 秒`;
}

function displayTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—';
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    claimed: '已排队', running: '运行中', completed: '已完成', failed: '失败', blocked: '已阻塞',
    silent_ok: '静默完成', deferred_busy: '繁忙延后', skipped_misfire: '错过并跳过', skipped_overlap: '重叠并跳过',
  };
  return labels[status] ?? status;
}

function heartbeatPayload(task: ScheduledTask, enabled: boolean) {
  const intervalSeconds = task.schedule.type === 'interval' ? task.schedule.interval_seconds : 1800;
  return {
    target_task_id: task.target_task_id,
    enabled,
    interval_seconds: intervalSeconds,
    timezone: task.timezone,
    active_hours: task.heartbeat.active_hours ?? null,
    prompt: task.prompt,
    execution: task.execution,
  };
}

export function ScheduledTasksView({ onClose, onOpenConversation }: Props) {
  const { t } = useI18n();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runs, setRuns] = useState<ScheduledTaskRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const selected = tasks.find((task) => task.id === selectedId) ?? null;
  const conversationById = useMemo(() => new Map(conversations.map((item) => [item.id, item])), [conversations]);

  async function refresh(preferredId?: string | null) {
    setLoading(true);
    try {
      const [nextTasks, nextConversations] = await Promise.all([listScheduledTasks(), listConversations()]);
      setTasks(nextTasks);
      setConversations(nextConversations);
      setSelectedId((current) => {
        const wanted = preferredId ?? current;
        return nextTasks.some((item) => item.id === wanted) ? wanted : nextTasks[0]?.id ?? null;
      });
      setMessage('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法读取已安排任务');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!selectedId) { setRuns([]); return; }
    const controller = new AbortController();
    void listScheduledTaskRuns(selectedId, controller.signal).then(setRuns).catch((error) => {
      if (!(error instanceof DOMException && error.name === 'AbortError')) setMessage(error instanceof Error ? error.message : '无法读取运行历史');
    });
    return () => controller.abort();
  }, [selectedId]);

  async function perform(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setMessage('');
    try {
      await action();
      await refresh(selectedId);
      setMessage(success);
    } catch (error) {
      setMessage(error instanceof AstraApiError ? error.payload.message : error instanceof Error ? error.message : '操作失败');
    } finally {
      setBusy(false);
    }
  }

  return <section className="scheduled-tasks-page">
    <header className="scheduled-tasks-header">
      <div><span>{t('工作区')}</span><h1>{t('已安排任务')}</h1><p>{t('统一管理所有会话创建的定时任务与全局 Heartbeat。')}</p></div>
      <div><button type="button" onClick={() => void refresh(selectedId)} disabled={loading || busy}>{t('刷新')}</button><CloseButton label={t('关闭已安排任务')} onClick={onClose} /></div>
    </header>
    <div className="scheduled-tasks-layout">
      <aside className="scheduled-task-list" aria-label={t('已安排任务列表')}>
        <div className="scheduled-task-list-summary"><strong>{tasks.length}</strong><span>{t('个任务')}</span><small>{tasks.filter((task) => task.enabled).length} {t('个启用')}</small></div>
        {tasks.map((task) => <button className={selectedId === task.id ? 'active' : ''} type="button" key={task.id} onClick={() => setSelectedId(task.id)}>
          <span className={`scheduled-kind ${task.kind}`}>{task.kind === 'heartbeat' ? '♥' : '◷'}</span>
          <span><strong>{task.kind === 'heartbeat' ? t('Heartbeat') : task.name}</strong><small>{scheduleSummary(task)}</small></span>
          <i className={task.enabled ? 'enabled' : 'paused'}>{task.enabled ? t('启用') : t('暂停')}</i>
        </button>)}
        {!loading && !tasks.length && <div className="scheduled-task-empty"><strong>{t('暂无已安排任务')}</strong><p>{t('在任意对话中使用 /schedule create 或 /heartbeat on 创建任务。')}</p></div>}
      </aside>
      <main className="scheduled-task-detail">
        {message && <div className="scheduled-task-message" role="status">{t(message)}</div>}
        {loading && !selected && <div className="scheduled-task-empty">{t('正在读取已安排任务…')}</div>}
        {selected && <>
          <section className="scheduled-task-overview">
            <div><span className={`scheduled-kind ${selected.kind}`}>{selected.kind === 'heartbeat' ? '♥' : '◷'}</span><div><small>{selected.kind === 'heartbeat' ? t('全局 Heartbeat') : t('定时任务')}</small><h2>{selected.kind === 'heartbeat' ? t('Heartbeat') : selected.name}</h2></div></div>
            <div className="scheduled-task-actions">
              <button type="button" disabled={busy || !selected.enabled} onClick={() => void perform(() => runScheduledTask(selected.id), '已排队手动运行。')}>{t('立即运行')}</button>
              <button type="button" disabled={busy || (selected.kind === 'heartbeat' && !selected.target_task_id)} onClick={() => void perform(
                () => selected.kind === 'heartbeat'
                  ? selected.enabled ? disableHeartbeat() : updateHeartbeat(heartbeatPayload(selected, true))
                  : setScheduledTaskEnabled(selected, !selected.enabled),
                selected.enabled ? '已暂停任务。' : '已恢复任务。',
              )}>{selected.enabled ? t('暂停') : t('恢复')}</button>
              {selected.kind !== 'heartbeat' && <button className="danger" type="button" disabled={busy} onClick={() => { if (window.confirm(t('确定删除这个任务吗？运行历史会保留。'))) void perform(() => deleteScheduledTask(selected), '已删除任务。'); }}>{t('删除')}</button>}
            </div>
          </section>
          <div className="scheduled-task-facts">
            <div><span>{t('状态')}</span><strong>{selected.enabled ? t('启用') : t('暂停')}</strong></div>
            <div><span>{t('计划')}</span><strong>{scheduleSummary(selected)}</strong></div>
            <div><span>{t('下次运行')}</span><strong>{displayTime(selected.next_fire_at)}</strong></div>
            <div><span>{t('目标会话')}</span><strong>{selected.target_task_id ? conversationById.get(selected.target_task_id)?.title ?? selected.target_task_id : t('未指定')}</strong></div>
          </div>
          {selected.kind === 'heartbeat'
            ? <HeartbeatEditor task={selected} conversations={conversations} busy={busy} onSave={(payload) => perform(() => updateHeartbeat(payload), 'Heartbeat 设置已保存。')} />
            : <ScheduleEditor task={selected} conversations={conversations} busy={busy} onSave={(payload) => perform(() => updateScheduledTask(selected.id, payload), '任务设置已保存。')} />}
          <section className="scheduled-run-history">
            <header><div><h3>{t('运行历史')}</h3><p>{t('手动与计划触发会显示在同一条时间线上。')}</p></div><span>{runs.length}</span></header>
            {runs.map((run) => <article key={run.id}>
              <i className={`run-status ${run.status}`} />
              <div><strong>{t(statusLabel(run.status))}</strong><small>{displayTime(run.scheduled_for)} · {run.trigger_type === 'manual' ? t('手动') : t('计划')}</small></div>
              {run.task_id && <button type="button" onClick={() => onOpenConversation(run.task_id!, conversationById.get(run.task_id!)?.title ?? t('关联对话'))}>{t('查看对话')}</button>}
            </article>)}
            {!runs.length && <div className="scheduled-task-empty">{t('还没有运行记录')}</div>}
          </section>
        </>}
      </main>
    </div>
  </section>;
}

function ScheduleEditor({ task, conversations, busy, onSave }: { task: ScheduledTask; conversations: ConversationSummary[]; busy: boolean; onSave: (payload: Record<string, unknown>) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(task.name);
  const [prompt, setPrompt] = useState(task.prompt);
  const [timezone, setTimezone] = useState(task.timezone);
  const [target, setTarget] = useState(task.target_task_id ?? '');
  const [expression, setExpression] = useState(task.schedule.type === 'cron' ? task.schedule.expression : '0 9 * * *');
  const [intervalMinutes, setIntervalMinutes] = useState(task.schedule.type === 'interval' ? Math.max(1, Math.round(task.schedule.interval_seconds / 60)) : 30);
  const [onceAt, setOnceAt] = useState(task.schedule.type === 'once' ? new Date(task.schedule.at).toISOString().slice(0, 16) : '');
  const [misfirePolicy, setMisfirePolicy] = useState(task.misfire_policy);
  const [misfireGrace, setMisfireGrace] = useState(task.misfire_grace_seconds);
  useEffect(() => {
    setName(task.name); setPrompt(task.prompt); setTimezone(task.timezone); setTarget(task.target_task_id ?? '');
    if (task.schedule.type === 'cron') setExpression(task.schedule.expression);
    if (task.schedule.type === 'interval') setIntervalMinutes(Math.max(1, Math.round(task.schedule.interval_seconds / 60)));
    if (task.schedule.type === 'once') setOnceAt(new Date(task.schedule.at).toISOString().slice(0, 16));
    setMisfirePolicy(task.misfire_policy); setMisfireGrace(task.misfire_grace_seconds);
  }, [task]);
  const schedule = task.schedule.type === 'cron'
    ? { type: 'cron', expression }
    : task.schedule.type === 'interval'
      ? { type: 'interval', interval_seconds: intervalMinutes * 60 }
      : { type: 'once', at: onceAt ? new Date(onceAt).toISOString() : task.schedule.at };
  return <section className="scheduled-task-editor"><header><h3>{t('任务设置')}</h3><p>{t('修改会进行版本检查，避免覆盖其他窗口中的更新。')}</p></header>
    <div className="scheduled-editor-grid">
      <label><span>{t('名称')}</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label><span>{t('目标会话')}</span><select value={target} onChange={(event) => setTarget(event.target.value)}><option value="">{t('未指定')}</option>{conversations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>
      <label><span>{t('时区')}</span><input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="Asia/Shanghai" /></label>
      {task.schedule.type === 'cron' && <label><span>Cron</span><input value={expression} onChange={(event) => setExpression(event.target.value)} /></label>}
      {task.schedule.type === 'interval' && <label><span>{t('间隔（分钟）')}</span><input type="number" min={1} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>}
      {task.schedule.type === 'once' && <label><span>{t('运行时间')}</span><input type="datetime-local" value={onceAt} onChange={(event) => setOnceAt(event.target.value)} /></label>}
      <label><span>{t('错过触发策略')}</span><select value={misfirePolicy} onChange={(event) => setMisfirePolicy(event.target.value as 'skip' | 'fire_once')}><option value="skip">{t('跳过')}</option><option value="fire_once">{t('合并执行一次')}</option></select></label>
      <label><span>{t('宽限时间（秒）')}</span><input type="number" min={0} max={604800} value={misfireGrace} onChange={(event) => setMisfireGrace(Number(event.target.value))} /></label>
      <label className="wide"><span>{t('任务指令')}</span><textarea rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
    </div>
    <footer><button className="primary" type="button" disabled={busy || !name.trim() || !prompt.trim() || !timezone.trim() || (task.schedule.type === 'once' && !onceAt)} onClick={() => onSave({ version: task.version, name, prompt, timezone, target_task_id: target || null, schedule, misfire_policy: misfirePolicy, misfire_grace_seconds: misfireGrace })}>{t('保存更改')}</button></footer>
  </section>;
}

function HeartbeatEditor({ task, conversations, busy, onSave }: { task: ScheduledTask; conversations: ConversationSummary[]; busy: boolean; onSave: (payload: Record<string, unknown>) => void }) {
  const { t } = useI18n();
  const active = task.heartbeat.active_hours;
  const [intervalMinutes, setIntervalMinutes] = useState(task.schedule.type === 'interval' ? Math.round(task.schedule.interval_seconds / 60) : 30);
  const [timezone, setTimezone] = useState(task.timezone);
  const [target, setTarget] = useState(task.target_task_id ?? '');
  const [start, setStart] = useState(active?.start ?? '09:00');
  const [end, setEnd] = useState(active?.end ?? '22:00');
  const [prompt, setPrompt] = useState(task.prompt);
  useEffect(() => {
    if (task.schedule.type === 'interval') setIntervalMinutes(Math.round(task.schedule.interval_seconds / 60));
    setTimezone(task.timezone); setTarget(task.target_task_id ?? ''); setStart(task.heartbeat.active_hours?.start ?? '09:00'); setEnd(task.heartbeat.active_hours?.end ?? '22:00'); setPrompt(task.prompt);
  }, [task]);
  return <section className="scheduled-task-editor heartbeat-editor"><header><h3>{t('Heartbeat 设置')}</h3><p>{t('Astra 只在发现需要关注的事项时提醒你；仅返回 HEARTBEAT_OK 的检查会保持静默。')}</p></header>
    <div className="scheduled-editor-grid">
      <label><span>{t('周期（分钟）')}</span><input type="number" min={5} max={1440} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>
      <label><span>{t('时区')}</span><input value={timezone} onChange={(event) => setTimezone(event.target.value)} /></label>
      <label><span>{t('目标会话')}</span><select value={target} onChange={(event) => setTarget(event.target.value)}>{conversations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>
      <label><span>{t('活动时间')}</span><div className="active-hours"><input type="time" value={start} onChange={(event) => setStart(event.target.value)} /><b>–</b><input type="time" value={end} onChange={(event) => setEnd(event.target.value)} /></div></label>
      <label className="wide"><span>{t('检查指令')}</span><textarea rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
    </div>
    <footer><button className="primary" type="button" disabled={busy || !target || intervalMinutes < 5 || !prompt.trim()} onClick={() => onSave({ target_task_id: target, enabled: true, interval_seconds: intervalMinutes * 60, timezone, active_hours: { start, end }, prompt, execution: task.execution })}>{t('保存并启用')}</button></footer>
  </section>;
}
