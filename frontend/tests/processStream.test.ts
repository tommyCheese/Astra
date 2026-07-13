import { describe, expect, it } from 'vitest';
import { createOptimisticProcessState, reduceProcessEvent } from '../src/processStream';

describe('process stream reducer', () => {
  it('merges reasoning deltas and ignores duplicate event ids', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
    state = reduceProcessEvent(state, { id: 2, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '先检索' } });
    state = reduceProcessEvent(state, { id: 3, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '可靠来源' } });
    state = reduceProcessEvent(state, { id: 3, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '不应重复' } });
    state = reduceProcessEvent(state, { id: 4, type: 'reasoning.summary.completed', payload: { turn_index: 1, summary: '先检索可靠来源' } });

    expect(state.items.find((item) => item.id === 'reasoning-1')).toMatchObject({
      detail: '先检索可靠来源',
      status: 'completed',
    });
    expect(state.seenEventIds).toEqual([1, 2, 3, 4]);
  });

  it('tracks tools and terminal status without exposing tool input', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'web_search', input: { api_key: 'secret' } } });
    state = reduceProcessEvent(state, { id: 2, type: 'tool_call.completed', payload: { tool_call_id: 'call-1', tool_name: 'web_search', status: 'succeeded' } });
    state = reduceProcessEvent(state, { id: 3, type: 'run.completed', payload: { status: 'completed' } });

    expect(state.active).toBe(false);
    expect(state.items.find((item) => item.id === 'tool-call-1')).toEqual(expect.objectContaining({ title: 'web_search', status: 'completed' }));
    expect(JSON.stringify(state)).not.toContain('secret');
  });
});
