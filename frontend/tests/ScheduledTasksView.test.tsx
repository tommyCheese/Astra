import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ScheduledTasksView } from '../src/ScheduledTasksView';
import { I18nProvider } from '../src/i18n';
import * as api from '../src/api';

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api')>();
  return {
    ...actual,
    listScheduledTasks: vi.fn(),
    createScheduledTask: vi.fn(),
    createConversation: vi.fn(),
    listScheduledDeliverables: vi.fn(),
    listScheduledTaskRuns: vi.fn(),
    listConversations: vi.fn(),
    setScheduledTaskEnabled: vi.fn(),
    runScheduledTask: vi.fn(),
    updateScheduledTask: vi.fn(),
    updateHeartbeat: vi.fn(),
    disableHeartbeat: vi.fn(),
    deleteScheduledTask: vi.fn(),
  };
});

const task: api.ScheduledTask = {
  id: 'schedule-1', name: '日报', kind: 'agent', system_managed: false, owner_principal: 'local-user', target_task_id: 'task-1',
  prompt: '生成日报', schedule_type: 'cron', schedule: { type: 'cron', expression: '0 9 * * *' }, timezone: 'Asia/Shanghai', enabled: true,
  misfire_policy: 'skip', misfire_grace_seconds: 300, overlap_policy: 'skip', execution: {}, heartbeat: {},
  next_fire_at: '2026-08-03T01:00:00Z', last_fire_at: null, version: 1, created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
};
const heartbeat: api.ScheduledTask = { ...task, id: 'heartbeat-1', name: 'Heartbeat', kind: 'heartbeat', system_managed: true, target_task_id: 'task-1', schedule_type: 'interval', schedule: { type: 'interval', interval_seconds: 1800 }, heartbeat: { active_hours: { start: '09:00', end: '22:00' } } };

