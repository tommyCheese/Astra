import { describe, expect, it } from 'vitest';
import { buildPresentation, presentCommandMessages } from '../src/conversations';
import type { RunView } from '../src/types';

function waitingRun(overrides: Partial<RunView> = {}): RunView {
  return {
    id: 'run-waiting',
    task_id: 'task-waiting',
    status: 'waiting_user',
    mode: 'general-agent',
    runtime_kind: 'fast-v1',
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

  it('places the thinking process before a persisted clarification response', () => {
    const messages = buildPresentation(waitingRun({
      events: [
        { id: 1, type: 'reasoning.summary.completed', payload: { summary: '需要澄清用户意图' }, created_at: 'now' },
        { id: 2, type: 'run.waiting_user', payload: { request: '请告诉我你希望我完成的具体任务或问题。' }, created_at: 'now' },
      ],
      chat_messages: [
        { id: 'user-1', role: 'user', content: '！', status: 'completed', metadata: {} },
        { id: 'assistant-1', role: 'assistant', content: '请告诉我你希望我完成的具体任务或问题。', status: 'waiting_user', metadata: {} },
      ],
    }));

    expect(messages.map((message) => message.metadata.presentation ?? message.role)).toEqual([
      'user',
      'process',
      'assistant',
    ]);
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

  it('keeps the completed thinking entry for a fast run', () => {
    const messages = buildPresentation(waitingRun({
      status: 'completed',
      runtime_kind: 'fast-v1',
      result: {
        summary: '快速回答',
        findings: [],
        claims: [],
        citations: [],
        sources: [],
        failed_sources: [],
        source_quality: [],
        conflicts: [],
        caveats: [],
        verification_notes: [],
        memory_references: [],
        audit_refs: {
          evidence_record_count: 0,
          agent_turn_count: 0,
          referenced_artifact_ids: [],
        },
        verification_report: null,
        completion_decision: null,
        error: null,
      },
      waiting_state: null,
      events: [
        { id: 1, type: 'fast.started', payload: { runtime: 'fast-v1' }, created_at: 'now' },
        { id: 2, type: 'fast.action.decided', payload: { action: 'answer', turn_index: 1 }, created_at: 'now' },
        { id: 3, type: 'fast.completed', payload: { status: 'completed' }, created_at: 'now' },
      ],
    }));

    expect(messages.some((message) => message.metadata.presentation === 'process')).toBe(true);
    expect(messages.some((message) => message.metadata.presentation === 'answer')).toBe(true);
  });

  it('does not invent an active process entry for a cancelled run without process events', () => {
    const messages = buildPresentation(waitingRun({
      status: 'cancelled',
      waiting_state: null,
    }));

    expect(messages.some((message) => message.metadata.presentation === 'process')).toBe(false);
  });
});
