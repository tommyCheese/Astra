import { describe, expect, it } from 'vitest';
import { buildPresentation, presentCommandMessages } from '../src/conversations';
import type { RunView } from '../src/types';

function waitingRun(overrides: Partial<RunView> = {}): RunView {
  return {
    id: 'run-waiting',
    task_id: 'task-waiting',
    status: 'waiting_user',
    mode: 'general-agent',
    answer_mode: 'standard',
    result: null,
    steps: [],
    tool_calls: [],
    artifacts: [],
    events: [],
    chat_messages: [{ id: 'user-1', role: 'user', content: '！', status: 'completed', metadata: {} }],
    waiting_state: {
      paused_node: 'select_action',
      continuation_token: 'continue-1',
      request: '请告诉我你希望我完成的具体任务或问题。',
    },
    ...overrides,
  };
}

describe('conversation presentation', () => {
  it('shows a regular clarification request while a run waits for user input', () => {
    const messages = buildPresentation(waitingRun());

    expect(messages).toEqual(expect.arrayContaining([
      expect.objectContaining({
        role: 'assistant',
        status: 'waiting_user',
        content: '请告诉我你希望我完成的具体任务或问题。',
      }),
    ]));
  });

  it('does not duplicate waiting text when a dedicated approval UI is present', () => {
    const messages = buildPresentation(waitingRun({
      pending_approval: {
        id: 'approval-1',
        tool_call_id: 'call-1',
        tool_name: 'bash_execute',
        preview: 'pytest',
        permission: 'command_execute',
        impact: 'external_side_effect',
        decisions: ['approve_once', 'reject'],
        created_at: 'now',
      },
    }));

    expect(messages.some((message) => message.content.includes('请告诉我'))).toBe(false);
  });

  it('restores both sides of a persisted context command exchange', () => {
    const messages = presentCommandMessages({
      id: 'command-compact',
      command: '/compact',
      content: '/compact',
      arguments: '',
      assistant_content: '上下文压缩完成。',
      after_run_count: 1,
      created_at: '2026-08-06T00:00:00Z',
    });

    expect(messages).toEqual([
      expect.objectContaining({ role: 'user', content: '/compact' }),
      expect.objectContaining({ role: 'assistant', content: '上下文压缩完成。', metadata: expect.objectContaining({ presentation: 'command-result' }) }),
    ]);
  });
});
