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

describe('ScheduledTasksView', () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([task]);
    vi.mocked(api.listScheduledTaskRuns).mockResolvedValue([{ id: 'scheduled-run-1', job_id: task.id, scheduled_for: '2026-08-02T01:00:00Z', trigger_type: 'manual', status: 'completed', task_id: 'task-1', run_id: 'run-1', outcome: {}, claimed_at: null, started_at: null, completed_at: null, created_at: '2026-08-02T01:00:00Z' }]);
    vi.mocked(api.listConversations).mockResolvedValue([{ id: 'task-1', title: '日报会话', title_source: 'auto', pinned_at: null, created_at: '', updated_at: '', last_run_status: 'completed', last_message_preview: '', has_active_share: false }]);
  });

  it('shows the global task, target conversation, and run history', async () => {
    const openConversation = vi.fn();
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={openConversation} /></I18nProvider>);

    expect(await screen.findByRole('heading', { name: '已安排任务' })).toBeInTheDocument();
    expect((await screen.findAllByText('日报会话')).length).toBeGreaterThan(0);
    expect(await screen.findByText('已完成')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看对话' }));
    expect(openConversation).toHaveBeenCalledWith('task-1', '日报会话');
  });

  it('pauses a task through the global API and refreshes the list', async () => {
    vi.mocked(api.setScheduledTaskEnabled).mockResolvedValue({ ...task, enabled: false, version: 2 });
    render(<I18nProvider><ScheduledTasksView onClose={() => undefined} onOpenConversation={() => undefined} /></I18nProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '暂停' }));
    await waitFor(() => expect(api.setScheduledTaskEnabled).toHaveBeenCalledWith(task, false));
  });
});
