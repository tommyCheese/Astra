import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { buildRuntime, cancelRuntimeBuild, createRun, getRun, getRuntimeProfile, listRuns, streamRunEvents, updateToolSettings, type RunStreamEvent } from '../src/api';

vi.mock('../src/api', () => ({
  getToolSettings: vi.fn(async () => ({ tools: [
    { name: 'web_search', label: 'Web Search', description: '搜索公开网页并生成候选来源', enabled: true, available: true },
    { name: 'web_fetch', label: 'Web Fetch', description: '自适应提取页面主要内容', enabled: true, available: true },
    { name: 'chart_render', label: 'Chart Render', description: '在隔离的 Docker 运行时中生成图表', enabled: true, available: false, unavailable_reason: '需要先启用 Docker 沙箱。' },
  ] })),
  updateToolSettings: vi.fn(async (tools) => ({ tools })),
  getRuntimeProfile: vi.fn(async () => ({ dependencies: [], core_dependencies: [{ name: 'numpy', version: '2.2.6' }, { name: 'matplotlib', version: '3.10.3' }], active_image: 'astra-data-viz:0.1.0', dependency_digest: 'base', build: null })),
  buildRuntime: vi.fn(async () => ({ dependencies: [{ name: 'polars', version: '' }], core_dependencies: [], active_image: 'astra-data-viz:0.1.0', dependency_digest: 'base', build: { id: 'build-1', status: 'queued', phase: '等待构建', progress: 0, log: '等待构建' } })),
  cancelRuntimeBuild: vi.fn(async () => ({ dependencies: [{ name: 'polars', version: '' }], core_dependencies: [], active_image: 'astra-data-viz:0.1.0', dependency_digest: 'base', build: { id: 'build-1', status: 'cancelled', phase: '已取消', progress: 12, log: '构建已由用户取消' } })),
  streamRunEvents: vi.fn(() => () => undefined),
  createRun: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'created' })),
  listRuns: vi.fn(async () => []),
  resumeRun: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'executing' })),
  getUsageSummary: vi.fn(async () => ({
    scope: 'task', from: null, to: null,
    overview: { model_invocations: 3, successful_invocations: 3, failed_invocations: 0, interrupted_invocations: 0, agent_turns: 2, tool_calls: 2, successful_tool_calls: 2, failed_tool_calls: 0, tool_success_rate: 1, memories: 1, sandbox_jobs: 0, artifacts: 0, artifact_bytes: 0 },
    tokens: { input: 100, cached_input: 20, output: 50, reasoning: 10, total: 150 },
    coverage: { reported_invocations: 3, total_invocations: 3, ratio: 1, complete: true },
    trend: [], models: [{ provider: 'openai', model: 'gpt-5', invocations: 3, reported_invocations: 3, tokens: { input: 100, cached_input: 20, output: 50, reasoning: 10, total: 150 } }], tools: [],
  })),
  getRun: vi.fn(async () => ({
    id: 'run-1',
    task_id: 'task-1',
    status: 'completed',
    mode: 'web_agent',
    summary: '完成',
    result: {
      summary: '已完成查询',
      findings: [{ text: '**发现一条证据**', source_urls: ['https://example.com'], artifact_ids: [] }],
      sources: [{ url: '示例网站：https://example.com/docs', title: 'Example' }],
      source_quality: [
        {
          url: '示例网站：https://example.com/docs',
          quality_score: 0.92,
          extraction_strategy: 'readability',
          warnings: ['正文与查询词重叠较少'],
        },
      ],
      failed_sources: [{ url: 'https://bad.example', category: 'fetch_failed', retryable: false, details: {} }],
      conflicts: [],
      caveats: ['部分来源抓取失败'],
      verification_notes: ['验证通过'],
      memory_references: [],
      audit_refs: { agent_turn_count: 2, referenced_artifact_ids: [] },
      verification_report: {
        status: 'completed',
        source_count: 1,
        caveat_count: 1,
        low_quality_sources: [],
        failed_sources: [],
        memory_references: [],
        notes: ['至少一个抓取来源支撑了最终答案。'],
      },
    },
    steps: [
      { id: 's1', index: 1, title: '搜索候选来源', intent: '调用 web_search', status: 'completed' },
      { id: 's2', index: 2, title: '筛选和去重来源', intent: '筛选', status: 'completed' },
      { id: 's3', index: 3, title: '抓取来源内容', intent: '调用 web_fetch', status: 'completed' },
    ],
    tool_calls: [
      {
        id: 't1',
        tool_name: 'web_search',
        status: 'succeeded',
        input: {},
        output: { candidate_count: 2 },
      },
      {
        id: 't2',
        tool_name: 'web_fetch',
        status: 'succeeded',
        input: {},
        output: { extraction_strategy: 'readability', quality_score: 0.92 },
      },
    ],
    turns: [
      {
        id: 'turn-1',
        run_id: 'run-1',
        turn_index: 1,
        decision_type: 'call_tool',
        reasoning_summary: '先搜索候选来源',
        selected_tool: 'web_search',
        decision: {},
        observation: { kind: 'tool_result', status: 'succeeded' },
        reflection: null,
        tool_call_id: 't1',
        artifact_id: null,
        memory_reads: [],
        memory_writes: [],
        status: 'completed',
        created_at: 'now',
        updated_at: 'now',
      },
      {
        id: 'turn-2',
        run_id: 'run-1',
        turn_index: 2,
        decision_type: 'finalize',
        reasoning_summary: '基于证据生成最终回复',
        selected_tool: null,
        decision: {},
        observation: { kind: 'final_answer', status: 'completed' },
        reflection: null,
        tool_call_id: null,
        artifact_id: null,
        memory_reads: [],
        memory_writes: [{ id: 'm1' }],
        status: 'completed',
        created_at: 'now',
        updated_at: 'now',
      },
    ],
    memories: [
      {
        id: 'm1',
        run_id: 'run-1',
        scope: 'run',
        kind: 'source_summary',
        content: '记录本次来源摘要',
        structured_data: {},
        provenance: { run_id: 'run-1' },
        confidence: 0.8,
        created_at: 'now',
        updated_at: 'now',
      },
    ],
    chat_messages: [
      { id: 'u1', role: 'user', content: '查询 Astra', status: 'completed', metadata: {} },
      {
        id: 'turn-1',
        role: 'tool',
        content: '先搜索候选来源',
        status: 'completed',
        metadata: { turn_index: 1 },
      },
      {
        id: 'turn-2',
        role: 'assistant',
        content: '已完成查询',
        status: 'completed',
        metadata: { turn_index: 2, decision_type: 'finalize' },
      },
    ],
    artifacts: [{ id: 'a-chart', type: 'sandbox_output', metadata: { filename: 'chart.png' }, created_at: 'now', mime_type: 'image/png', size_bytes: 128, checksum: 'sha256', security_status: 'verified', content_url: '/api/artifacts/a-chart/content' }, { id: 'a-html', type: 'sandbox_output', metadata: { filename: 'chart.html' }, created_at: 'now', mime_type: 'text/html', size_bytes: 256, checksum: 'sha256-html', security_status: 'verified', content_url: '/api/artifacts/a-html/content' }],
    events: [{ id: 1, type: 'run.created', payload: { status: 'created' }, created_at: 'now' }],
    reasoning_policy: { effective: { reasoning_effort: 'balanced', planning_strategy: 'adaptive', execution_mode: 'request_approval' }, adjustments: [] },
    task_contract: { success_criteria: [{ id: 'criterion-result', description: '完成查询', status: 'satisfied' }] },
    plan_graph: { version: 1 },
    state_version: 2,
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
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
        key: (index: number) => [...values.keys()][index] ?? null,
        get length() { return values.size; },
      },
    });
  });

  afterEach(() => {
    cleanup();
    globalThis.localStorage?.clear();
  });

  it('submits a goal and renders the result', async () => {
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '查询 Astra');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(await screen.findByText('已完成查询')).toBeInTheDocument();
    const evidence = screen.getByText('数据与证据 · 1').closest('details');
    expect(evidence).not.toHaveAttribute('open');
    expect(screen.getByText('发现一条证据')).toBeInTheDocument();
    expect(screen.getByText('发现一条证据').tagName).toBe('STRONG');
    expect(screen.getByText(/92%/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Example/ })).toHaveAttribute('href', 'https://example.com/docs');
    expect(screen.getByRole('link', { name: '关联来源' })).toHaveAttribute('href', 'https://example.com');
    expect(screen.queryByText('审计详情')).not.toBeInTheDocument();
    expect(screen.getAllByText(/web_search/).length).toBeGreaterThan(0);
    expect(screen.getByRole('navigation', { name: '问题导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跳转到问题 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跳转到问题 1' })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByText('思考过程')).toBeInTheDocument();
    expect(screen.getByText('至少一个抓取来源支撑了最终答案。')).toBeInTheDocument();
    expect(screen.getAllByText('已完成查询')).toHaveLength(1);
    expect(screen.getByText('web_search').closest('details')).not.toHaveAttribute('open');
    expect(document.querySelectorAll('.answer-message')).toHaveLength(1);
    expect(screen.getByRole('img', { name: 'chart.png' })).toHaveAttribute('src', '/api/artifacts/a-chart/content');
    expect(screen.getByTitle('chart.html')).toHaveAttribute('sandbox', 'allow-scripts');
    expect(document.querySelectorAll('.process-panel')).toHaveLength(1);
  });

  it('renders referenced artifacts beside findings and only links repeated references', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    const contextual = {
      ...snapshot,
      result: {
        ...snapshot.result!,
        findings: [
          { text: '第一个结论', source_urls: [], artifact_ids: ['a-chart'] },
          { text: '第二个结论', source_urls: [], artifact_ids: ['a-chart', 'a-html'] },
        ],
      },
      artifacts: snapshot.artifacts.map((artifact) => ({
        ...artifact,
        tool_call_id: artifact.id === 'a-chart' ? 't1' : 't2',
      })),
    };
    vi.mocked(getRun).mockResolvedValueOnce(contextual);

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '关联展示');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    const firstFinding = await screen.findByText('第一个结论');
    const secondFinding = screen.getByText('第二个结论');
    const chart = screen.getByRole('img', { name: 'chart.png' });
    const html = screen.getByTitle('chart.html');
    expect(firstFinding.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chart.compareDocumentPosition(secondFinding) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(secondFinding.compareDocumentPosition(html) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelectorAll('#artifact-output-a-chart')).toHaveLength(1);
    expect(screen.getByRole('link', { name: '查看上方已展示的输出' })).toHaveAttribute('href', '#artifact-output-a-chart');
    expect(screen.queryByText('其他输出')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '1 个输出 · 查看输出' })).toHaveAttribute('href', '#artifact-output-a-chart');
  });

  it('sorts unreferenced outputs, collapses more than two, and keeps safe renderers', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    const unreferenced = {
      ...snapshot,
      result: {
        ...snapshot.result!,
        findings: [{ text: '旧结果没有关联字段', source_urls: [] }],
      },
      artifacts: [
        { id: 'file-c', type: 'sandbox_output', metadata: { filename: 'report.csv' }, created_at: '2026-01-03', mime_type: 'text/csv', security_status: 'verified', content_url: '/api/artifacts/file-c/content' },
        { ...snapshot.artifacts[1], created_at: '2026-01-02' },
        { ...snapshot.artifacts[0], created_at: '2026-01-01' },
      ],
    };
    vi.mocked(getRun).mockResolvedValueOnce(unreferenced as unknown as typeof snapshot);

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '旧数据降级');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(await screen.findByText('其他输出 · 3')).toBeInTheDocument();
    const cards = [...document.querySelectorAll('.other-artifacts .artifact-card')];
    expect(cards.map((card) => card.id)).toEqual([
      'artifact-output-a-chart',
      'artifact-output-a-html',
      'artifact-output-file-c',
    ]);
    expect(screen.getByRole('img', { name: 'chart.png' })).toHaveAttribute('alt', 'chart.png');
    expect(screen.getByTitle('chart.html')).toHaveAttribute('sandbox', 'allow-scripts');
    expect(screen.getByRole('link', { name: /report.csv/ })).toHaveAttribute('href', '/api/artifacts/file-c/content');
  });

  it('does not show an output locator for tool calls without visible artifacts', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    vi.mocked(getRun).mockResolvedValueOnce({
      ...snapshot,
      artifacts: snapshot.artifacts.map((artifact) => ({ ...artifact, tool_call_id: null })),
    });

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '无过程输出');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(await screen.findByText('已完成查询')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /查看输出/ })).not.toBeInTheDocument();
  });

  it('omits the tool call count from the reasoning summary when no tools were called', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    vi.mocked(getRun).mockResolvedValueOnce({
      ...snapshot,
      tool_calls: [],
      turns: (snapshot.turns ?? []).map((turn) => ({ ...turn, selected_tool: null, tool_call_id: null })),
    });

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '直接思考');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(await screen.findByText('2 个步骤')).toBeInTheDocument();
    expect(screen.queryByText(/0 次工具调用/)).not.toBeInTheDocument();
  });

  it('keeps the streamed answer until a terminal snapshot contains the persisted result', async () => {
    const finalSnapshot = await vi.mocked(getRun)('fixture');
    vi.mocked(getRun)
      .mockResolvedValueOnce({ ...finalSnapshot, result: null, summary: null, status: 'completed' })
      .mockImplementation(() => new Promise((resolve) => window.setTimeout(() => resolve(finalSnapshot), 500)));
    vi.mocked(streamRunEvents).mockImplementationOnce((_runId, onEvent) => {
      window.setTimeout(() => {
        onEvent({ type: 'answer.started', payload: {} });
        onEvent({ type: 'answer.delta', payload: { delta: '流式回答不会消失' } });
        onEvent({ type: 'answer.settling', payload: { phase: 'structuring_and_verifying' } });
      }, 0);
      window.setTimeout(() => onEvent({ type: 'answer.completed', payload: { content: '流式回答不会消失' } }), 300);
      return () => undefined;
    });
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '竞态测试');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(await screen.findByText('流式回答不会消失')).toBeInTheDocument();
    expect(screen.getByText('正在整理并验证结果…')).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 200));
    expect(screen.getByText('流式回答不会消失')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('已完成查询')).toBeInTheDocument(), { timeout: 4000 });
    expect(screen.queryByText('流式回答不会消失')).not.toBeInTheDocument();
    vi.mocked(getRun).mockResolvedValue(finalSnapshot);
    vi.mocked(streamRunEvents).mockImplementation(() => () => undefined);
  });

  it('keeps live reasoning collapsed by default and persists the user choice within the conversation', async () => {
    const finalSnapshot = await vi.mocked(getRun)('fixture');
    const executingSnapshot = {
      ...finalSnapshot,
      status: 'executing',
      result: null,
      summary: null,
      turns: [],
      tool_calls: [],
      events: [],
      chat_messages: [{ id: 'u-live', role: 'user', content: '实时过程', status: 'completed', metadata: {} }],
    };
    vi.mocked(getRun).mockClear();
    vi.mocked(getRun).mockResolvedValue(executingSnapshot as typeof finalSnapshot);
    let emit: ((event: RunStreamEvent) => void) | undefined;
    vi.mocked(streamRunEvents).mockImplementationOnce((_runId, onEvent) => {
      emit = onEvent;
      return () => undefined;
    });

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '实时过程');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    const summary = await screen.findByText('思考过程');
    const panel = summary.closest('details');
    expect(panel).not.toHaveAttribute('open');
    expect(screen.getByText('实时更新')).toBeInTheDocument();
    expect(panel?.querySelector('.process-loading-pane')).toBeInTheDocument();
    expect(panel?.querySelector('.process-live-dot')).not.toBeInTheDocument();
    await waitFor(() => expect(emit).toBeTypeOf('function'));
    const snapshotCalls = vi.mocked(getRun).mock.calls.length;

    await userEvent.click(summary);
    expect(panel).toHaveAttribute('open');
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem('astra.process-panel-preferences.v1') ?? '{}')['task-1']).toBe(true));

    act(() => {
      emit?.({ id: 10, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
      emit?.({ id: 11, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '正在选择可靠来源' } });
    });
    expect(await screen.findByText('正在选择可靠来源')).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 150));
    expect(vi.mocked(getRun)).toHaveBeenCalledTimes(snapshotCalls);

    act(() => emit?.({ id: 12, type: 'answer.delta', payload: { delta: '开始回答' } }));
    expect(await screen.findByText('开始回答')).toBeInTheDocument();
    expect(panel).toHaveAttribute('open');

    await userEvent.click(summary);
    expect(panel).not.toHaveAttribute('open');
    act(() => emit?.({ id: 13, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '并继续验证' } }));
    expect(await screen.findByText('正在选择可靠来源并继续验证')).toBeInTheDocument();
    expect(panel).not.toHaveAttribute('open');

    await userEvent.click(summary);
    expect(panel).toHaveAttribute('open');
    await userEvent.click(screen.getByRole('button', { name: '新对话' }));
    expect(screen.queryByText('思考过程')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '实时过程 executing' }));
    expect(await screen.findByText('思考过程')).toBeInTheDocument();
    expect(screen.getByText('思考过程').closest('details')).toHaveAttribute('open');

    await userEvent.click(screen.getByText('思考过程'));
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem('astra.process-panel-preferences.v1') ?? '{}')['task-1']).toBe(false));
    await userEvent.click(screen.getByRole('button', { name: '新对话' }));
    await userEvent.click(screen.getByRole('button', { name: '实时过程 executing' }));
    expect(await screen.findByText('思考过程')).toBeInTheDocument();
    expect(screen.getByText('思考过程').closest('details')).not.toHaveAttribute('open');

    vi.mocked(getRun).mockResolvedValue(finalSnapshot);
    vi.mocked(streamRunEvents).mockImplementation(() => () => undefined);
  });

  it('does not carry an expanded process preference into a different conversation', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    window.localStorage.setItem('astra.process-panel-preferences.v1', JSON.stringify({ 'task-1': true }));
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-2', task_id: 'task-2', status: 'created' });
    vi.mocked(getRun).mockResolvedValueOnce({
      ...snapshot,
      id: 'run-2',
      task_id: 'task-2',
      chat_messages: [{ id: 'u-2', role: 'user', content: '另一个会话', status: 'completed', metadata: {} }],
    });

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '另一个会话');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    const summary = await screen.findByText('思考过程');
    expect(summary.closest('details')).not.toHaveAttribute('open');
  });

  it('sends selected reasoning policy with a run', async () => {
    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '分析复杂问题');
    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    await userEvent.click(screen.getByRole('button', { name: '深入' }));
    await userEvent.click(screen.getByRole('button', { name: '先规划' }));
    await userEvent.click(screen.getByRole('button', { name: '↑' }));
    expect(vi.mocked(createRun)).toHaveBeenLastCalledWith(
      expect.any(String),
      undefined,
      expect.objectContaining({ reasoning_effort: 'deep', planning_strategy: 'plan_first' }),
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
    );
  });

  it('shows validation error for empty goal', async () => {
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.clear(textbox);
    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(screen.getByText('请输入任务目标')).toBeInTheDocument();
  });

  it('submits with Enter and keeps Shift+Enter for a new line', async () => {
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.clear(textbox);
    await userEvent.type(textbox, '第一行{shift>}{enter}{/shift}第二行');
    expect(textbox).toHaveValue('第一行\n第二行');

    await userEvent.keyboard('{Enter}');
    expect(await screen.findByText('已完成查询')).toBeInTheDocument();
  });

  it('renders empty chat history as a non-interactive background state', () => {
    render(<App />);

    expect(screen.getByText('暂无对话')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '暂无对话' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByText('Navigate Ideas. Create Reality.')).toBeInTheDocument();
    expect(screen.getByText('今天想完成点什么？')).toBeInTheDocument();
  });

  it('reveals the local star burst after five quick logo clicks', async () => {
    render(<App />);
    const logo = screen.getByRole('button', { name: 'Astra 图标' });

    for (let index = 0; index < 5; index += 1) await userEvent.click(logo);

    expect(screen.getByTestId('astra-burst')).toBeInTheDocument();
  });

  it('keeps follow-up messages in the same history conversation', async () => {
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '查询 Astra');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));
    await screen.findByText('已完成查询');
    await userEvent.type(screen.getByRole('textbox'), '继续追问{Enter}');
    await screen.findAllByText('已完成查询');

    expect(vi.mocked(createRun)).toHaveBeenLastCalledWith('继续追问', 'task-1', expect.objectContaining({
      reasoning_effort: 'balanced',
      planning_strategy: 'adaptive',
      reflection_enabled: true,
      execution_mode: 'request_approval',
    }), expect.objectContaining({ provider: 'openai', name: 'gpt-5' }));
    expect(screen.getAllByRole('button', { name: /完成 completed/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /跳转到问题/ })).toHaveLength(2);
    const firstQuestion = screen.getByRole('button', { name: '跳转到问题 1' });
    const secondQuestion = screen.getByRole('button', { name: '跳转到问题 2' });
    expect(secondQuestion).toHaveAttribute('aria-current', 'true');
    await userEvent.click(firstQuestion);
    expect(firstQuestion).toHaveAttribute('aria-current', 'true');
    expect(secondQuestion).not.toHaveAttribute('aria-current');
  });

  it('opens settings and moves capabilities into the settings view', async () => {
    render(<App />);

    expect(screen.queryByText('Web Fetch')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));

    expect(screen.getByRole('heading', { name: '模型管理' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '工具' }));

    expect(screen.getByRole('heading', { name: '工具' })).toBeInTheDocument();
    expect(screen.getByText('Web Fetch')).toBeInTheDocument();
    expect(screen.getByText('Chart Render')).toBeInTheDocument();
    expect(screen.getByText('需要先启用 Docker 沙箱。')).toBeInTheDocument();
    const searchSwitch = screen.getByRole('switch', { name: /Web Search/ });
    await userEvent.click(searchSwitch);
    await waitFor(() => expect(updateToolSettings).toHaveBeenCalled());
    expect(searchSwitch).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('工具已停用，之后新建的任务不会调用它。')).toBeInTheDocument();
  });

  it('manages model providers and keeps API credentials masked by default', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '模型管理' }));

    expect(screen.getByRole('heading', { name: '模型管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Anthropic/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /DeepSeek/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /通义千问/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SiliconFlow/ })).toBeInTheDocument();
    const keyInput = screen.getByPlaceholderText('sk-...');
    expect(keyInput).toHaveAttribute('type', 'password');

    await userEvent.type(keyInput, 'secret-key');
    await userEvent.click(screen.getByRole('button', { name: '显示' }));
    expect(keyInput).toHaveAttribute('type', 'text');
    await userEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(screen.getByText('连接正常')).toBeInTheDocument();
  });

  it('restores conversation history and model credentials after remount', async () => {
    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '持久化测试');
    await userEvent.click(screen.getByRole('button', { name: '↑' }));
    await screen.findByText('已完成查询');
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'persisted-secret');

    cleanup();
    render(<App />);

    expect(screen.getByRole('button', { name: /完成 completed/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    expect(screen.getByPlaceholderText('sk-...')).toHaveValue('persisted-secret');
  });

  it('shows more than six persisted conversations and explains the retention limit', async () => {
    vi.mocked(listRuns).mockResolvedValueOnce(Array.from({ length: 8 }, (_, index) => ({
      id: `run-history-${index}`,
      task_id: `task-history-${index}`,
      status: 'completed',
      mode: 'general-agent',
      summary: `历史会话 ${index + 1}`,
      result: { summary: `历史会话 ${index + 1}` },
    })) as never);

    render(<App />);

    expect(await screen.findByRole('button', { name: '历史会话 8 completed' })).toBeInTheDocument();
    expect(screen.getByText('最多保留最近 100 个会话')).toBeInTheDocument();
  });

  it('can repeatedly switch to incomplete failed history without crashing', async () => {
    vi.mocked(listRuns).mockResolvedValueOnce([
      {
        id: 'failed-run', task_id: 'failed-task', status: 'blocked', mode: 'web_agent', summary: '失败记录',
        result: {
          summary: '模型调用失败', findings: [], sources: [], failed_sources: [], source_quality: [],
          conflicts: [], caveats: [], verification_notes: ['运行未能完成。'], memory_references: [],
          audit_refs: { agent_turn_count: 0, referenced_artifact_ids: [] },
          error: {
            type: 'dependency.model_response_invalid', code: 'MODEL_FAILED', message: '模型调用失败',
            retryable: true, trace_id: 'req_failed', details: {},
          },
        },
        chat_messages: [{ id: 'failed-message', role: 'assistant', content: '模型调用失败', status: 'blocked' }],
      },
      {
        id: 'empty-run', task_id: 'empty-task', status: 'completed', mode: 'web_agent', summary: '空数组记录',
        result: { summary: '已完成' }, chat_messages: [],
      },
    ] as never);
    render(<App />);

    const failed = await screen.findByRole('button', { name: '失败记录 blocked' });
    const empty = screen.getByRole('button', { name: '空数组记录 completed' });
    await userEvent.click(failed);
    expect(screen.getByText('模型调用失败')).toBeInTheDocument();
    await userEvent.click(empty);
    await userEvent.click(failed);
    await userEvent.click(empty);

    expect(screen.getByRole('heading', { name: 'Astra' })).toBeInTheDocument();
  });

  it('syncs enabled provider models into the chat model selector', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '模型管理' }));
    await userEvent.click(screen.getByRole('button', { name: /DeepSeek/ }));
    await userEvent.click(screen.getByRole('switch'));
    await userEvent.click(screen.getByRole('button', { name: '关闭设置' }));
    await userEvent.click(screen.getByRole('button', { name: /当前模型/ }));

    expect(screen.getByRole('button', { name: /deepseek-v4-pro/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /deepseek-v4-flash/ })).toBeInTheDocument();
  });

  it('shows sandbox and execution policies in runtime settings', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '运行时' }));

    expect(screen.getByRole('heading', { name: 'Docker 运行时' })).toBeInTheDocument();
    expect(screen.getByText('Docker Ready')).toBeInTheDocument();
    expect(screen.getByText('尚未添加自定义依赖')).toBeInTheDocument();
    expect(screen.getByText('numpy')).toBeInTheDocument();
    expect(screen.getByLabelText('numpy 已锁定')).toBeInTheDocument();
    expect(screen.getByText('2.2.6')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '配置已同步' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '添加依赖' }));
    await userEvent.type(screen.getByLabelText('依赖名称'), 'polars');
    expect(screen.getByLabelText('polars版本')).toHaveAttribute('placeholder', '最新版本');
    await userEvent.click(screen.getByRole('button', { name: '批量添加' }));
    await userEvent.type(screen.getByLabelText(/每行一个依赖/), 'openpyxl==3.1.5');
    await userEvent.click(screen.getByRole('button', { name: '添加到列表' }));
    expect(screen.getAllByLabelText('依赖名称')).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: '删除 openpyxl' }));
    expect(screen.getAllByLabelText('依赖名称')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '删除 polars' })).toHaveTextContent('−');
    expect(screen.getByRole('button', { name: '构建并激活' })).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: '构建并激活' }));
    expect(vi.mocked(buildRuntime)).toHaveBeenCalledWith([{ name: 'polars', version: '' }]);
    expect(screen.queryByText('工具调用上限')).not.toBeInTheDocument();
    expect(screen.queryByText('并行工具调用')).not.toBeInTheDocument();
    expect(screen.queryByText('工具失败重试')).not.toBeInTheDocument();
    expect(screen.queryByText('命令执行确认')).not.toBeInTheDocument();
    expect(screen.getByText('当前镜像')).toBeInTheDocument();
  });

  it('shows live runtime build progress and supports cancellation', async () => {
    vi.mocked(getRuntimeProfile).mockResolvedValue({
      dependencies: [{ name: 'polars', version: '' }],
      core_dependencies: [],
      active_image: 'astra-data-viz:0.1.0',
      dependency_digest: 'base',
      build: { id: 'build-1', status: 'building', phase: '构建镜像并安装依赖', progress: 42, log: 'installing polars' },
    });
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '运行时' }));

    expect(await screen.findByRole('progressbar', { name: '依赖构建进度' })).toHaveAttribute('aria-valuenow', '42');
    expect(screen.getByText('构建镜像并安装依赖')).toBeInTheDocument();
    expect(screen.getByText('installing polars')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '取消构建' }));
    expect(vi.mocked(cancelRuntimeBuild)).toHaveBeenCalledWith('build-1');
    vi.mocked(getRuntimeProfile).mockResolvedValue({
      dependencies: [],
      core_dependencies: [],
      active_image: 'astra-data-viz:0.1.0',
      dependency_digest: 'base',
      build: null,
    });
  });

  it('keeps dependency edits pending when a build request fails', async () => {
    vi.mocked(buildRuntime).mockRejectedValueOnce(new Error('Docker 服务不可用'));
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '运行时' }));
    await userEvent.click(screen.getByRole('button', { name: '添加依赖' }));
    await userEvent.type(screen.getByLabelText('依赖名称'), 'polars');
    await userEvent.click(screen.getByRole('button', { name: '构建并激活' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Docker 服务不可用');
    expect(screen.getByLabelText('依赖名称')).toHaveValue('polars');
    expect(screen.getByRole('button', { name: '构建并激活' })).toBeEnabled();
  });

  it('shows asynchronous build failure details and allows retrying unchanged dependencies', async () => {
    vi.mocked(getRuntimeProfile).mockResolvedValue({
      dependencies: [{ name: 'cv2', version: '' }],
      core_dependencies: [],
      active_image: 'astra-data-viz:0.1.0',
      dependency_digest: 'base',
      build: { id: 'build-1', status: 'failed', phase: '构建失败', progress: 16, log: 'No matching distribution found for cv2' },
    });
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '运行时' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('No matching distribution found for cv2');
    expect(screen.getByRole('button', { name: '构建并激活' })).toBeEnabled();
  });

  it('shows the resolved version after an unpinned dependency build succeeds', async () => {
    vi.mocked(getRuntimeProfile).mockResolvedValue({
      dependencies: [{ name: 'openpyxl', version: '3.1.5' }],
      core_dependencies: [],
      active_image: 'astra-data-viz:custom-resolved',
      dependency_digest: 'resolved',
      build: { id: 'build-1', status: 'succeeded', phase: '构建完成', progress: 100, log: '构建与导入验证成功' },
    });
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '运行时' }));

    expect(await screen.findByLabelText('openpyxl版本')).toHaveValue('3.1.5');
    expect(screen.getByRole('button', { name: '配置已同步' })).toBeDisabled();
  });

  it('keeps validation and data settings task agnostic', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '验证与安全' }));
    expect(screen.getByText('验证强度')).toBeInTheDocument();
    expect(screen.getByText('验证失败处理')).toBeInTheDocument();
    expect(screen.queryByText('冲突处理')).not.toBeInTheDocument();
    expect(screen.queryByText('最低独立来源数')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '数据与隐私' }));
    expect(screen.getByText('工具内容保留')).toBeInTheDocument();
    expect(screen.queryByText('保存抓取正文')).not.toBeInTheDocument();
  });

  it('switches the interface between Chinese and English', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '界面' }));
    await userEvent.selectOptions(screen.getByDisplayValue('中文'), 'en');

    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tools' })).toBeInTheDocument();
    expect(screen.getByText('Choose the interface language')).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('en');
    await userEvent.click(screen.getByRole('button', { name: 'Close settings' }));
    await userEvent.click(screen.getByRole('button', { name: /^Usage/ }));
    expect(screen.getByRole('dialog', { name: 'Usage' })).toBeInTheDocument();
    expect(await screen.findByText('Total tokens')).toBeInTheDocument();
    expect(screen.getByText('Token reporting coverage 100%')).toBeInTheDocument();
  });

  it('switches between system, dark, and light themes', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '界面' }));
    const themeSelect = screen.getByDisplayValue('跟随系统');

    await userEvent.selectOptions(themeSelect, 'dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');

    await userEvent.selectOptions(themeSelect, 'light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('keeps conversation reasoning controls in the model menu', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));

    expect(screen.getByText('规划策略')).toBeInTheDocument();
    expect(screen.getByText('触发方式')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '深入' })).toBeInTheDocument();
    expect(screen.queryByText('最大 Agent 轮次')).not.toBeInTheDocument();
    expect(screen.queryByText('工具调用上限')).not.toBeInTheDocument();
  });

  it('switches execution modes and confirms before enabling bypass', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /请求批准/ }));
    await userEvent.click(screen.getByRole('button', { name: /仅规划/ }));
    expect(screen.getByRole('button', { name: /仅规划/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /仅规划/ }));
    await userEvent.click(screen.getByRole('button', { name: /自动批准/ }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /仅规划/ }));
    await userEvent.click(screen.getByRole('button', { name: /自动批准/ }));
    await userEvent.click(screen.getByRole('button', { name: '确认启用自动批准' }));
    expect(screen.getByRole('button', { name: /自动批准/ })).toHaveClass('mode-bypass');
  });

  it('uses translated execution mode names in the English interface', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '界面' }));
    await userEvent.selectOptions(screen.getByDisplayValue('中文'), 'en');
    await userEvent.click(screen.getByRole('button', { name: 'Close settings' }));
    await userEvent.click(screen.getByRole('button', { name: /Request approval/ }));

    expect(screen.getByRole('button', { name: /Plan/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Auto approve/ })).toBeInTheDocument();
    expect(screen.queryByText('仅规划')).not.toBeInTheDocument();
    expect(screen.queryByText('自动批准')).not.toBeInTheDocument();
  });

  it('hides reflection trigger choices when reflection is disabled', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByText('触发方式')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch'));
    expect(screen.queryByText('触发方式')).not.toBeInTheDocument();
  });

  it('opens usage statistics and changes the active model', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /用量统计/ }));
    expect(screen.getByRole('dialog', { name: '用量统计' })).toBeInTheDocument();
    expect(await screen.findByText('Token 总量')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '关闭用量统计' }));

    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    await userEvent.click(screen.getByRole('button', { name: /gpt-5-mini/ }));
    expect(screen.getByRole('button', { name: '当前模型：gpt-5-mini' })).toBeInTheDocument();
  });

  it('closes composer menus when clicking outside or pressing escape', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '添加内容' }));
    expect(screen.getByText('上传文件')).toBeInTheDocument();
    await userEvent.click(document.body);
    expect(screen.queryByText('上传文件')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByText('对话策略')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByText('对话策略')).not.toBeInTheDocument();
  });
});
