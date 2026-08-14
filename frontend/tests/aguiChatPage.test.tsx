import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { EventType, type AgentCapabilities, type BaseEvent, type RunAgentInput } from '@ag-ui/core';
import { afterEach, describe, expect, it } from 'vitest';
import { AgUiChatSurface } from '../src/agui/AgUiChatPage';
import type { AstraAgentStream, AstraAgentTransport, AstraStreamCallbacks } from '../src/agui/transport';
import type { ConversationSummary } from '../src/types';

const thread: ConversationSummary = {
  id: 'thread-1', title: 'AG-UI 测试', title_source: 'user', pinned_at: null,
  created_at: '2026-08-12T00:00:00Z', updated_at: '2026-08-12T00:00:00Z',
  last_run_status: null, last_message_preview: '', has_active_share: false,
};

afterEach(cleanup);

class FakeTransport implements AstraAgentTransport {
  callbacks: AstraStreamCallbacks | null = null;
  inputs: RunAgentInput[] = [];
  resumes: RunAgentInput[] = [];
  cancelled: string[] = [];
  closed = 0;

  private connect(input: RunAgentInput, callbacks: AstraStreamCallbacks): AstraAgentStream {
    this.inputs.push(input);
    this.callbacks = callbacks;
    return { close: () => { this.closed += 1; } };
  }

  start(input: RunAgentInput, callbacks: AstraStreamCallbacks) { return this.connect(input, callbacks); }
  resume(input: RunAgentInput, callbacks: AstraStreamCallbacks) {
    this.resumes.push(input);
    return this.connect(input, callbacks);
  }
  async cancel(runId: string) { this.cancelled.push(runId); }
  async getCapabilities() {
    return { transport: { streaming: true }, custom: { astra: { activities: true } } } as AgentCapabilities;
  }
  emit(event: BaseEvent) { this.callbacks?.onEvent(event); }
}

describe('AG-UI first-party chat', () => {
  it('renders progressive answers, plan/Subagent activities and unknown fallback', async () => {
    const transport = new FakeTransport();
    render(<AgUiChatSurface thread={thread} transport={transport} />);
    fireEvent.change(screen.getByLabelText('发送消息'), { target: { value: '开始任务' } });
    fireEvent.keyDown(screen.getByLabelText('发送消息'), { key: 'Enter' });
    expect(transport.inputs[0]).toMatchObject({
      threadId: 'thread-1', tools: [], forwardedProps: { astra: { planExecution: 'auto' } },
    });
    expect(screen.getByText('开始任务')).toBeVisible();

    act(() => {
      transport.emit({ type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-1' });
      transport.emit({ type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'answer-1', delta: '首个分片' });
    });
    expect(screen.getByText('首个分片')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('正在生成');

    act(() => transport.emit({
      type: EventType.ACTIVITY_SNAPSHOT, messageId: 'plan-1', activityType: 'astra.plan',
      content: { schemaVersion: 1, revision: 1, fallbackText: '计划执行中' }, replace: false,
    }));
    expect(screen.getByRole('region', { name: '执行计划' })).toHaveTextContent('计划执行中');

    act(() => transport.emit({
      type: EventType.ACTIVITY_SNAPSHOT, messageId: 'agents-1', activityType: 'astra.agent_tree',
      content: { schemaVersion: 1, revision: 1, fallbackText: '2 个 Subagent 工作中' }, replace: false,
    }));
    expect(screen.getByRole('region', { name: 'Agent 协作' })).toHaveTextContent('2 个 Subagent');

    act(() => transport.emit({
      type: EventType.ACTIVITY_SNAPSHOT, messageId: 'unknown-1', activityType: 'vendor.future',
      content: { schemaVersion: 9, revision: 1, fallbackText: '仍可阅读' }, replace: false,
    }));
    expect(screen.getByRole('region', { name: 'Agent 活动' })).toHaveTextContent('仍可阅读');
  });

  it('supports approval/resume, keyboard input, explicit cancellation and disconnect', async () => {
    const transport = new FakeTransport();
    render(<AgUiChatSurface thread={thread} transport={transport} />);
    fireEvent.change(screen.getByLabelText('发送消息'), { target: { value: '执行操作' } });
    fireEvent.keyDown(screen.getByLabelText('发送消息'), { key: 'Enter' });
    act(() => {
      transport.emit({ type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-1' });
      transport.emit({
        type: EventType.RUN_FINISHED, threadId: 'thread-1', runId: 'run-1',
        outcome: { type: 'interrupt', interrupts: [{
          id: 'approval-1', reason: 'tool_call', message: '允许执行？',
          responseSchema: { type: 'object', properties: { decision: { type: 'string', enum: ['approve_once', 'reject'] } } },
        }] },
      });
    });
    expect(screen.getByLabelText('发送消息')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('审批决定'), { target: { value: 'approve_once' } });
    fireEvent.click(screen.getByRole('button', { name: '继续' }));
    expect(transport.resumes[0].resume).toEqual([{ interruptId: 'approval-1', status: 'resolved', payload: { decision: 'approve_once' } }]);
    expect(transport.resumes[0].parentRunId).toBe('run-1');

    act(() => transport.emit({ type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-2' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '仅断开连接' })).toBeVisible());
    fireEvent.click(screen.getByRole('button', { name: '仅断开连接' }));
    expect(transport.closed).toBeGreaterThan(0);
    expect(transport.cancelled).toEqual([]);

    act(() => transport.emit({ type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-3' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '停止运行' })).toBeVisible());
    fireEvent.click(screen.getByRole('button', { name: '停止运行' }));
    await waitFor(() => expect(transport.cancelled).toEqual(['run-3']));
  });

  it('keeps the composer operable at a narrow viewport and after a terminal outcome', () => {
    const transport = new FakeTransport();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 360 });
    render(<AgUiChatSurface thread={thread} transport={transport} />);
    const input = screen.getByLabelText('发送消息');
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: '手机端' } });
    fireEvent.submit(screen.getByRole('form', { name: '消息编辑器' }));
    act(() => transport.emit({ type: EventType.RUN_FINISHED, threadId: 'thread-1', runId: 'run-1', outcome: { type: 'success' } }));
    expect(input).toBeEnabled();
  });
});
