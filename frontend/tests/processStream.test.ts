import { describe, expect, it } from 'vitest';
import { createOptimisticProcessState, reconcileProcessSnapshot, reduceProcessEvent } from '../src/processStream';
import type { RunView } from '../src/types';

describe('process stream reducer', () => {
  it('shows only real reasoning content in standard mode without plan phases', () => {
    let state = createOptimisticProcessState('run-quick', 'standard');
    state = reduceProcessEvent(state, {
      id: 1,
      type: 'reasoning.phase.started',
      payload: { phase: 'executing' },
    });
    state = reduceProcessEvent(state, {
      id: 2,
      type: 'reasoning.phase.started',
      payload: { phase: 'selecting_action', turn_index: 1 },
    });
    state = reduceProcessEvent(state, {
      id: 3,
      type: 'reasoning.summary.completed',
      payload: { turn_index: 1, summary: '直接判断用户请求能否完成' },
    });

    expect(state.items.map((item) => item.title)).not.toContain('正在理解任务并制定计划');
    expect(state.items.map((item) => item.title)).not.toContain('正在执行计划');
    expect(state.items.map((item) => item.title)).not.toContain('正在分析下一步');
    expect(state.items.find((item) => item.id === 'reasoning-1')).toMatchObject({
      title: '思考',
      detail: '直接判断用户请求能否完成',
      groupId: undefined,
    });
  });

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

  it('marks active process rows as cancelled when the run is stopped', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'web_search' } });
    state = reduceProcessEvent(state, { id: 2, type: 'run.cancelled', payload: { status: 'cancelled' } });

    expect(state.active).toBe(false);
    expect(state.items.filter((item) => item.status === 'cancelled')).toHaveLength(2);
  });

  it('keeps each reasoning and tool row inside its nearest selecting-action group', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
    state = reduceProcessEvent(state, { id: 2, type: 'reasoning.summary.completed', payload: { turn_index: 1, summary: '先搜索' } });
    state = reduceProcessEvent(state, { id: 3, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'web_search' } });
    state = reduceProcessEvent(state, { id: 4, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 2 } });
    state = reduceProcessEvent(state, { id: 5, type: 'reasoning.summary.completed', payload: { turn_index: 2, summary: '再抓取' } });
    state = reduceProcessEvent(state, { id: 6, type: 'tool_call.started', payload: { tool_call_id: 'call-2', tool_name: 'web_fetch' } });

    expect(state.items.find((item) => item.id === 'reasoning-1')?.groupId).toBe('phase-selecting_action-1');
    expect(state.items.find((item) => item.id === 'tool-call-1')?.groupId).toBe('phase-selecting_action-1');
    expect(state.items.find((item) => item.id === 'reasoning-2')?.groupId).toBe('phase-selecting_action-2');
    expect(state.items.find((item) => item.id === 'tool-call-2')?.groupId).toBe('phase-selecting_action-2');
  });

  it('shows a running evaluation handoff after a tool completes and replaces it with the next phase', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
    state = reduceProcessEvent(state, { id: 2, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'web_search' } });
    state = reduceProcessEvent(state, { id: 3, type: 'tool_call.completed', payload: { tool_call_id: 'call-1', tool_name: 'web_search', status: 'succeeded' } });

    expect(state.items.find((item) => item.id === 'phase-selecting_action-1')?.status).toBe('completed');
    expect(state.items.find((item) => item.id === 'phase-processing_result-call-1')).toMatchObject({
      title: '正在评估执行结果',
      status: 'running',
      groupId: 'phase-selecting_action-1',
    });
    expect(state.items.filter((item) => item.status === 'running')).toHaveLength(1);

    state = reduceProcessEvent(state, { id: 4, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 2 } });
    expect(state.items.find((item) => item.id === 'phase-processing_result-call-1')).toBeUndefined();
    expect(state.items.find((item) => item.id === 'phase-selecting_action-2')?.status).toBe('running');
    expect(state.items.filter((item) => item.status === 'running')).toHaveLength(1);
  });

  it('does not retain the evaluation handoff after a terminal event', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
    state = reduceProcessEvent(state, { id: 2, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'web_search' } });
    state = reduceProcessEvent(state, { id: 3, type: 'tool_call.completed', payload: { tool_call_id: 'call-1', tool_name: 'web_search', status: 'succeeded' } });
    state = reduceProcessEvent(state, { id: 4, type: 'run.completed', payload: { status: 'completed' } });

    expect(state.items.find((item) => item.id === 'phase-processing_result-call-1')).toBeUndefined();
    expect(state.active).toBe(false);
  });

  it('rebuilds decision groups from a terminal snapshot without live state', () => {
    const state = reconcileProcessSnapshot(null, {
      id: 'run-1', task_id: 'task-1', status: 'completed', mode: 'agent', summary: 'done', result: null,
      steps: [], artifacts: [], sandbox_jobs: [], events: [], memories: [], chat_messages: [],
      turns: [{
        id: 'turn-1', run_id: 'run-1', turn_index: 1, decision_type: 'call_tool', reasoning_summary: '先搜索',
        selected_tool: 'web_search', decision: {}, observation: null, reflection: null, tool_call_id: 'call-1',
        artifact_id: null, memory_reads: [], memory_writes: [], status: 'completed', created_at: 'now', updated_at: 'now',
      }],
      tool_calls: [{ id: 'call-1', tool_name: 'web_search', status: 'succeeded', input: {}, output: {} }],
    } as RunView);

    expect(state.items.find((item) => item.id === 'phase-selecting_action-1')).toBeDefined();
    expect(state.items.find((item) => item.id === 'reasoning-1')?.groupId).toBe('phase-selecting_action-1');
    expect(state.items.find((item) => item.id === 'tool-call-1')?.groupId).toBe('phase-selecting_action-1');
  });

  it('restores a missing group anchor when replay starts from a child event', () => {
    const state = reduceProcessEvent(createOptimisticProcessState('run-1'), {
      id: 8,
      type: 'reasoning.summary.completed',
      payload: { turn_index: 3, summary: '从断点恢复' },
    });

    expect(state.items.find((item) => item.id === 'phase-selecting_action-3')).toBeDefined();
    expect(state.items.find((item) => item.id === 'reasoning-3')?.groupId).toBe('phase-selecting_action-3');
  });
});