describe('ScheduledTasksView', () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([task]);
    vi.mocked(api.listScheduledTaskRuns).mockResolvedValue([{ id: 'scheduled-run-1', job_id: task.id, scheduled_for: '2026-08-02T01:00:00Z', trigger_type: 'manual', status: 'completed', task_id: 'task-1', run_id: 'run-1', outcome: {}, claimed_at: null, started_at: null, completed_at: null, created_at: '2026-08-02T01:00:00Z' }]);
    vi.mocked(api.listScheduledDeliverables).mockResolvedValue([
      { id: 'result:scheduled-run-1', job_id: task.id, schedule_run_id: 'scheduled-run-1', run_id: 'run-1', task_id: 'task-1', kind: 'result', title: '执行结果', summary: '日报生成完成', mime_type: null, size_bytes: null, content_url: null, external_url: null, metadata: {}, created_at: '2026-08-02T01:00:00Z' },
      { id: 'workspace-file:file-1:scheduled-run-1', job_id: task.id, schedule_run_id: 'scheduled-run-1', run_id: 'run-1', task_id: 'task-1', kind: 'file', title: 'daily.txt', summary: 'reports/daily.txt', mime_type: 'text/plain', size_bytes: 1024, content_url: '/api/tasks/task-1/workspace/files/file-1/content', external_url: null, metadata: {}, created_at: '2026-08-02T01:00:00Z' },
      { id: 'workspace-file:data-1:scheduled-run-1', job_id: task.id, schedule_run_id: 'scheduled-run-1', run_id: 'run-1', task_id: 'task-1', kind: 'data', title: 'metrics.json', summary: 'exports/metrics.json', mime_type: 'application/json', size_bytes: 320, content_url: '/api/tasks/task-1/workspace/files/data-1/content', external_url: null, metadata: {}, created_at: '2026-08-02T01:00:00Z' },
      { id: 'receipt:call-1', job_id: task.id, schedule_run_id: 'scheduled-run-1', run_id: 'run-1', task_id: 'task-1', kind: 'receipt', title: 'publish 操作回执', summary: 'Dashboard updated', mime_type: null, size_bytes: null, content_url: null, external_url: 'https://example.test/report/42', metadata: { status: 'published', target: 'dashboard', object_id: '42' }, created_at: '2026-08-02T01:00:00Z' },
      { id: 'workspace-file:image-1:scheduled-run-1', job_id: task.id, schedule_run_id: 'scheduled-run-1', run_id: 'run-1', task_id: 'task-1', kind: 'file', title: 'chart.png', summary: 'charts/chart.png', mime_type: 'image/png', size_bytes: 2048, content_url: '/api/tasks/task-1/workspace/files/image-1/content', external_url: null, metadata: {}, created_at: '2026-08-02T01:00:00Z' },
      { id: 'workspace-file:html-1:scheduled-run-1', job_id: task.id, schedule_run_id: 'scheduled-run-1', run_id: 'run-1', task_id: 'task-1', kind: 'file', title: 'dashboard.html', summary: 'reports/dashboard.html', mime_type: 'text/html', size_bytes: 4096, content_url: '/api/tasks/task-1/workspace/files/html-1/content', external_url: null, metadata: {}, created_at: '2026-08-02T01:00:00Z' },
    ]);
    vi.mocked(api.listConversations).mockResolvedValue([{ id: 'task-1', title: '日报会话', title_source: 'auto', pinned_at: null, created_at: '', updated_at: '', last_run_status: 'completed', last_message_preview: '', has_active_share: false }]);
  });

  it('shows the global task, target conversation, and run history', async () => {
    const openConversation = vi.fn();
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={openConversation} /></I18nProvider>);

    expect(await screen.findByRole('heading', { name: '已安排任务' })).toBeInTheDocument();
    const createButton = screen.getByRole('button', { name: '新建' });
    expect(createButton).toBeVisible();
    expect(createButton.closest('.scheduled-tasks-header-actions')).not.toBeNull();
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);
    expect(screen.getByText('个定时任务')).toBeInTheDocument();
    expect(screen.getByText('尚未配置 Heartbeat')).toBeInTheDocument();
    expect((await screen.findAllByText('日报会话')).length).toBeGreaterThan(0);
    expect(screen.getByText('复用目标对话')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: '制品' })).toBeInTheDocument();
    expect(screen.getByText('日报生成完成')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '打开文件' }).some((link) => link.getAttribute('href') === '/api/tasks/task-1/workspace/files/file-1/content')).toBe(true);
    expect(screen.getByRole('link', { name: '查看数据' })).toHaveAttribute('href', '/api/tasks/task-1/workspace/files/data-1/content');
    expect(screen.getByRole('link', { name: '打开目标' })).toHaveAttribute('href', 'https://example.test/report/42');
    expect(screen.getByRole('img', { name: 'chart.png' })).toHaveAttribute('src', '/api/tasks/task-1/workspace/files/image-1/content?inline=true');
    expect(screen.getByTitle('dashboard.html')).toHaveAttribute('src', '/api/tasks/task-1/workspace/files/html-1/content?inline=true');
    expect(await screen.findByText('已完成')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看结果对话' }));
    expect(openConversation).toHaveBeenCalledWith('task-1', '日报会话');
  });

  it('pauses a task through the global API and refreshes the list', async () => {
    vi.mocked(api.setScheduledTaskEnabled).mockResolvedValue({ ...task, enabled: false, version: 2 });
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '暂停' }));
    await waitFor(() => expect(api.setScheduledTaskEnabled).toHaveBeenCalledWith(task, false));
  });

  it('keeps heartbeat separate from the scheduled-task count', async () => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([heartbeat, task]);
    const { container } = render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    await screen.findByText(/固定间隔系统检查/);
    expect(screen.getByText(/按计划执行指令/)).toBeInTheDocument();
    expect(container.querySelector('.scheduled-task-list-summary strong')).toHaveTextContent('1');
  });

  it('creates a scheduled task from the management page without constructing a permission bundle', async () => {
    vi.mocked(api.createScheduledTask).mockResolvedValue(task);
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '新建' }));
    expect(screen.getByText(/直接使用结果对话的工作空间和工具权限/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '晨间摘要' } });
    fireEvent.change(screen.getByLabelText('任务指令'), { target: { value: '整理今天的重点' } });
    fireEvent.click(screen.getByRole('button', { name: '创建并启用' }));

    await waitFor(() => expect(api.createScheduledTask).toHaveBeenCalled());
    const payload = vi.mocked(api.createScheduledTask).mock.calls[0][0];
    expect(payload).toMatchObject({ name: '晨间摘要', target_task_id: 'task-1', prompt: '整理今天的重点', schedule: { type: 'cron', expression: '0 9 * * *' } });
    expect(payload).not.toHaveProperty('execution');
  });

  it('builds a weekly schedule with visual wheels instead of a cron text field', async () => {
    vi.mocked(api.createScheduledTask).mockResolvedValue(task);
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '新建' }));
    expect(screen.queryByLabelText('Cron')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '周报' } });
    fireEvent.change(screen.getByLabelText('重复方式'), { target: { value: 'weekly' } });
    fireEvent.change(screen.getByLabelText('星期'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('小时'), { target: { value: '18' } });
    fireEvent.change(screen.getByLabelText('分钟'), { target: { value: '30' } });
    fireEvent.change(screen.getByLabelText('任务指令'), { target: { value: '生成本周总结' } });
    fireEvent.click(screen.getByRole('button', { name: '创建并启用' }));

    await waitFor(() => expect(api.createScheduledTask).toHaveBeenCalledWith(expect.objectContaining({ schedule: { type: 'cron', expression: '30 18 * * 5' } })));
  });

  it('creates and binds a new result conversation for a scheduled task', async () => {
    vi.mocked(api.listConversations).mockResolvedValue([]);
    vi.mocked(api.createConversation).mockResolvedValue({ id: 'task-new', title: '自动化产出', title_source: 'user', pinned_at: null, created_at: '', updated_at: '', last_run_status: null, last_message_preview: '', has_active_share: false });
    vi.mocked(api.createScheduledTask).mockResolvedValue({ ...task, target_task_id: 'task-new' });
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '新建' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '归档报告' } });
    fireEvent.change(screen.getByLabelText('新对话名称'), { target: { value: '自动化产出' } });
    fireEvent.change(screen.getByLabelText('任务指令'), { target: { value: '生成报告文件' } });
    fireEvent.click(screen.getByRole('button', { name: '创建并启用' }));

    await waitFor(() => expect(api.createConversation).toHaveBeenCalledWith('自动化产出'));
    expect(api.createScheduledTask).toHaveBeenCalledWith(expect.objectContaining({ target_task_id: 'task-new' }));
  });

  it('creates the workspace heartbeat from the management page', async () => {
    vi.mocked(api.updateHeartbeat).mockResolvedValue(heartbeat);
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '新建' }));
    fireEvent.change(screen.getByLabelText('类型'), { target: { value: 'heartbeat' } });
    expect(screen.getByText('可设置为 5 分钟到 24 小时。')).toBeInTheDocument();
    const intervalInput = screen.getByRole('spinbutton', { name: /周期（分钟）/ });
    fireEvent.change(intervalInput, { target: { value: '2' } });
    expect(screen.getByRole('alert')).toHaveTextContent('检查间隔不能少于 5 分钟，请调大后再继续。');
    expect(screen.getByRole('button', { name: '创建并启用' })).toBeDisabled();
    fireEvent.change(intervalInput, { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: '创建并启用' }));

    await waitFor(() => expect(api.updateHeartbeat).toHaveBeenCalled());
    const payload = vi.mocked(api.updateHeartbeat).mock.calls[0][0];
    expect(payload).toMatchObject({ target_task_id: 'task-1', enabled: true, interval_seconds: 1800, active_hours: { start: '09:00', end: '22:00' } });
    expect(payload).not.toHaveProperty('execution');
  });
});
