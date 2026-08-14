import { EventType } from '@ag-ui/core';
import { describe, expect, it, vi } from 'vitest';
import { AgUiFrameBatcher } from '../src/agui/batching';
import {
  initialAgUiProjectionStore,
  markAgUiDisconnected,
  reduceAgUiEvent,
  withAgUiCapabilities,
  type AgUiProjectedEvent,
} from '../src/agui/store';

function reduce(events: AgUiProjectedEvent[]) {
  return events.reduce(reduceAgUiEvent, initialAgUiProjectionStore());
}

describe('AG-UI projection store', () => {
  it('makes the first text delta renderable before message or run completion', () => {
    const store = reduce([
      { type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-1' },
      { type: EventType.TEXT_MESSAGE_START, messageId: 'message-1', role: 'assistant' },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'message-1', delta: '首个分片' },
    ]);

    expect(store.connection).toBe('streaming');
    expect(store.messages['message-1']).toEqual({
      id: 'message-1', role: 'assistant', content: '首个分片', complete: false,
    });
  });

  it('accepts a first content event defensively and appends later deltas once', () => {
    const store = reduce([
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'message-1', delta: 'A' },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'message-1', delta: 'B' },
      { type: EventType.TEXT_MESSAGE_END, messageId: 'message-1' },
    ]);
    expect(store.messageOrder).toEqual(['message-1']);
    expect(store.messages['message-1'].content).toBe('AB');
    expect(store.messages['message-1'].complete).toBe(true);
  });

  it('renders the first activity snapshot immediately', () => {
    const store = reduce([{
      type: EventType.ACTIVITY_SNAPSHOT,
      messageId: 'plan-1',
      activityType: 'astra.plan',
      content: { schemaVersion: 1, fallbackText: '执行计划' },
      replace: false,
    }]);
    expect(store.activities['plan-1'].content.fallbackText).toBe('执行计划');
  });

  it('converges to a final correction snapshot without duplicate text', () => {
    const store = reduce([
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'message-1', delta: 'draft' },
      { type: EventType.MESSAGES_SNAPSHOT, messages: [{ id: 'message-1', role: 'assistant', content: 'final' }] },
      { type: EventType.RUN_FINISHED, threadId: 'thread-1', runId: 'run-1', outcome: { type: 'success' } },
    ]);
    expect(store.messages['message-1'].content).toBe('final');
    expect(store.messageOrder).toEqual(['message-1']);
    expect(store.connection).toBe('finished');
  });

  it('applies compatible activity patches and isolates revision gaps until replacement', () => {
    const snapshot = reduce([{
      type: EventType.ACTIVITY_SNAPSHOT,
      messageId: 'plan-1',
      activityType: 'astra.plan',
      content: { schemaVersion: 1, revision: 1, status: 'running' },
      replace: false,
    }]);
    const patched = reduceAgUiEvent(snapshot, {
      type: EventType.ACTIVITY_DELTA,
      messageId: 'plan-1',
      activityType: 'astra.plan',
      patch: [{ op: 'replace', path: '/status', value: 'completed' }],
      metadata: { baseRevision: 1, revision: 2 },
    } as AgUiProjectedEvent);
    expect(patched.activities['plan-1'].content.status).toBe('completed');
    expect(patched.activities['plan-1'].revision).toBe(2);

    const gap = reduceAgUiEvent(patched, {
      type: EventType.ACTIVITY_DELTA,
      messageId: 'plan-1',
      activityType: 'astra.plan',
      patch: [{ op: 'replace', path: '/status', value: 'failed' }],
      metadata: { baseRevision: 4, revision: 5 },
    } as AgUiProjectedEvent);
    expect(gap.activities['plan-1'].content.status).toBe('completed');
    expect(gap.activities['plan-1'].error).toContain('revision');

    const recovered = reduceAgUiEvent(gap, {
      type: EventType.ACTIVITY_SNAPSHOT,
      messageId: 'plan-1',
      activityType: 'astra.plan',
      content: { schemaVersion: 1, revision: 6, status: 'failed' },
      replace: true,
    });
    expect(recovered.activities['plan-1'].error).toBeUndefined();
    expect(recovered.activities['plan-1'].content.status).toBe('failed');
  });

  it('keeps partial text while disconnected and reconciles from snapshots', () => {
    const partial = reduce([
      { type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-1' },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'message-1', delta: 'partial' },
    ]);
    const disconnected = markAgUiDisconnected(partial);
    expect(disconnected.connection).toBe('reconnecting');
    expect(disconnected.messages['message-1'].content).toBe('partial');
    const recovered = reduceAgUiEvent(disconnected, {
      type: EventType.MESSAGES_SNAPSHOT,
      messages: [{ id: 'message-1', role: 'assistant', content: 'final' }],
    });
    expect(recovered.messages['message-1'].content).toBe('final');
  });

  it('normalizes reasoning, tools, interrupts and cancellation', () => {
    const store = reduce([
      { type: EventType.REASONING_START, messageId: 'reason-1' },
      { type: EventType.REASONING_MESSAGE_START, messageId: 'reason-1', role: 'reasoning' },
      { type: EventType.REASONING_MESSAGE_CONTENT, messageId: 'reason-1', delta: 'check' },
      { type: EventType.REASONING_END, messageId: 'reason-1' },
      { type: EventType.TOOL_CALL_START, toolCallId: 'tool-1', toolCallName: 'search' },
      { type: EventType.TOOL_CALL_ARGS, toolCallId: 'tool-1', delta: '{}' },
      { type: EventType.TOOL_CALL_RESULT, messageId: 'result-1', toolCallId: 'tool-1', content: 'ok' },
      {
        type: EventType.RUN_FINISHED,
        threadId: 'thread-1',
        runId: 'run-1',
        outcome: { type: 'interrupt', interrupts: [{ id: 'interrupt-1', reason: 'confirmation' }] },
      },
    ]);
    expect(store.reasoning['reason-1']).toMatchObject({ content: 'check', complete: true });
    expect(store.tools['tool-1']).toMatchObject({ name: 'search', arguments: '{}', result: 'ok' });
    expect(store.pendingInterrupts[0].id).toBe('interrupt-1');

    const cancelled = reduce([{
      type: EventType.RUN_FINISHED,
      threadId: 'thread-1',
      runId: 'run-1',
      result: { status: 'cancelled' },
      outcome: { type: 'success' },
    }]);
    expect(cancelled.connection).toBe('cancelled');
  });

  it('commits first content and terminal events immediately while batching ordinary deltas', () => {
    const commits: string[][] = [];
    const callbacks = new Map<number, FrameRequestCallback>();
    let nextFrame = 1;
    const request = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      const id = nextFrame++;
      callbacks.set(id, callback);
      return id;
    });
    const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => callbacks.delete(id));
    const batcher = new AgUiFrameBatcher((events) => commits.push(events.map((event) => event.type)));
    batcher.push({ type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'm1', delta: 'first' });
    batcher.push({ type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'm1', delta: 'second' });
    batcher.push({ type: EventType.REASONING_MESSAGE_CONTENT, messageId: 'r1', delta: 'reason' });
    expect(commits).toEqual([[EventType.TEXT_MESSAGE_CONTENT]]);
    for (const callback of callbacks.values()) callback(0);
    expect(commits[1]).toEqual([EventType.TEXT_MESSAGE_CONTENT, EventType.REASONING_MESSAGE_CONTENT]);
    batcher.push({ type: EventType.RUN_ERROR, message: 'failed' });
    expect(commits[commits.length - 1]).toEqual([EventType.RUN_ERROR]);
    request.mockRestore();
    cancel.mockRestore();
  });

  it('stores capability discovery independently from stream events', () => {
    const store = withAgUiCapabilities(initialAgUiProjectionStore(), {
      transport: { streaming: true },
      tools: { supported: true, clientProvided: false },
    });
    expect(store.capabilities?.transport?.streaming).toBe(true);
    expect(store.capabilities?.tools?.clientProvided).toBe(false);
  });
});
