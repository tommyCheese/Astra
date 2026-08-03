import { useEffect, useMemo, useState } from 'react';

import { CloseButton } from './CloseButton';
import {
  AstraApiError,
  createConversation,
  createScheduledTask,
  deleteConversation,
  deleteScheduledTask,
  disableHeartbeat,
  listConversations,
  listScheduledDeliverables,
  listScheduledTaskRuns,
  listScheduledTasks,
  runScheduledTask,
  setScheduledTaskEnabled,
  updateHeartbeat,
  updateScheduledTask,
  type ScheduledTask,
  type ScheduledDeliverable,
  type ScheduledTaskRun,
} from './api';
import { useI18n } from './i18n';
import type { ConversationSummary } from './types';

type Props = {
  onClose: () => void;
  onOpenConversation: (id: string, title: string) => void;
};

type CronFrequency = 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom';
type CronPickerValue = { frequency: CronFrequency; hour: number; minute: number; weekday: number; monthDay: number; originalExpression?: string };

const DEFAULT_CRON_PICKER: CronPickerValue = { frequency: 'daily', hour: 9, minute: 0, weekday: 1, monthDay: 1 };
const WEEKDAY_LABELS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];

function parseCronPicker(expression: string): CronPickerValue {
  const [minuteText, hourText, monthDay, month, weekday] = expression.trim().split(/\s+/);
  const minute = Number(minuteText);
  const hour = Number(hourText);
  const validTime = Number.isInteger(minute) && minute >= 0 && minute <= 59 && Number.isInteger(hour) && hour >= 0 && hour <= 23;
  if (validTime && month === '*') {
    if (monthDay === '*' && weekday === '*') return { ...DEFAULT_CRON_PICKER, frequency: 'daily', hour, minute };
    if (monthDay === '*' && weekday === '1-5') return { ...DEFAULT_CRON_PICKER, frequency: 'weekdays', hour, minute };
    if (monthDay === '*' && /^[0-6]$/.test(weekday)) return { ...DEFAULT_CRON_PICKER, frequency: 'weekly', hour, minute, weekday: Number(weekday) };
    if (/^(?:[1-9]|[12]\d|3[01])$/.test(monthDay) && weekday === '*') return { ...DEFAULT_CRON_PICKER, frequency: 'monthly', hour, minute, monthDay: Number(monthDay) };
  }
  return { ...DEFAULT_CRON_PICKER, frequency: 'custom', originalExpression: expression };
}

function cronExpression(value: CronPickerValue) {
  if (value.frequency === 'custom') return value.originalExpression || '0 9 * * *';
  if (value.frequency === 'weekdays') return `${value.minute} ${value.hour} * * 1-5`;
  if (value.frequency === 'weekly') return `${value.minute} ${value.hour} * * ${value.weekday}`;
  if (value.frequency === 'monthly') return `${value.minute} ${value.hour} ${value.monthDay} * *`;
  return `${value.minute} ${value.hour} * * *`;
}

function cronPickerSummary(value: CronPickerValue) {
  if (value.frequency === 'custom') return '自定义定时';
  const time = `${String(value.hour).padStart(2, '0')}:${String(value.minute).padStart(2, '0')}`;
  if (value.frequency === 'weekdays') return `工作日 ${time}`;
  if (value.frequency === 'weekly') return `每${WEEKDAY_LABELS[value.weekday].replace('星期', '周')} ${time}`;
  if (value.frequency === 'monthly') return `每月 ${value.monthDay} 日 ${time}`;
  return `每天 ${time}`;
}

function scheduleSummary(task: ScheduledTask) {
  if (task.schedule.type === 'cron') return cronPickerSummary(parseCronPicker(task.schedule.expression));
  if (task.schedule.type === 'once') return `一次 · ${new Date(task.schedule.at).toLocaleString()}`;
  const seconds = task.schedule.interval_seconds;
  if (seconds % 3600 === 0) return `每 ${seconds / 3600} 小时`;
  if (seconds % 60 === 0) return `每 ${seconds / 60} 分钟`;
  return `每 ${seconds} 秒`;
}

function displayTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—';
}

