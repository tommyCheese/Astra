import { describe, expect, it } from 'vitest';
import { createOptimisticProcessState, reconcileProcessSnapshot, reduceProcessEvent, reduceProcessEvents } from '../src/processStream';
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

  it('keeps provider model thinking separate and preserves streamed whitespace', () => {
    let state = createOptimisticProcessState('run-thinking');
    const base = { stream_id: 'stream-1', provider: 'qwen', operation: 'decision_with_answer', content_level: 'reasoning' };
    state = reduceProcessEvent(state, { id: 1, type: 'model_thinking.started', payload: base });
    state = reduceProcessEvent(state, { id: 2, type: 'model_thinking.delta', payload: { ...base, delta: '第一行\n' } });
    state = reduceProcessEvent(state, { id: 3, type: 'model_thinking.delta', payload: { ...base, delta: '  缩进内容' } });
    state = reduceProcessEvent(state, { id: 4, type: 'reasoning.summary.completed', payload: { turn_index: 1, summary: '公开摘要' } });
    state = reduceProcessEvent(state, { id: 5, type: 'model_thinking.completed', payload: { ...base, truncated: false } });

    expect(state.items.find((item) => item.id === 'model-thinking-stream-1')).toMatchObject({
      kind: 'model_thinking',
      title: '模型思考',
      detail: '第一行\n  缩进内容',
      status: 'completed',
      contentLevel: 'reasoning',
    });
    expect(state.items.find((item) => item.id === 'reasoning-1')?.detail).toBe('公开摘要');
  });

  it('coalesces one-frame model-thinking bursts while preserving cursor and duplicate semantics', () => {
    const base = { stream_id: 'burst-1', provider: 'deepseek', operation: 'synthesis', content_level: 'reasoning' };
    const state = reduceProcessEvents(createOptimisticProcessState('run-burst'), [
      { id: 1, run_sequence: 1, type: 'model_thinking.started', payload: base },
      { id: 2, run_sequence: 2, type: 'model_thinking.delta', payload: { ...base, delta: '逐' } },
      { id: 3, run_sequence: 3, type: 'model_thinking.delta', payload: { ...base, delta: '帧' } },
      { id: 3, run_sequence: 3, type: 'model_thinking.delta', payload: { ...base, delta: '重复' } },
      { id: 5, run_sequence: 5, type: 'model_thinking.delta', payload: { ...base, delta: '输出' } },
    ]);

    expect(state.items.find((item) => item.id === 'model-thinking-burst-1')?.detail).toBe('逐帧输出');
    expect(state.seenEventIds).toEqual([1, 2, 3, 5]);
    expect(state.runCursor).toBe(5);
    expect(state.cursorGap).toBe(true);
  });

  it('restores provider summaries, unavailable states, and truncation from snapshot events', () => {
    const state = reconcileProcessSnapshot(null, {
      id: 'run-thinking', task_id: 'task-1', status: 'completed', mode: 'agent', summary: 'done', result: null,
      steps: [], tool_calls: [], artifacts: [], memories: [], chat_messages: [], turns: [],
      events: [
        { id: 1, type: 'model_thinking.started', payload: { stream_id: 'summary-1', provider: 'anthropic', operation: 'contract', content_level: 'summary' }, created_at: 'now' },
        { id: 2, type: 'model_thinking.delta', payload: { stream_id: 'summary-1', provider: 'anthropic', operation: 'contract', content_level: 'summary', delta: '供应商摘要' }, created_at: 'now' },
        { id: 3, type: 'model_thinking.completed', payload: { stream_id: 'summary-1', provider: 'anthropic', operation: 'contract', content_level: 'summary', truncated: true }, created_at: 'now' },
        { id: 4, type: 'model_thinking.unavailable', payload: { stream_id: 'none-1', provider: 'openai', operation: 'synthesis', content_level: 'unavailable', reason: 'provider_did_not_return_visible_thinking' }, created_at: 'now' },
      ],
    } as RunView);

    expect(state.items.find((item) => item.id === 'model-thinking-summary-1')).toMatchObject({
      title: '供应商思考摘要', detail: '供应商摘要', truncated: true, contentLevel: 'summary',
    });
    expect(state.items.find((item) => item.id === 'model-thinking-none-1')).toMatchObject({
      title: '模型思考不可见', contentLevel: 'unavailable', unavailableReason: 'provider_did_not_return_visible_thinking',
    });
  });

  it('does not duplicate model-thinking deltas when a live projection reconciles the same snapshot', () => {
    const base = { stream_id: 'thinking-1', provider: 'deepseek', operation: 'decision', content_level: 'reasoning' };
    let live = createOptimisticProcessState('run-thinking', 'standard');
    live = reduceProcessEvent(live, { id: 1, type: 'model_thinking.started', payload: base });
    live = reduceProcessEvent(live, { id: 2, type: 'model_thinking.delta', payload: { ...base, delta: '只显示一次' } });

    const reconciled = reconcileProcessSnapshot(live, {
      id: 'run-thinking', task_id: 'task-1', status: 'executing', mode: 'agent', answer_mode: 'standard', summary: null, result: null,
      steps: [], tool_calls: [], artifacts: [], memories: [], chat_messages: [], turns: [],
      events: [
        { id: 1, type: 'model_thinking.started', payload: base, created_at: 'now' },
        { id: 2, type: 'model_thinking.delta', payload: { ...base, delta: '只显示一次' }, created_at: 'now' },
      ],
    } as RunView);

    expect(reconciled.items.find((item) => item.id === 'model-thinking-thinking-1')?.detail).toBe('只显示一次');
  });

  it('tracks tools and terminal status without exposing tool input', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search', input: { api_key: 'secret' } } });
    state = reduceProcessEvent(state, { id: 2, type: 'tool_call.completed', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search', status: 'succeeded' } });
    state = reduceProcessEvent(state, { id: 3, type: 'run.completed', payload: { status: 'completed' } });

    expect(state.active).toBe(false);
    expect(state.items.find((item) => item.id === 'tool-call-1')).toEqual(expect.objectContaining({ title: 'catalog_search', status: 'completed' }));
    expect(JSON.stringify(state)).not.toContain('secret');
  });

  it('marks active process rows as cancelled when the run is stopped', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search' } });
    state = reduceProcessEvent(state, { id: 2, type: 'run.cancelled', payload: { status: 'cancelled' } });

    expect(state.active).toBe(false);
    expect(state.items.filter((item) => item.status === 'cancelled')).toHaveLength(2);
  });

  it('keeps each reasoning and tool row inside its nearest selecting-action group', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
    state = reduceProcessEvent(state, { id: 2, type: 'reasoning.summary.completed', payload: { turn_index: 1, summary: '先搜索' } });
    state = reduceProcessEvent(state, { id: 3, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search' } });
    state = reduceProcessEvent(state, { id: 4, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 2 } });
    state = reduceProcessEvent(state, { id: 5, type: 'reasoning.summary.completed', payload: { turn_index: 2, summary: '再抓取' } });
    state = reduceProcessEvent(state, { id: 6, type: 'tool_call.started', payload: { tool_call_id: 'call-2', tool_name: 'catalog_read' } });

    expect(state.items.find((item) => item.id === 'reasoning-1')?.groupId).toBe('phase-selecting_action-1');
    expect(state.items.find((item) => item.id === 'tool-call-1')?.groupId).toBe('phase-selecting_action-1');
    expect(state.items.find((item) => item.id === 'reasoning-2')?.groupId).toBe('phase-selecting_action-2');
    expect(state.items.find((item) => item.id === 'tool-call-2')?.groupId).toBe('phase-selecting_action-2');
  });

  it('shows a running evaluation handoff after a tool completes and replaces it with the next phase', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, { id: 1, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
    state = reduceProcessEvent(state, { id: 2, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search' } });
    state = reduceProcessEvent(state, { id: 3, type: 'tool_call.completed', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search', status: 'succeeded' } });

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
    state = reduceProcessEvent(state, { id: 2, type: 'tool_call.started', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search' } });
    state = reduceProcessEvent(state, { id: 3, type: 'tool_call.completed', payload: { tool_call_id: 'call-1', tool_name: 'catalog_search', status: 'succeeded' } });
    state = reduceProcessEvent(state, { id: 4, type: 'run.completed', payload: { status: 'completed' } });

    expect(state.items.find((item) => item.id === 'phase-processing_result-call-1')).toBeUndefined();
    expect(state.active).toBe(false);
  });

  it('projects fast actions and approval waits without trusted decision groups', () => {
    let state = createOptimisticProcessState('run-fast', 'standard');
    state = reduceProcessEvent(state, { id: 1, type: 'fast.started', payload: { runtime: 'fast-v1' } });
    expect(state.items).toEqual([
      expect.objectContaining({ id: 'reasoning-0', title: '思考', status: 'running' }),
    ]);
    state = reduceProcessEvent(state, { id: 2, type: 'fast.action.decided', payload: { turn_index: 1, action: 'call_tool', tool_name: 'write_value' } });
    state = reduceProcessEvent(state, { id: 3, type: 'fast.approval.waiting', payload: { tool_call_id: 'call-fast', tool_name: 'write_value' } });

    expect(state.items.find((item) => item.id === 'fast-action-1')).toMatchObject({
      title: '选择工具',
      status: 'completed',
    });
    expect(state.items.find((item) => item.id === 'tool-call-fast')).toMatchObject({
      detail: '等待批准',
      status: 'running',
    });
    expect(state.items.some((item) => item.id.startsWith('phase-selecting_action-'))).toBe(false);
  });

  it('projects fast failures and cancellation without trusted placeholders', () => {
    let state = createOptimisticProcessState('run-fast', 'standard');
    state = reduceProcessEvent(state, { id: 1, type: 'fast.started', payload: { runtime: 'fast-v1' } });
    state = reduceProcessEvent(state, { id: 2, type: 'fast.tool.failed', payload: { turn_index: 1, tool_name: 'sandbox_failure', category: 'sandbox_execution_failed' } });
    state = reduceProcessEvent(state, { id: 3, type: 'fast.cancelled', payload: { status: 'cancelled' } });

    expect(state.active).toBe(false);
    expect(state.items.find((item) => item.id === 'fast-tool-error-1')).toMatchObject({
      status: 'failed',
      detail: 'sandbox_execution_failed',
    });
    expect(state.items.some((item) => ['reflection', 'verification'].includes(item.kind))).toBe(false);
    expect(state.items.some((item) => item.title.includes('计划'))).toBe(false);
  });

  it('rebuilds fast history from explicit runtime events only', () => {
    const state = reconcileProcessSnapshot(null, {
      id: 'run-fast', task_id: 'task-fast', status: 'completed', mode: 'agent', answer_mode: 'standard', runtime_kind: 'fast-v1', summary: 'done', result: null,
      steps: [], turns: [], tool_calls: [], artifacts: [], sandbox_jobs: [], memories: [], chat_messages: [],
      events: [
        { id: 1, type: 'fast.started', payload: { runtime: 'fast-v1' }, created_at: 'now' },
        { id: 2, type: 'fast.action.decided', payload: { turn_index: 1, action: 'answer' }, created_at: 'now' },
        { id: 3, type: 'fast.completed', payload: { status: 'completed' }, created_at: 'now' },
      ],
    } as RunView);

    expect(state.answerMode).toBe('standard');
    expect(state.active).toBe(false);
    expect(state.items.find((item) => item.id === 'fast-action-1')?.title).toBe('生成回答');
    expect(state.items.some((item) => item.detail === '模型驱动执行')).toBe(false);
    expect(state.items.some((item) => item.kind === 'verification' || item.kind === 'reflection')).toBe(false);
  });

  it('rebuilds decision groups from a terminal snapshot without live state', () => {
    const state = reconcileProcessSnapshot(null, {
      id: 'run-1', task_id: 'task-1', status: 'completed', mode: 'agent', answer_mode: 'trusted', runtime_kind: 'trusted-v1', summary: 'done', result: null,
      steps: [], artifacts: [], sandbox_jobs: [], events: [], memories: [], chat_messages: [],
      turns: [{
        id: 'turn-1', run_id: 'run-1', turn_index: 1, decision_type: 'call_tool', reasoning_summary: '先搜索',
        selected_tool: 'catalog_search', decision: {}, observation: null, reflection: null, tool_call_id: 'call-1',
        artifact_id: null, memory_reads: [], memory_writes: [], status: 'completed', created_at: 'now', updated_at: 'now',
      }],
      tool_calls: [{ id: 'call-1', tool_name: 'catalog_search', status: 'succeeded', input: {}, output: {} }],
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

  it('suppresses stale per-agent events and detects cursor gaps', () => {
    let state = createOptimisticProcessState('run-1');
    state = reduceProcessEvent(state, {
      id: 10,
      run_sequence: 1,
      agent_execution_id: 'child-1',
      agent_sequence: 1,
      type: 'tool_call.started',
      payload: { tool_call_id: 'call-1', tool_name: 'catalog_search' },
    });
    const unchanged = reduceProcessEvent(state, {
      id: 11,
      run_sequence: 2,
      agent_execution_id: 'child-1',
      agent_sequence: 1,
      type: 'tool_call.started',
      payload: { tool_call_id: 'stale', tool_name: 'must_not_render' },
    });
    expect(unchanged).toBe(state);

    state = reduceProcessEvent(state, {
      id: 13,
      run_sequence: 3,
      agent_execution_id: 'child-1',
      agent_sequence: 3,
      type: 'tool_call.completed',
      payload: { tool_call_id: 'call-1', tool_name: 'catalog_search', status: 'succeeded' },
    });
    expect(state.cursorGap).toBe(true);
    expect(state.agentCursors['child-1']).toBe(3);
  });

  it('clears a detected gap after authoritative snapshot reconciliation', () => {
    const gapped = {
      ...createOptimisticProcessState('run-1'),
      runCursor: 9,
      cursorGap: true,
    };
    const reconciled = reconcileProcessSnapshot(gapped, {
      id: 'run-1', task_id: 'task-1', status: 'executing', mode: 'agent', summary: null, result: null,
      steps: [], tool_calls: [], artifacts: [], events: [{
        id: 20, run_sequence: 1, type: 'reasoning.phase.started', payload: { phase: 'executing' }, created_at: 'now',
      }],
    } as RunView);
    expect(reconciled.cursorGap).toBe(false);
    expect(reconciled.runCursor).toBe(1);
  });
});
