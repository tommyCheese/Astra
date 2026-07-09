import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';

vi.mock('../src/api', () => ({
  createRun: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'created' })),
  getRun: vi.fn(async () => ({
    id: 'run-1',
    task_id: 'task-1',
    status: 'completed',
    mode: 'web_data_query',
    summary: '完成',
    result: {
      summary: '已完成查询',
      findings: [{ text: '发现一条证据', source_urls: ['https://example.com'] }],
      sources: [{ url: 'https://example.com', title: 'Example' }],
      caveats: [],
      verification_notes: ['验证通过'],
    },
    steps: [{ id: 's1', index: 1, title: '搜索', intent: '调用 web_search', status: 'completed' }],
    tool_calls: [{ id: 't1', tool_name: 'web_search', status: 'succeeded', input: {}, output: {} }],
    artifacts: [],
    events: [{ id: 1, type: 'run.created', payload: { status: 'created' }, created_at: 'now' }],
  })),
}));

class MockEventSource {
  onmessage: ((message: MessageEvent) => void) | null = null;
  addEventListener = vi.fn();
  close = vi.fn();
}

Object.defineProperty(window, 'EventSource', {
  value: MockEventSource,
});

describe('App', () => {
  afterEach(() => {
    cleanup();
  });

  it('submits a goal and renders the result', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: 'Run' }));

    expect(await screen.findByText('已完成查询')).toBeInTheDocument();
    expect(screen.getByText('发现一条证据')).toBeInTheDocument();
    expect(screen.getByText('web_search')).toBeInTheDocument();
  });

  it('shows validation error for empty goal', async () => {
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.clear(textbox);
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));

    expect(screen.getByText('请输入任务目标')).toBeInTheDocument();
  });
});