function displayFileSize(value: number | null) {
  if (value === null) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function inlineContentUrl(value: string) {
  return `${value}${value.includes('?') ? '&' : '?'}inline=true`;
}

function deliverableIcon(deliverable: ScheduledDeliverable) {
  if (deliverable.kind === 'result') return 'Aa';
  if (deliverable.kind === 'data') return '{}';
  if (deliverable.kind === 'receipt') return '✓';
  return '↧';
}

function receiptDetails(metadata: Record<string, unknown>) {
  return [metadata.status, metadata.target, metadata.object_id]
    .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
    .join(' · ');
}

function ScheduledDeliverableCard({ deliverable, conversationTitle, onOpenConversation }: { deliverable: ScheduledDeliverable; conversationTitle: string; onOpenConversation: (id: string, title: string) => void }) {
  const { t } = useI18n();
  const mimeType = (deliverable.mime_type ?? '').split(';', 1)[0].toLowerCase();
  const imagePreview = deliverable.content_url && mimeType.startsWith('image/');
  const htmlPreview = deliverable.content_url && mimeType === 'text/html';
  const details = deliverable.kind === 'receipt' ? receiptDetails(deliverable.metadata) : '';
  return <article className={`${deliverable.kind}${imagePreview || htmlPreview ? ' previewable' : ''}`}>
    <i aria-hidden="true">{deliverableIcon(deliverable)}</i>
    <div><strong>{deliverable.title}</strong>{deliverable.summary && <p>{deliverable.summary}</p>}{details && <small>{details}</small>}<small>{displayTime(deliverable.created_at)}{['file', 'data'].includes(deliverable.kind) ? ` · ${displayFileSize(deliverable.size_bytes)}` : ''}</small></div>
    {deliverable.kind === 'result'
      ? <button type="button" onClick={() => onOpenConversation(deliverable.task_id, conversationTitle)}>{t('查看结果')}</button>
      : deliverable.kind === 'receipt'
        ? deliverable.external_url
          ? <a href={deliverable.external_url} target="_blank" rel="noreferrer">{t('打开目标')}</a>
          : <button type="button" onClick={() => onOpenConversation(deliverable.task_id, conversationTitle)}>{t('查看回执')}</button>
        : deliverable.content_url
          ? <a href={deliverable.content_url} target="_blank" rel="noreferrer">{deliverable.kind === 'data' ? t('查看数据') : t('打开文件')}</a>
          : null}
    {imagePreview && <figure className="scheduled-deliverable-preview"><img src={inlineContentUrl(deliverable.content_url!)} alt={deliverable.title} /></figure>}
    {htmlPreview && <div className="scheduled-deliverable-preview html"><iframe src={inlineContentUrl(deliverable.content_url!)} title={deliverable.title} sandbox="allow-scripts" referrerPolicy="no-referrer" /></div>}
  </article>;
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
  const [deliverables, setDeliverables] = useState<ScheduledDeliverable[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [createMode, setCreateMode] = useState<'schedule' | 'heartbeat' | null>(null);
  const selected = tasks.find((task) => task.id === selectedId) ?? null;
  const scheduledTasks = tasks.filter((task) => task.kind === 'agent');
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
    if (!selectedId) { setRuns([]); setDeliverables([]); return; }
    const controller = new AbortController();
    void Promise.all([
      listScheduledTaskRuns(selectedId, controller.signal),
      listScheduledDeliverables(selectedId, controller.signal),
    ]).then(([nextRuns, nextDeliverables]) => {
      setRuns(nextRuns);
      setDeliverables(nextDeliverables);
    }).catch((error) => {
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

  async function create(action: () => Promise<ScheduledTask>, success: string) {
    setBusy(true);
    setMessage('');
    try {
      const created = await action();
      setCreateMode(null);
      await refresh(created.id);
      setMessage(success);
    } catch (error) {
      setMessage(error instanceof AstraApiError ? error.payload.message : error instanceof Error ? error.message : '操作失败');
    } finally {
      setBusy(false);
    }
  }

  async function createBoundSchedule(
    payload: Record<string, unknown>,
    targetTaskId: string | null,
    newConversationTitle: string,
  ) {
    return createWithBoundConversation(
      targetTaskId,
      newConversationTitle,
      (target) => createScheduledTask({ ...payload, target_task_id: target }),
    );
  }

  async function createBoundHeartbeat(
    payload: Record<string, unknown>,
    targetTaskId: string | null,
    newConversationTitle: string,
  ) {
    return createWithBoundConversation(
      targetTaskId,
      newConversationTitle,
      (target) => updateHeartbeat({ ...payload, target_task_id: target }),
    );
  }

  async function createWithBoundConversation(
    targetTaskId: string | null,
    newConversationTitle: string,
    bind: (targetTaskId: string) => Promise<ScheduledTask>,
  ) {
    if (targetTaskId) return bind(targetTaskId);
    const conversation = await createConversation(newConversationTitle.trim());
    try {
      return await bind(conversation.id);
    } catch (error) {
      try { await deleteConversation(conversation.id); } catch { /* preserve the binding error */ }
      throw error;
    }
  }

  const heartbeat = tasks.find((task) => task.kind === 'heartbeat');

  return <section className="scheduled-tasks-page">
    <header className="scheduled-tasks-header">
      <div className="scheduled-tasks-header-copy"><span>{t('工作区')}</span><h1>{t('已安排任务')}</h1><p>{t('统一管理定时任务、结果对话与全局 Heartbeat。')}</p></div>
      <div className="scheduled-tasks-header-actions">
        <button className="primary scheduled-task-create-button" type="button" onClick={() => { setCreateMode('schedule'); setSelectedId(null); setMessage(''); }} disabled={busy}>{t('新建')}</button>
        <button className="scheduled-tasks-refresh-button" type="button" onClick={() => void refresh(selectedId)} disabled={loading || busy}>{t('刷新')}</button><CloseButton label={t('关闭已安排任务')} onClick={onClose} />
      </div>
    </header>
    <div className="scheduled-tasks-layout">
      <aside className="scheduled-task-list" aria-label={t('已安排任务列表')}>
        <div className="scheduled-task-list-summary"><strong>{scheduledTasks.length}</strong><span>{t('个定时任务')}</span><small>{scheduledTasks.filter((task) => task.enabled).length} {t('个启用')}</small></div>
        <h2 className="scheduled-task-list-section-label">{t('Heartbeat')}</h2>
        {heartbeat ? <button className={!createMode && selectedId === heartbeat.id ? 'active' : ''} type="button" key={heartbeat.id} onClick={() => { setCreateMode(null); setSelectedId(heartbeat.id); }}>
          <span className="scheduled-kind heartbeat">♥</span>
          <span><strong>{t('Heartbeat')}</strong><small>{t('固定间隔系统检查')} · {scheduleSummary(heartbeat)}</small></span>
          <i className={heartbeat.enabled ? 'enabled' : 'paused'}>{heartbeat.enabled ? t('启用') : t('暂停')}</i>
        </button> : <div className="scheduled-task-list-placeholder">{t('尚未配置 Heartbeat')}</div>}
        <h2 className="scheduled-task-list-section-label">{t('定时任务')}</h2>
        {scheduledTasks.map((task) => <button className={!createMode && selectedId === task.id ? 'active' : ''} type="button" key={task.id} onClick={() => { setCreateMode(null); setSelectedId(task.id); }}>
          <span className="scheduled-kind agent">◷</span>
          <span><strong>{task.name}</strong><small>{t('按计划执行指令')} · {scheduleSummary(task)}</small></span>
          <i className={task.enabled ? 'enabled' : 'paused'}>{task.enabled ? t('启用') : t('暂停')}</i>
        </button>)}
        {!loading && !tasks.length && <div className="scheduled-task-empty"><strong>{t('暂无已安排任务')}</strong><p>{t('可以在这里创建，也可以在任意对话中使用 /schedule create 或 /heartbeat on。')}</p></div>}
      </aside>
      <main className="scheduled-task-detail">
        {message && <div className="scheduled-task-message" role="status">{t(message)}</div>}
        {createMode === 'schedule' && <CreateScheduleEditor conversations={conversations} busy={busy} onTypeChange={setCreateMode} onCancel={() => { setCreateMode(null); setSelectedId(tasks[0]?.id ?? null); }} onCreate={(payload, targetTaskId, newConversationTitle) => void create(() => createBoundSchedule(payload, targetTaskId, newConversationTitle), '定时任务已创建，运行结果将投递到目标对话。')} />}
        {createMode === 'heartbeat' && <CreateHeartbeatEditor task={heartbeat} conversations={conversations} busy={busy} onTypeChange={setCreateMode} onCancel={() => { setCreateMode(null); setSelectedId(tasks[0]?.id ?? null); }} onCreate={(payload, targetTaskId, newConversationTitle) => void create(() => createBoundHeartbeat(payload, targetTaskId, newConversationTitle), heartbeat ? 'Heartbeat 设置已保存。' : 'Heartbeat 已创建并启用。')} />}
        {loading && !selected && <div className="scheduled-task-empty">{t('正在读取已安排任务…')}</div>}
        {!createMode && selected && <>
          <section className="scheduled-task-overview">
            <div><span className={`scheduled-kind ${selected.kind}`}>{selected.kind === 'heartbeat' ? '♥' : '◷'}</span><div><small>{selected.kind === 'heartbeat' ? t('全局 Heartbeat') : t('定时任务')}</small><h2>{selected.kind === 'heartbeat' ? t('Heartbeat') : selected.name}</h2></div></div>
            <div className="scheduled-task-actions">
              {selected.target_task_id && <button type="button" onClick={() => onOpenConversation(selected.target_task_id!, conversationById.get(selected.target_task_id!)?.title ?? t('结果对话'))}>{t('打开结果对话')}</button>}
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
            <div><span>{t('执行空间')}</span><strong>{t('复用目标对话')}</strong></div>
          </div>
          {selected.kind === 'heartbeat'
            ? <HeartbeatEditor task={selected} conversations={conversations} busy={busy} onSave={(payload) => perform(() => updateHeartbeat(payload), 'Heartbeat 设置已保存。')} />
            : <ScheduleEditor task={selected} conversations={conversations} busy={busy} onSave={(payload) => perform(() => updateScheduledTask(selected.id, payload), '任务设置已保存。')} />}
          <section className="scheduled-deliverables">
            <header><div><h3>{t('制品')}</h3><p>{t('集中查看每次执行的结果、文件、结构化数据和外部操作回执。')}</p></div><span>{deliverables.length}</span></header>
            <div className="scheduled-deliverable-grid">
              {deliverables.map((deliverable) => <ScheduledDeliverableCard deliverable={deliverable} conversationTitle={conversationById.get(deliverable.task_id)?.title ?? t('结果对话')} onOpenConversation={onOpenConversation} key={deliverable.id} />)}
            </div>
            {!deliverables.length && <div className="scheduled-task-empty">{t('任务运行后，结果文本和生成文件会出现在这里。')}</div>}
          </section>
          <section className="scheduled-run-history">
            <header><div><h3>{t('运行历史')}</h3><p>{t('手动与计划触发会显示在同一条时间线上。')}</p></div><span>{runs.length}</span></header>
            {runs.map((run) => <article key={run.id}>
              <i className={`run-status ${run.status}`} />
              <div><strong>{t(statusLabel(run.status))}</strong><small>{displayTime(run.scheduled_for)} · {run.trigger_type === 'manual' ? t('手动') : t('计划')}</small></div>
              {(run.task_id || selected.target_task_id) && <button type="button" onClick={() => {
                const resultTaskId = run.task_id || selected.target_task_id!;
                onOpenConversation(resultTaskId, conversationById.get(resultTaskId)?.title ?? t('结果对话'));
              }}>{selected.kind === 'heartbeat' ? t('查看对话') : t('查看结果对话')}</button>}
            </article>)}
            {!runs.length && <div className="scheduled-task-empty">{t('还没有运行记录')}</div>}
          </section>
        </>}
      </main>
    </div>
  </section>;
}

function localTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

function heartbeatIntervalHint(minutes: number) {
  if (!Number.isFinite(minutes) || minutes < 5) return '检查间隔不能少于 5 分钟，请调大后再继续。';
  if (minutes > 1440) return '检查间隔不能超过 24 小时，请调小后再继续。';
  return '可设置为 5 分钟到 24 小时。';
}

function CreateScheduleEditor({ conversations, busy, onTypeChange, onCancel, onCreate }: { conversations: ConversationSummary[]; busy: boolean; onTypeChange: (type: 'schedule' | 'heartbeat') => void; onCancel: () => void; onCreate: (payload: Record<string, unknown>, targetTaskId: string | null, newConversationTitle: string) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [timezone, setTimezone] = useState(localTimezone());
  const [scheduleType, setScheduleType] = useState<'once' | 'interval' | 'cron'>('cron');
  const [cronPicker, setCronPicker] = useState<CronPickerValue>(DEFAULT_CRON_PICKER);
  const [intervalMinutes, setIntervalMinutes] = useState(30);
  const [onceAt, setOnceAt] = useState('');
  const [misfirePolicy, setMisfirePolicy] = useState<'skip' | 'fire_once'>('skip');
  const [misfireGrace, setMisfireGrace] = useState(300);
  const [targetChoice, setTargetChoice] = useState(conversations[0]?.id ?? '__new__');
  const [newConversationTitle, setNewConversationTitle] = useState('定时任务结果');
  const schedule = scheduleType === 'cron'
    ? { type: 'cron', expression: cronExpression(cronPicker) }
    : scheduleType === 'interval'
      ? { type: 'interval', interval_seconds: intervalMinutes * 60 }
      : { type: 'once', at: onceAt ? new Date(onceAt).toISOString() : '' };
  const validTarget = targetChoice === '__new__' ? Boolean(newConversationTitle.trim()) : Boolean(targetChoice);
  const valid = Boolean(name.trim() && prompt.trim() && timezone.trim() && validTarget && (scheduleType !== 'once' || onceAt) && (scheduleType !== 'interval' || intervalMinutes >= 1));
  return <section className="scheduled-task-editor scheduled-create-editor"><header><h3>{t('新建定时任务')}</h3><p>{t('任务会直接使用结果对话的工作空间和工具权限；输出、生成文件和运行记录都保存在该对话中。')}</p></header>
    <div className="scheduled-editor-grid">
      <AutomationTypeField value="schedule" onChange={onTypeChange} />
      <label><span>{t('名称')}</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label><span>{t('结果对话')}</span><select value={targetChoice} onChange={(event) => setTargetChoice(event.target.value)}>{conversations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}<option value="__new__">{t('创建新对话')}</option></select></label>
      {targetChoice === '__new__' && <label><span>{t('新对话名称')}</span><input value={newConversationTitle} maxLength={240} onChange={(event) => setNewConversationTitle(event.target.value)} /></label>}
      <label><span>{t('计划类型')}</span><select value={scheduleType} onChange={(event) => setScheduleType(event.target.value as 'once' | 'interval' | 'cron')}><option value="cron">{t('按时间重复')}</option><option value="interval">{t('固定间隔')}</option><option value="once">{t('一次性')}</option></select></label>
      <label><span>{t('时区')}</span><input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="Asia/Shanghai" /></label>
      {scheduleType === 'cron' && <CronWheelPicker value={cronPicker} onChange={setCronPicker} />}
      {scheduleType === 'interval' && <label><span>{t('间隔（分钟）')}</span><input type="number" min={1} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>}
      {scheduleType === 'once' && <label><span>{t('运行时间')}</span><input type="datetime-local" value={onceAt} onChange={(event) => setOnceAt(event.target.value)} /></label>}
      <label><span>{t('错过触发策略')}</span><select value={misfirePolicy} onChange={(event) => setMisfirePolicy(event.target.value as 'skip' | 'fire_once')}><option value="skip">{t('跳过')}</option><option value="fire_once">{t('合并执行一次')}</option></select></label>
      <label><span>{t('宽限时间（秒）')}</span><input type="number" min={0} max={604800} value={misfireGrace} onChange={(event) => setMisfireGrace(Number(event.target.value))} /></label>
      <label className="wide"><span>{t('任务指令')}</span><textarea rows={6} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
    </div>
    <footer><button type="button" onClick={onCancel} disabled={busy}>{t('取消')}</button><button className="primary" type="button" disabled={busy || !valid} onClick={() => onCreate({ name: name.trim(), prompt: prompt.trim(), schedule, timezone, enabled: true, misfire_policy: misfirePolicy, misfire_grace_seconds: misfireGrace, overlap_policy: 'skip' }, targetChoice === '__new__' ? null : targetChoice, newConversationTitle)}>{t('创建并启用')}</button></footer>
  </section>;
}

function AutomationTypeField({ value, onChange }: { value: 'schedule' | 'heartbeat'; onChange: (value: 'schedule' | 'heartbeat') => void }) {
  const { t } = useI18n();
  return <label className="wide automation-type-field"><span>{t('类型')}</span><select value={value} onChange={(event) => onChange(event.target.value as 'schedule' | 'heartbeat')}><option value="schedule">{t('定时任务')}</option><option value="heartbeat">{t('Heartbeat')}</option></select></label>;
}

function CronWheelPicker({ value, onChange }: { value: CronPickerValue; onChange: (value: CronPickerValue) => void }) {
  const { t } = useI18n();
  const update = (patch: Partial<CronPickerValue>) => onChange({ ...value, ...patch });
  return <fieldset className="wide cron-wheel-picker">
    <legend>{t('运行频率')}</legend>
    <div className="cron-wheel-columns">
      <label><span>{t('重复方式')}</span><select value={value.frequency} onChange={(event) => update({ frequency: event.target.value as CronFrequency })}>
        {value.frequency === 'custom' && <option value="custom">{t('保留现有计划')}</option>}
        <option value="daily">{t('每天')}</option><option value="weekdays">{t('每个工作日')}</option><option value="weekly">{t('每周')}</option><option value="monthly">{t('每月')}</option>
      </select></label>
      {value.frequency === 'weekly' && <label><span>{t('星期')}</span><select value={value.weekday} onChange={(event) => update({ weekday: Number(event.target.value) })}>{WEEKDAY_LABELS.map((label, index) => <option value={index} key={label}>{t(label)}</option>)}</select></label>}
      {value.frequency === 'monthly' && <label><span>{t('日期')}</span><select value={value.monthDay} onChange={(event) => update({ monthDay: Number(event.target.value) })}>{Array.from({ length: 31 }, (_, index) => index + 1).map((day) => <option value={day} key={day}>{day} {t('日')}</option>)}</select></label>}
      {value.frequency !== 'custom' && <><label><span>{t('小时')}</span><select value={value.hour} onChange={(event) => update({ hour: Number(event.target.value) })}>{Array.from({ length: 24 }, (_, hour) => <option value={hour} key={hour}>{String(hour).padStart(2, '0')}</option>)}</select></label><label><span>{t('分钟')}</span><select value={value.minute} onChange={(event) => update({ minute: Number(event.target.value) })}>{Array.from({ length: 60 }, (_, minute) => <option value={minute} key={minute}>{String(minute).padStart(2, '0')}</option>)}</select></label></>}
    </div>
    <small>{value.frequency === 'custom' ? t('这是旧版自定义计划；选择新的重复方式后即可改用可视化计划。') : t('预计：{summary}').replace('{summary}', cronPickerSummary(value))}</small>
  </fieldset>;
}

function CreateHeartbeatEditor({ task, conversations, busy, onTypeChange, onCancel, onCreate }: { task?: Extract<ScheduledTask, { kind: 'heartbeat' }>; conversations: ConversationSummary[]; busy: boolean; onTypeChange: (type: 'schedule' | 'heartbeat') => void; onCancel: () => void; onCreate: (payload: Record<string, unknown>, targetTaskId: string | null, newConversationTitle: string) => void }) {
  const { t } = useI18n();
  const [targetChoice, setTargetChoice] = useState(task?.target_task_id ?? conversations[0]?.id ?? '__new__');
  const [newConversationTitle, setNewConversationTitle] = useState('Heartbeat 结果');
  const [intervalMinutes, setIntervalMinutes] = useState(task ? Math.round(task.schedule.interval_seconds / 60) : 30);
  const [timezone, setTimezone] = useState(task?.timezone ?? localTimezone());
  const [start, setStart] = useState(task?.heartbeat.active_hours?.start ?? '09:00');
  const [end, setEnd] = useState(task?.heartbeat.active_hours?.end ?? '22:00');
  const [prompt, setPrompt] = useState(task?.prompt ?? '检查明确记录的未完成事项与后台结果。不要从旧对话推断重复任务。如果没有需要用户关注的内容，只回复 HEARTBEAT_OK。');
  const intervalInvalid = !Number.isFinite(intervalMinutes) || intervalMinutes < 5 || intervalMinutes > 1440;
  return <section className="scheduled-task-editor scheduled-create-editor heartbeat-editor"><header><h3>{task ? t('配置 Heartbeat') : t('新建 Heartbeat')}</h3><p>{t('Heartbeat 会直接使用目标会话的工作空间和工具权限；仅返回 HEARTBEAT_OK 的检查会保持静默。')}</p></header>
    <div className="scheduled-editor-grid">
      <AutomationTypeField value="heartbeat" onChange={onTypeChange} />
      <label><span>{t('目标会话')}</span><select autoFocus value={targetChoice} onChange={(event) => setTargetChoice(event.target.value)}>{conversations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}<option value="__new__">{t('创建新对话')}</option></select></label>
      {targetChoice === '__new__' && <label><span>{t('新对话名称')}</span><input value={newConversationTitle} maxLength={240} onChange={(event) => setNewConversationTitle(event.target.value)} /></label>}
      <label><span>{t('周期（分钟）')}</span><input type="number" min={5} max={1440} value={intervalMinutes} aria-invalid={intervalInvalid} aria-describedby="heartbeat-create-interval-hint" onChange={(event) => setIntervalMinutes(Number(event.target.value))} /><small id="heartbeat-create-interval-hint" className={`scheduled-field-hint${intervalInvalid ? ' error' : ''}`} role={intervalInvalid ? 'alert' : undefined}>{t(heartbeatIntervalHint(intervalMinutes))}</small></label>
      <label><span>{t('时区')}</span><input value={timezone} onChange={(event) => setTimezone(event.target.value)} /></label>
      <label><span>{t('活动时间')}</span><div className="active-hours"><input type="time" value={start} onChange={(event) => setStart(event.target.value)} /><b>–</b><input type="time" value={end} onChange={(event) => setEnd(event.target.value)} /></div></label>
      <label className="wide"><span>{t('检查指令')}</span><textarea rows={6} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
    </div>
    <footer><button type="button" onClick={onCancel} disabled={busy}>{t('取消')}</button><button className="primary" type="button" disabled={busy || (targetChoice === '__new__' ? !newConversationTitle.trim() : !targetChoice) || intervalInvalid || !timezone.trim() || !prompt.trim()} onClick={() => onCreate({ enabled: true, interval_seconds: intervalMinutes * 60, timezone, active_hours: { start, end }, prompt: prompt.trim(), ...(task ? { execution: task.execution } : {}) }, targetChoice === '__new__' ? null : targetChoice, newConversationTitle)}>{task ? t('保存并启用') : t('创建并启用')}</button></footer>
  </section>;
}

function ScheduleEditor({ task, conversations, busy, onSave }: { task: Extract<ScheduledTask, { kind: 'agent' }>; conversations: ConversationSummary[]; busy: boolean; onSave: (payload: Record<string, unknown>) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(task.name);
  const [prompt, setPrompt] = useState(task.prompt);
  const [timezone, setTimezone] = useState(task.timezone);
  const [target, setTarget] = useState(task.target_task_id ?? conversations[0]?.id ?? '');
  const [cronPicker, setCronPicker] = useState<CronPickerValue>(task.schedule.type === 'cron' ? parseCronPicker(task.schedule.expression) : DEFAULT_CRON_PICKER);
  const [intervalMinutes, setIntervalMinutes] = useState(task.schedule.type === 'interval' ? Math.max(1, Math.round(task.schedule.interval_seconds / 60)) : 30);
  const [onceAt, setOnceAt] = useState(task.schedule.type === 'once' ? new Date(task.schedule.at).toISOString().slice(0, 16) : '');
  const [misfirePolicy, setMisfirePolicy] = useState(task.misfire_policy);
  const [misfireGrace, setMisfireGrace] = useState(task.misfire_grace_seconds);
  useEffect(() => {
    setName(task.name); setPrompt(task.prompt); setTimezone(task.timezone); setTarget(task.target_task_id ?? conversations[0]?.id ?? '');
    if (task.schedule.type === 'cron') setCronPicker(parseCronPicker(task.schedule.expression));
    if (task.schedule.type === 'interval') setIntervalMinutes(Math.max(1, Math.round(task.schedule.interval_seconds / 60)));
    if (task.schedule.type === 'once') setOnceAt(new Date(task.schedule.at).toISOString().slice(0, 16));
    setMisfirePolicy(task.misfire_policy); setMisfireGrace(task.misfire_grace_seconds);
  }, [task]);
  const schedule = task.schedule.type === 'cron'
    ? { type: 'cron', expression: cronExpression(cronPicker) }
    : task.schedule.type === 'interval'
      ? { type: 'interval', interval_seconds: intervalMinutes * 60 }
      : { type: 'once', at: onceAt ? new Date(onceAt).toISOString() : task.schedule.at };
  return <section className="scheduled-task-editor"><header><h3>{t('任务设置')}</h3><p>{t('修改会进行版本检查，避免覆盖其他窗口中的更新。')}</p></header>
    <div className="scheduled-editor-grid">
      <label><span>{t('名称')}</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label><span>{t('结果对话')}</span><select value={target} onChange={(event) => setTarget(event.target.value)}>{conversations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>
      <label><span>{t('时区')}</span><input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="Asia/Shanghai" /></label>
      {task.schedule.type === 'cron' && <CronWheelPicker value={cronPicker} onChange={setCronPicker} />}
      {task.schedule.type === 'interval' && <label><span>{t('间隔（分钟）')}</span><input type="number" min={1} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>}
      {task.schedule.type === 'once' && <label><span>{t('运行时间')}</span><input type="datetime-local" value={onceAt} onChange={(event) => setOnceAt(event.target.value)} /></label>}
      <label><span>{t('错过触发策略')}</span><select value={misfirePolicy} onChange={(event) => setMisfirePolicy(event.target.value as 'skip' | 'fire_once')}><option value="skip">{t('跳过')}</option><option value="fire_once">{t('合并执行一次')}</option></select></label>
      <label><span>{t('宽限时间（秒）')}</span><input type="number" min={0} max={604800} value={misfireGrace} onChange={(event) => setMisfireGrace(Number(event.target.value))} /></label>
      <label className="wide"><span>{t('任务指令')}</span><textarea rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
    </div>
    <footer><button className="primary" type="button" disabled={busy || !target || !name.trim() || !prompt.trim() || !timezone.trim() || (task.schedule.type === 'once' && !onceAt)} onClick={() => onSave({ version: task.version, target_task_id: target, name, prompt, timezone, schedule, misfire_policy: misfirePolicy, misfire_grace_seconds: misfireGrace })}>{t('保存更改')}</button></footer>
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
  const intervalInvalid = !Number.isFinite(intervalMinutes) || intervalMinutes < 5 || intervalMinutes > 1440;
  useEffect(() => {
    if (task.schedule.type === 'interval') setIntervalMinutes(Math.round(task.schedule.interval_seconds / 60));
    setTimezone(task.timezone); setTarget(task.target_task_id ?? ''); setStart(task.heartbeat.active_hours?.start ?? '09:00'); setEnd(task.heartbeat.active_hours?.end ?? '22:00'); setPrompt(task.prompt);
  }, [task]);
  return <section className="scheduled-task-editor heartbeat-editor"><header><h3>{t('Heartbeat 设置')}</h3><p>{t('Astra 只在发现需要关注的事项时提醒你；仅返回 HEARTBEAT_OK 的检查会保持静默。')}</p></header>
    <div className="scheduled-editor-grid">
      <label><span>{t('周期（分钟）')}</span><input type="number" min={5} max={1440} value={intervalMinutes} aria-invalid={intervalInvalid} aria-describedby="heartbeat-edit-interval-hint" onChange={(event) => setIntervalMinutes(Number(event.target.value))} /><small id="heartbeat-edit-interval-hint" className={`scheduled-field-hint${intervalInvalid ? ' error' : ''}`} role={intervalInvalid ? 'alert' : undefined}>{t(heartbeatIntervalHint(intervalMinutes))}</small></label>
      <label><span>{t('时区')}</span><input value={timezone} onChange={(event) => setTimezone(event.target.value)} /></label>
      <label><span>{t('目标会话')}</span><select value={target} onChange={(event) => setTarget(event.target.value)}>{conversations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select></label>
      <label><span>{t('活动时间')}</span><div className="active-hours"><input type="time" value={start} onChange={(event) => setStart(event.target.value)} /><b>–</b><input type="time" value={end} onChange={(event) => setEnd(event.target.value)} /></div></label>
      <label className="wide"><span>{t('检查指令')}</span><textarea rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
    </div>
    <footer><button className="primary" type="button" disabled={busy || !target || intervalInvalid || !prompt.trim()} onClick={() => onSave({ target_task_id: target, enabled: true, interval_seconds: intervalMinutes * 60, timezone, active_hours: { start, end }, prompt, execution: task.execution })}>{t('保存并启用')}</button></footer>
  </section>;
}
