import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App, DocumentationPage } from '../src/App';
import { buildRuntime, cancelRun, cancelRuntimeBuild, confirmPlanExecution, createConversationShare, createRun, decideToolApproval, deleteConversation, executeConversationCommand, getConversation, getConversationContext, getConversationStrategy, getRun, getRuntimeDefaultModel, getRuntimeProfile, listConversationShares, listConversations, listLibraryDeliverables, listRuns, listSkills, listSystemCommands, resetRuntimeAgentProfile, resolveModelContextCapabilities, resolveModelThinkingCapabilities, resumeRun, revisePlan, revokeConversationShare, streamRunEvents, takeCreatedRunStream, testModelConnection, updateConversation, updateConversationStrategy, updateRuntimeAgentProfile, updateRuntimeMemorySettings, updateToolSettings, type ModelThinkingCapability, type RunStreamEvent, type SkillSummary } from '../src/api';
import type { PlanGraphSnapshot, RunView } from '../src/types';

vi.mock('../src/api', () => ({
  AstraApiError: class AstraApiError extends Error {
    payload: unknown;

    constructor(payload: unknown) {
      super('Astra API error');
      this.payload = payload;
    }
  },
  getConversationStrategy: vi.fn(async () => ({ preferred_answer_mode: 'standard', reasoning_effort: 'balanced', max_tool_calls: 8, reflection_enabled: true, reflection_trigger: 'adaptive' })),
  getRuntimeDefaultModel: vi.fn(async () => { throw new Error('runtime default unavailable in unit tests'); }),
  testModelConnection: vi.fn(async (model) => ({
    connected: true,
    provider: model.provider,
    model: model.name,
    message: '连接成功，模型已响应测试请求。',
    latency_ms: 42,
    error_code: null,
  })),
  resolveModelThinkingCapabilities: vi.fn(async (models: Array<{ provider: string; model: string }>) => models.map((item) => ({
    ...item,
    supported: item.provider === 'openai',
    toggle: item.provider === 'openai' ? 'always_on' : 'unavailable',
    depths: item.provider === 'openai'
      ? [{ id: 'minimal', label: 'Minimal' }, { id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }]
      : [],
    default_enabled: item.provider === 'openai',
    default_depth: item.provider === 'openai' ? 'medium' : null,
    reason: item.provider === 'openai' ? null : 'unknown_model_capability',
    adapter: item.provider === 'openai' ? 'openai-gpt5' : 'unsupported',
    capability_version: 2,
  }))),
  resolveModelContextCapabilities: vi.fn(async (models: Array<{ provider: string; model: string }>) => models.map((item) => ({
    ...item,
    window_tokens: item.provider === 'openai' ? 400000 : 131072,
    max_output_tokens: item.provider === 'openai' ? 128000 : null,
    source: item.provider === 'openai' ? 'catalog' : 'fallback',
    verified: item.provider === 'openai',
    documentation_url: item.provider === 'openai' ? 'https://developers.openai.com/api/docs/models/gpt-5' : null,
    capability_version: 2,
  }))),
  updateConversationStrategy: vi.fn(async (strategy) => strategy),
  getToolSettings: vi.fn(async () => ({ tools: [
    { name: 'web_search', label: 'Web Search', description: '搜索公开网页并生成候选来源', enabled: true, available: true },
    { name: 'web_fetch', label: 'Web Fetch', description: '自适应提取页面主要内容', enabled: true, available: true },
    { name: 'chart_render', label: 'Chart Render', description: '生成图表', enabled: true, available: false, unavailable_reason: '需要先启用安全运行环境。' },
    { name: 'bash_execute', label: 'Bash Execute', description: '在隔离容器中执行命令', enabled: false, available: true },
    { name: 'swarm', label: 'Swarm / 子 Agent', description: '并发创建受治理的子 Agent 并自动汇合结果', enabled: true, available: true, unavailable_reason: null },
  ] })),
  updateToolSettings: vi.fn(async (tools) => ({ tools })),
  getRuntimeProfile: vi.fn(async () => ({ dependencies: [], core_dependencies: [{ name: 'numpy', version: '2.2.6' }, { name: 'matplotlib', version: '3.10.3' }], active_image: 'astra-data-viz:0.1.0', dependency_digest: 'base', build: null, agent_profile: { source: 'default', version: 'profile-default', documents: { identity: '# Astra Identity\n\n## Identity\nDefault', soul: '# Astra Soul', memory: '# Astra Memory Protocol', autodream: '# Astra AutoDream Protocol' } }, memory_settings: { write_enabled: true, recall_enabled: false, retrieval_max_items: 8, retrieval_max_tokens: 2000, retrieval_min_confidence: 0.2, retrieval_min_score: 0.05, autodream_enabled: false, autodream_scan_seconds: 3600, autodream_min_candidates: 2 } })),
  updateRuntimeAgentProfile: vi.fn(async (documents) => ({ source: 'user', version: 'profile-user', documents })),
  resetRuntimeAgentProfile: vi.fn(async () => ({ source: 'default', version: 'profile-default', documents: { identity: '# Astra Identity\n\n## Identity\nDefault', soul: '# Astra Soul', memory: '# Astra Memory Protocol', autodream: '# Astra AutoDream Protocol' } })),
  updateRuntimeMemorySettings: vi.fn(async (settings) => settings),
  buildRuntime: vi.fn(async () => ({ dependencies: [{ name: 'polars', version: '' }], core_dependencies: [], active_image: 'astra-data-viz:0.1.0', dependency_digest: 'base', build: { id: 'build-1', status: 'queued', phase: '等待构建', progress: 0, log: '等待构建' } })),
  cancelRuntimeBuild: vi.fn(async () => ({ dependencies: [{ name: 'polars', version: '' }], core_dependencies: [], active_image: 'astra-data-viz:0.1.0', dependency_digest: 'base', build: { id: 'build-1', status: 'cancelled', phase: '已取消', progress: 12, log: '构建已由用户取消' } })),
  streamRunEvents: vi.fn(() => () => undefined),
  takeCreatedRunStream: vi.fn(() => undefined),
  createRun: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'created', answer_mode: 'standard' })),
  confirmPlanExecution: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'executing' })),
  revisePlan: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'waiting_user' })),
  getPlanVersion: vi.fn(),
  getPlanVersionDiff: vi.fn(),
  cancelRun: vi.fn(async () => ({
    id: 'run-1', task_id: 'task-1', status: 'cancelled', mode: 'general-agent', summary: '已终止本次运行。', result: { summary: '已终止本次运行。', findings: [], sources: [], failed_sources: [], source_quality: [], conflicts: [], caveats: ['运行已由用户终止，未继续执行后续步骤。'], verification_notes: [] },
    steps: [], tool_calls: [], artifacts: [], events: [{ id: 1, type: 'run.cancelled', payload: { category: 'user_cancelled' }, created_at: '2026-07-14T00:00:00Z' }], turns: [], memories: [], chat_messages: [{ id: 'run-1-terminal', role: 'assistant', content: '已终止本次运行。', status: 'completed', metadata: { terminal_status: 'cancelled' } }],
  })),
  listConversations: vi.fn(async () => []),
  listScheduledTasks: vi.fn(async () => []),
  listScheduledTaskRuns: vi.fn(async () => []),
  setScheduledTaskEnabled: vi.fn(),
  runScheduledTask: vi.fn(),
  updateScheduledTask: vi.fn(),
  updateHeartbeat: vi.fn(),
  disableHeartbeat: vi.fn(),
  deleteScheduledTask: vi.fn(),
  getConversation: vi.fn(async (id) => ({ id, title: '对话', title_source: 'auto', pinned_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), last_run_status: null, last_message_preview: '', has_active_share: false, runs: [] })),
  updateConversation: vi.fn(async (id, patch) => ({ id, title: patch.title ?? '对话', title_source: patch.title ? 'user' : 'auto', pinned_at: patch.pinned ? new Date().toISOString() : null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), last_run_status: 'completed', last_message_preview: '', has_active_share: false })),
  deleteConversation: vi.fn(async () => undefined),
  createConversationShare: vi.fn(async () => ({ url: '/share/token', created_at: new Date().toISOString(), updated_at: new Date().toISOString() })),
  revokeConversationShare: vi.fn(async () => undefined),
  listConversationShares: vi.fn(async () => []),
  listLibraryDeliverables: vi.fn(async () => []),
  listSkills: vi.fn(async () => []),
  listSystemCommands: vi.fn(async () => []),
  getConversationContext: vi.fn(async () => ({
    provider: 'openai', model: 'gpt-5', window_tokens: 400000, max_output_tokens: 128000,
    context_source: 'catalog', context_verified: true, context_documentation_url: 'https://developers.openai.com/api/docs/models/gpt-5', available_input_tokens: 391808,
    used_tokens: 12000, remaining_tokens: 379808, usage_ratio: 0.0306, auto_compact_ratio: 0.8,
    status: 'normal', estimated: true, summary_active: false, visible_run_count: 1,
    folded_run_count: 0, last_action: null, last_action_at: null,
  })),
  executeConversationCommand: vi.fn(async (_id, command, _provider, _model, argumentsText = '') => ({
    command: `/${command}`,
    message: command === 'clear' ? '模型将从当前消息重新开始，完整记录仍保留。' : '已整理较早的对话，完整记录仍保留。',
    context: {
      provider: 'openai', model: 'gpt-5', window_tokens: 400000, max_output_tokens: 128000,
      context_source: 'catalog', context_verified: true, context_documentation_url: 'https://developers.openai.com/api/docs/models/gpt-5', available_input_tokens: 391808,
      used_tokens: 5000, remaining_tokens: 386808, usage_ratio: 0.0128, auto_compact_ratio: 0.8,
      status: 'normal', estimated: true, summary_active: command === 'compact', visible_run_count: 1,
      folded_run_count: command === 'compact' ? 2 : 0, last_action: command, last_action_at: new Date().toISOString(),
    },
    details: {},
    user_message: {
      id: `command-${command}`,
      command: `/${command}`,
      content: `/${command}${argumentsText ? ` ${argumentsText}` : ''}`,
      arguments: argumentsText,
      after_run_count: 1,
      created_at: new Date().toISOString(),
    },
  })),
  getRunSkills: vi.fn(async () => ({
    catalog_digest: 'sha256:test',
    answer_mode: 'standard',
    draft_test: false,
    catalog: [],
    activations: [],
    resource_reads: [],
    attributed_actions: [],
    plan_bindings: [],
  })),
  listRuns: vi.fn(async () => []),
  resumeRun: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'executing' })),
  decideToolApproval: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'executing' })),
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
    answer_mode: 'standard',
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
    reasoning_policy: { effective: { reasoning_effort: 'balanced', execution_mode: 'request_approval' }, adjustments: [] },
    task_contract: { success_criteria: [{ id: 'criterion-result', description: '完成查询', status: 'satisfied' }] },
    plan_graph: {
      schema_version: 2,
      id: 'plan-1',
      run_id: 'run-1',
      version: 1,
      status: 'completed',
      nodes: [],
      edges: [],
    },
    state_version: 2,
  })),
}));

vi.mock('../src/SkillWorkbench', () => ({
  SkillWorkbench: ({ onClose }: { onClose: () => void }) => (
    <section aria-label="Skill 资料库">
      <button type="button" onClick={onClose}>关闭 Skill 资料库</button>
    </section>
  ),
}));

class MockEventSource {
  onmessage: ((message: MessageEvent) => void) | null = null;
  addEventListener = vi.fn();
  close = vi.fn();
}

Object.defineProperty(window, 'EventSource', {
  value: MockEventSource,
});

const helloSkill: SkillSummary = {
  id: 'skill-hello',
  name: 'hello-astra',
  qualified_identity: 'custom:hello-astra',
  origin: 'custom',
  description: '用于打招呼和介绍自己',
  enabled: true,
  readonly: false,
  lifecycle_state: 'published',
  active_revision: {
    id: 'revision-hello',
    version: 1,
    digest: 'sha256:hello',
    published_at: '2026-07-27T00:00:00Z',
    revoked_at: null,
    test_only: false,
    diagnostics: [],
  },
  diagnostics: [],
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:00:00Z',
};

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const values = new Map<string, string>([[
      'astra.model-providers.v2',
      JSON.stringify([{
        id: 'openai',
        name: 'OpenAI',
        enabled: true,
        endpoint: 'https://api.openai.com/v1',
        models: [{ id: 'gpt-5' }, { id: 'gpt-5-mini' }],
        apiKey: 'unit-test-key',
      }]),
    ]]);
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
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 });
    delete document.documentElement.dataset.theme;
    delete document.documentElement.dataset.astraQuestionToFirstTokenMs;
    document.documentElement.style.colorScheme = '';
  });

  it('opens the documentation center in a new standalone page', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<App />);

    const helpEntry = screen.getByRole('button', { name: '帮助文档' });
    await userEvent.click(helpEntry);

    expect(open).toHaveBeenCalledWith('/help', '_blank', 'noopener,noreferrer');
    expect(screen.queryByRole('heading', { name: 'Astra 文档中心' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新对话' })).toBeInTheDocument();
    open.mockRestore();
  });

  it('renders documentation as a page without the main application frame', async () => {
    render(<DocumentationPage />);

    expect(await screen.findByRole('heading', { name: 'Astra 文档中心' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /生产、召回、范围与整理/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByText('什么时候真正生效？')).toBeInTheDocument();
    const pageToc = screen.getByRole('navigation', { name: '本页目录' });
    expect(pageToc).toHaveClass('documentation-page-toc');
    expect(pageToc.closest('article')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /快速模式与可信模式.*定义、差异与选择建议/ }));
    expect(screen.getByRole('heading', { name: '快速模式与可信模式' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '完整差异对比' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Subagent 的行为差异' })).toBeInTheDocument();
    expect(screen.getByText('快速 Subagent 的轻量预算')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '如何选择' })).toHaveAttribute('href', '#answer-mode-choose');
    expect(screen.getByRole('button', { name: /快速模式与可信模式.*定义、差异与选择建议/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText('什么时候真正生效？')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /模型与运行设置.*思考、计划、反思、批准与上下文/ }));
    expect(screen.getByRole('heading', { name: '模型与运行设置' })).toBeInTheDocument();
    for (const heading of ['这些设置分别控制什么', '模型思考', '计划执行', '推理资源与工具调用上限', '反思循环与触发方式', '请求批准与自动批准', '上下文容量如何计算', '设置何时生效']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText(/默认上限为 8 次/)).toBeInTheDocument();
    expect(screen.getByText('计划确认不是工具批准')).toBeInTheDocument();
    expect(screen.getByText(/可用输入 = 模型窗口 − 回复预留/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '反思循环与触发方式' })).toHaveAttribute('href', '#runtime-settings-reflection');
    expect(screen.getByRole('button', { name: /模型与运行设置.*思考、计划、反思、批准与上下文/ })).toHaveAttribute('aria-current', 'page');
    await userEvent.click(screen.getByRole('button', { name: /关于 Astra.*创建动机、使命与版权信息/ }));
    expect(screen.getByRole('heading', { name: '关于 Astra' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '为什么创建 Astra' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '我们的使命' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '版权与许可证' })).toBeInTheDocument();
    expect(screen.getByText('Apache License 2.0')).toBeInTheDocument();
    expect(screen.getByText(/版权归各自权利人和贡献者所有/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '阅读 Apache License 2.0 完整原文' })).toHaveAttribute('href', 'https://www.apache.org/licenses/LICENSE-2.0');
    expect(screen.getByRole('button', { name: /关于 Astra.*创建动机、使命与版权信息/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: '新对话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '设置' })).not.toBeInTheDocument();
  });

  it('opens a documentation deep link on the topic that owns the anchor', async () => {
    window.history.replaceState(null, '', '/help#runtime-settings-reflection');
    render(<DocumentationPage />);

    expect(await screen.findByRole('heading', { name: '反思循环与触发方式' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /模型与运行设置.*思考、计划、反思、批准与上下文/ })).toHaveAttribute('aria-current', 'page');

    window.history.replaceState(null, '', '/');
  });

  it('shows the standalone context capacity control before the first conversation exists', async () => {
    render(<App />);

    const selector = await screen.findByRole('button', { name: '当前模型：gpt-5' });
    expect(getConversationContext).not.toHaveBeenCalled();

    await waitFor(() => expect(selector).toHaveClass('has-context'));
    const contextControl = screen.getByRole('button', { name: '上下文：已使用 0，总计 400K，剩余 392K（估算）' });
    expect(contextControl.querySelector('.model-context-ring-value')).toHaveAttribute('stroke-dasharray', '0 100');
    await userEvent.click(contextControl);
    const contextPanel = screen.getByRole('dialog', { name: '上下文容量' });
    expect(contextPanel).toHaveTextContent('本轮模型回复预留');
    expect(contextPanel).toHaveTextContent('模型窗口400K−回复预留8K=可用输入392K');
    expect(contextPanel).toHaveTextContent('它从模型窗口中扣除，但不计入“已使用输入”');
    expect(screen.getByRole('link', { name: '查看完整计算与排除项' })).toHaveAttribute('href', '/help#runtime-settings-context');
    expect(document.getElementById('model-context-status-description')).toHaveTextContent(
      '上下文：已使用 0，总计 400K，剩余 392K（估算）',
    );
    expect(getConversationContext).not.toHaveBeenCalled();
  });

  it('reinitializes the standalone context capacity when switching models before the first message', async () => {
    render(<App />);

    const initialSelector = await screen.findByRole('button', { name: '当前模型：gpt-5' });
    await waitFor(() => expect(initialSelector).toHaveClass('has-context'));
    await userEvent.click(initialSelector);
    await userEvent.click(screen.getByRole('button', { name: /gpt-5-mini/ }));

    const nextSelector = screen.getByRole('button', { name: '当前模型：gpt-5-mini' });
    await waitFor(() => expect(nextSelector).toHaveClass('has-context'));
    const contextControl = screen.getByRole('button', { name: '上下文：已使用 0，总计 400K，剩余 392K（估算）' });
    expect(contextControl.querySelector('.model-context-ring-value')).toHaveAttribute('stroke-dasharray', '0 100');
    expect(getConversationContext).not.toHaveBeenCalled();
  });

  it('keeps the runtime model separate from a configured provider with the same id', async () => {
    vi.mocked(getRuntimeDefaultModel).mockResolvedValueOnce({ provider: 'openai', model: 'gpt-5', configured: true });
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    const headings = [...document.querySelectorAll('.model-provider-heading')].map((item) => item.textContent);
    expect(headings).toEqual(expect.arrayContaining([
      expect.stringContaining('Astra 当前运行模型'),
      expect.stringContaining('OpenAI'),
    ]));
  });

  it('shows a local configuration error before submitting when no runnable model exists', async () => {
    window.localStorage.removeItem('astra.model-providers.v2');
    vi.mocked(getRuntimeDefaultModel).mockResolvedValueOnce({
      provider: 'openai',
      model: 'gpt-5',
      configured: false,
    });
    render(<App />);

    expect(await screen.findByRole('button', { name: '当前模型：未配置模型' })).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '不会提交');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(createRun).not.toHaveBeenCalled();
    const dialog = screen.getByRole('alertdialog', { name: '大模型尚未配置' });
    expect(dialog).toHaveTextContent('请先在模型管理中启用供应商并配置模型');
  });

  it('submits a goal and renders the result', async () => {
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '查询 Astra');
    const send = screen.getByRole('button', { name: '发送' });
    await waitFor(() => expect(send).toBeEnabled());
    await userEvent.click(send);

    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getRun).toHaveBeenCalledWith('run-1', expect.any(AbortSignal), 'initial'));
    const snapshotResults = vi.mocked(getRun).mock.results;
    await act(async () => { await snapshotResults[snapshotResults.length - 1]?.value; });
    expect(await screen.findByText('已完成查询')).toBeInTheDocument();
    expect(screen.queryByText('最终结果')).not.toBeInTheDocument();
    const supplementarySummary = screen.getByText('附加信息').closest('summary');
    const supplementary = supplementarySummary?.closest('details');
    expect(supplementary).toHaveClass('answer-supplementary-flat');
    expect(supplementary).not.toHaveAttribute('open');
    expect(screen.getByText('支撑证据')).toBeInTheDocument();
    expect(screen.getByText('限制与注意事项')).toBeInTheDocument();
    await userEvent.click(supplementarySummary!);
    expect(supplementary).toHaveAttribute('open');
    expect(supplementary?.querySelectorAll('details')).toHaveLength(0);
    expect(screen.getByText('发现一条证据')).toBeInTheDocument();
    expect(screen.getByText('发现一条证据').tagName).toBe('STRONG');
    expect(screen.getByText(/92%/)).toBeInTheDocument();
    expect(screen.queryByText('succeeded')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Example/ })).toHaveAttribute('href', 'https://example.com/docs');
    expect(screen.getByRole('link', { name: '关联来源' })).toHaveAttribute('href', 'https://example.com');
    expect(screen.queryByText('审计详情')).not.toBeInTheDocument();
    expect(screen.getAllByText(/web_search/).length).toBeGreaterThan(0);
    expect(screen.queryByText('这里展示 Astra 的公开执行过程摘要，不是模型隐藏思维链。')).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '问题导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跳转到问题 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跳转到问题 1' })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: '跳转到问题 1' })).toHaveClass('wave-distance-0');
    const completedProcessTitle = screen.getByText('思考完成');
    expect(completedProcessTitle).toBeInTheDocument();
    expect(completedProcessTitle.closest('summary')?.querySelector('.process-thinking-dots')).not.toBeInTheDocument();
    expect(screen.getByText('至少一个抓取来源支撑了最终答案。')).toBeInTheDocument();
    expect(screen.getAllByText('已完成查询')).toHaveLength(1);
    expect(screen.getByText('web_search').closest('details')).not.toHaveAttribute('open');
    expect(document.querySelectorAll('.answer-message')).toHaveLength(1);
    expect(screen.getByRole('img', { name: 'chart.png' })).toHaveAttribute('src', '/api/artifacts/a-chart/content');
    expect(screen.getByTitle('chart.html')).toHaveAttribute('sandbox', 'allow-scripts');
    expect(document.querySelectorAll('.process-panel')).toHaveLength(1);
    expect(document.querySelectorAll('.process-decision-group')).toHaveLength(0);
    expect(screen.queryByText('正在执行计划')).not.toBeInTheDocument();
    expect(screen.queryByText('正在分析下一步')).not.toBeInTheDocument();
  });

  it('renders the preconnected first delta without requesting an initial snapshot', async () => {
    vi.mocked(getRun).mockClear();
    vi.mocked(streamRunEvents).mockClear();
    vi.mocked(takeCreatedRunStream).mockReturnValueOnce({
      created: Promise.resolve({
        run_id: 'run-1',
        task_id: 'task-1',
        status: 'created',
        answer_mode: 'standard',
      }),
      subscribe(onEvent) {
        onEvent({ type: 'answer.started', payload: {} });
        onEvent({ id: 1, type: 'answer.delta', payload: { delta: '首个片段' } });
        return () => undefined;
      },
      close: vi.fn(),
    });
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '低延迟回答');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('首个片段')).toBeInTheDocument();
    expect(getRun).not.toHaveBeenCalled();
    expect(streamRunEvents).not.toHaveBeenCalled();
    expect(Number(document.documentElement.dataset.astraQuestionToFirstTokenMs)).toBeGreaterThan(0);
  });

  it('selects a Skill through slash commands, highlights it, and submits a clean explicit binding', async () => {
    vi.mocked(listSkills).mockResolvedValueOnce([helloSkill]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await waitFor(() => expect(listSkills).toHaveBeenCalled());
    await userEvent.type(textbox, '/hel');
    const listbox = screen.getByRole('listbox', { name: '快捷操作和 Skill' });
    expect(listbox).toBeInTheDocument();
    const option = screen.getByRole('option', { name: /hello-astra/ });
    expect(option).toHaveAttribute('aria-selected', 'false');
    await userEvent.click(option);

    expect(textbox).toHaveValue('');
    expect(screen.getByLabelText('已选择 Skill')).toHaveTextContent('hello-astra');
    expect(screen.getByRole('button', { name: '移除 Skill hello-astra' })).toBeInTheDocument();

    await userEvent.type(textbox, '介绍一下你自己');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(createRun).toHaveBeenLastCalledWith(
      '介绍一下你自己',
      undefined,
      'standard',
      expect.any(Object),
      expect.any(Object),
      undefined,
      ['custom:hello-astra'],
    );
    expect(screen.getByLabelText('已选择 Skill')).toHaveTextContent('hello-astra');
  });

  it('executes preset slash commands without submitting a model message and refreshes context status', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      { name: 'compact', command: '/compact', description: '整理较早的对话，保留近期内容和完整记录', effect: 'compact_context', argument_mode: 'optional', default_arguments: '保留后续任务所需的关键上下文', usage: '/compact [压缩方向]', side_effect: 'write', available: true, execution_mode: 'host', unavailable_reason: null },
      { name: 'clear', command: '/clear', description: '让模型从当前消息重新开始，完整记录仍会保留', effect: 'clear_context', argument_mode: 'none', usage: '/clear', side_effect: 'write', available: true, execution_mode: 'host', unavailable_reason: null },
    ]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '先建立对话');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(getRun).toHaveBeenCalled());
    await waitFor(() => expect(getConversationContext).toHaveBeenCalled());
    const initialSelector = screen.getByRole('button', { name: '当前模型：gpt-5' });
    expect(initialSelector.querySelector('[data-testid="model-context-ring"]')).toBeInTheDocument();
    expect(initialSelector.querySelector('.model-context-ring-value')).toHaveAttribute('stroke-dasharray', '3 100');
    expect(initialSelector.querySelector('.model-context-usage')).not.toBeInTheDocument();
    expect(document.querySelector('.context-window-status')).not.toBeInTheDocument();

    const runCalls = vi.mocked(createRun).mock.calls.length;
    await userEvent.type(textbox, '/comp');
    const command = screen.getByRole('option', { name: /\/compact/ });
    expect(command).toHaveTextContent('快捷操作');
    await userEvent.click(command);
    expect(textbox).toHaveValue('/compact 保留后续任务所需的关键上下文');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(executeConversationCommand).toHaveBeenCalledWith('task-1', 'compact', 'openai', 'gpt-5', '保留后续任务所需的关键上下文'));
    expect(vi.mocked(createRun).mock.calls).toHaveLength(runCalls);
    expect(textbox).toHaveValue('');
    expect(document.querySelector('.message-command-prefix')).toHaveTextContent('/compact');
    const modelSelector = screen.getByRole('button', { name: '当前模型：gpt-5' });
    expect(modelSelector.querySelector('.model-context-tooltip')).toHaveTextContent('5K / 400K');
    expect(modelSelector.querySelector('.model-context-tooltip')).toHaveTextContent('已整理');
    expect(modelSelector.querySelector('.model-context-tooltip')).not.toHaveTextContent('模型目录');
    expect(modelSelector.querySelector('.model-context-tooltip')).not.toHaveTextContent('回退');
    expect(modelSelector.querySelector('.model-context-ring > .model-context-tooltip')).toBeInTheDocument();
    expect(modelSelector).toHaveAttribute('aria-describedby', 'model-thinking-summary-description model-context-status-description');
    expect(modelSelector).not.toHaveAttribute('title');
    expect(document.getElementById('model-context-status-description')).toHaveTextContent('已整理较早的对话，完整记录仍保留。');
    const contextControl = screen.getByRole('button', { name: /上下文：已使用 5K/ });
    await userEvent.click(contextControl);
    const capacityPanel = screen.getByRole('dialog', { name: '上下文容量' });
    expect(capacityPanel).toHaveTextContent('5K');
    expect(capacityPanel).toHaveTextContent('未折叠轮次的用户目标与最终结果');
    expect(capacityPanel).toHaveTextContent('不包含：工具日志、思考过程、中间事件和已折叠轮次。');
    expect(capacityPanel).toHaveTextContent('这里的数字怎么计算');
    expect(capacityPanel).not.toHaveTextContent('模型目录');
    expect(capacityPanel).not.toHaveTextContent('回退');
    expect(document.querySelector('.context-window-status')).not.toBeInTheDocument();
    expect(document.querySelector('.chat-composer')).not.toHaveClass('has-context-status');
  });

  it('stages parameterized automation commands and submits them as host operations', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      {
        name: 'schedule',
        command: '/schedule',
        description: '创建、查看或管理当前对话的定时任务',
        effect: 'manage_schedules',
        argument_mode: 'required',
        usage: '/schedule list|show|create|pause|resume|run|delete …',
        side_effect: 'mixed',
        available: true,
        execution_mode: 'host',
        unavailable_reason: null,
      },
    ]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '先建立对话');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(getRun).toHaveBeenCalled());
    const runCalls = vi.mocked(createRun).mock.calls.length;

    await userEvent.type(textbox, '/sche');
    expect(screen.getByRole('option', { name: /\/schedule/ })).toBeInTheDocument();
    await userEvent.keyboard('{Enter}');
    expect(textbox).toHaveValue('/schedule ');
    expect(executeConversationCommand).not.toHaveBeenCalled();

    await userEvent.type(textbox, 'list');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(executeConversationCommand).toHaveBeenCalledWith(
      'task-1',
      'schedule',
      'openai',
      'gpt-5',
      'list',
    ));
    expect(vi.mocked(createRun).mock.calls).toHaveLength(runCalls);
    expect(textbox).toHaveValue('');

    vi.mocked(executeConversationCommand).mockRejectedValueOnce(new Error('offline'));
    await userEvent.type(textbox, '/schedule list');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(screen.getByText('操作执行失败，输入内容已保留，可稍后重试。')).toBeInTheDocument());
    expect(textbox).toHaveValue('/schedule list');
    expect(vi.mocked(createRun).mock.calls).toHaveLength(runCalls);
  });

  it('executes /clear directly without requiring message text', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      { name: 'clear', command: '/clear', description: '清空整个模型上下文', effect: 'clear_context', argument_mode: 'none', usage: '/clear', side_effect: 'write', available: true, execution_mode: 'host', unavailable_reason: null },
    ]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '先建立对话');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(getRun).toHaveBeenCalled());
    const runCalls = vi.mocked(createRun).mock.calls.length;

    await userEvent.type(textbox, '/cl');
    await userEvent.click(screen.getByRole('option', { name: /\/clear/ }));

    await waitFor(() => expect(executeConversationCommand).toHaveBeenCalledWith('task-1', 'clear', 'openai', 'gpt-5'));
    expect(vi.mocked(createRun).mock.calls).toHaveLength(runCalls);
    expect(textbox).toHaveValue('');
    expect(document.querySelector('.message-command-prefix')).toHaveTextContent('/clear');
  });

  it('treats /clear as an idempotent local command before a conversation exists', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      { name: 'clear', command: '/clear', description: '清空整个模型上下文', effect: 'clear_context', argument_mode: 'none', usage: '/clear', side_effect: 'write', available: true, execution_mode: 'host', unavailable_reason: null },
    ]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '/clear');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(executeConversationCommand).not.toHaveBeenCalled();
    expect(textbox).toHaveValue('');
    expect(screen.queryByText('请先开始一段对话，再使用此快捷操作。')).not.toBeInTheDocument();
    expect(document.querySelector('.message-command-prefix')).toHaveTextContent('/clear');
  });

  it('treats /compact as an idempotent local command before a conversation exists', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      { name: 'compact', command: '/compact', description: '整理较早的对话', effect: 'compact_context', argument_mode: 'optional', default_arguments: '保留后续任务所需的关键上下文', usage: '/compact [压缩方向]', side_effect: 'write', available: true, execution_mode: 'host', unavailable_reason: null },
    ]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '/compact');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(executeConversationCommand).not.toHaveBeenCalled();
    expect(textbox).toHaveValue('');
    expect(screen.queryByText('请先开始一段对话，再使用此快捷操作。')).not.toBeInTheDocument();
    expect(document.querySelector('.message-command-prefix')).toHaveTextContent('/compact');
    expect(document.querySelector('.message-command-arguments')).toHaveTextContent('保留后续任务所需的关键上下文');
  });

  it('routes /subagent to a quick required-subagent Run and preserves arguments on failure', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      {
        name: 'subagent',
        command: '/subagent',
        description: '使用 Astra Swarm 并发子 Agent 完成指定任务',
        effect: 'start_subagent_run',
        argument_mode: 'required',
        usage: '/subagent <任务>',
        side_effect: 'write',
        execution_mode: 'run',
        unavailable_reason: null,
        available: true,
      },
    ]);
    vi.mocked(createRun).mockRejectedValueOnce(new Error('temporarily unavailable'));
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '/subagent 调研三个独立方案');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(createRun).toHaveBeenCalledWith(
      '调研三个独立方案',
      undefined,
      'standard',
      expect.any(Object),
      expect.any(Object),
      undefined,
      undefined,
      'required',
    ));
    expect(executeConversationCommand).not.toHaveBeenCalled();
    expect(textbox).toHaveValue('/subagent 调研三个独立方案');
  });

  it('keeps trusted /subagent on automatic trusted Plan execution', async () => {
    vi.mocked(listSystemCommands).mockResolvedValueOnce([
      {
        name: 'subagent',
        command: '/subagent',
        description: '使用 Astra Swarm 并发子 Agent 完成指定任务',
        effect: 'start_subagent_run',
        argument_mode: 'required',
        usage: '/subagent <任务>',
        side_effect: 'write',
        execution_mode: 'run',
        unavailable_reason: null,
        available: true,
      },
    ]);
    render(<App />);
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '/subagent 调研三个独立方案');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(createRun).toHaveBeenCalledWith(
      '调研三个独立方案',
      undefined,
      'trusted',
      expect.any(Object),
      expect.any(Object),
      'auto',
      undefined,
      'required',
    ));
  });

  it('renders quick Subagents in the compact panel without a trusted graph', async () => {
    const base = await getRun('run-1');
    const child = {
      id: 'agent-child-1',
      parent_execution_id: 'agent-root',
      execution_type: 'child',
      identity_id: 'identity-child-1',
      delegation_id: 'delegation-1',
      request_id: 'compare-a',
      depth: 1,
      ordinal: 0,
      objective: '比较方案 A',
      creation_reason: '并行比较独立候选',
      required: true,
      status: 'running',
      phase: 'executing',
      wait_reason: null,
      budget_envelope: {},
      budget_usage: { tokens: 120 },
      permissions: ['network_read'],
      capabilities: ['information.search'],
      artifact_ids: [],
      result_summary: null,
      open_issues: [],
      error: null,
      created_at: 'now',
      updated_at: 'now',
      finished_at: null,
      plan: null,
      children: [],
    };
    vi.mocked(getRun).mockResolvedValue({
      ...base,
      answer_mode: 'standard',
      plan_graph: {},
      agent_executions: [{
        ...child,
        id: 'agent-root',
        parent_execution_id: null,
        execution_type: 'root',
        identity_id: 'identity-root',
        delegation_id: null,
        request_id: 'root',
        depth: 0,
        objective: '快速比较',
        status: 'running',
        children: [child],
      }],
      subagent_summary: {
        total: 1,
        running: 1,
        waiting: 0,
        completed: 0,
        failed: 0,
        cancelled: 0,
        budget_usage: { tokens: 120 },
        key_wait_reason: null,
      },
      agent_joins: [{
        id: 'join-1', parent_execution_id: 'agent-root', consumer_plan_node_id: null,
        join_key: 'comparison', group_id: 'comparison', policy: 'required',
        child_execution_ids: [child.id], required_execution_ids: [child.id], optional_execution_ids: [],
        status: 'waiting', result: {}, state_version: 1, created_at: 'now', updated_at: 'now', completed_at: null,
      }],
    });
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '快速并发比较');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('子系统')).toBeInTheDocument();
    expect(screen.getByText('1 运行 · 0 等待 · 0 完成')).toBeInTheDocument();
    await userEvent.click(screen.getByText('子系统'));
    expect(screen.getByLabelText('子系统汇合状态')).toHaveTextContent('汇合 waiting · 必需 1 · 可选 0');
    expect(screen.queryByRole('region', { name: '可信执行图谱' })).not.toBeInTheDocument();
  });

  it('refreshes published Skills after returning from the Skill library', async () => {
    vi.mocked(listSkills)
      .mockResolvedValueOnce([])
      .mockResolvedValue([helloSkill]);
    render(<App />);

    await waitFor(() => expect(listSkills).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole('button', { name: 'Skills' }));
    await userEvent.click(await screen.findByRole('button', { name: '关闭 Skill 资料库' }));
    await waitFor(() => expect(listSkills).toHaveBeenCalledTimes(2));

    await userEvent.type(screen.getByRole('textbox'), '/hello');
    expect(screen.getByRole('option', { name: /hello-astra/ })).toBeInTheDocument();
  });

  it('keeps the Skill command and highlighted token usable in dark and narrow layouts', async () => {
    globalThis.localStorage?.setItem('astra.theme', 'dark');
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 520 });
    vi.mocked(listSkills).mockResolvedValueOnce([helloSkill]);
    render(<App />);

    const textbox = screen.getByRole('textbox');
    await userEvent.type(textbox, '/hello');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(screen.getByRole('listbox', { name: '快捷操作和 Skill' })).toHaveClass('skill-command-menu');
    await userEvent.click(screen.getByRole('option', { name: /hello-astra/ }));

    expect(document.querySelector('.chat-composer')).toHaveClass('has-skill-tokens');
    expect(screen.getByLabelText('已选择 Skill')).toHaveClass('selected-skill-tokens');
    expect(screen.getByRole('button', { name: '移除 Skill hello-astra' })).toBeVisible();
  });

  it('supports slash keyboard navigation, cancellation, no-results, and Backspace token removal', async () => {
    const authoringSkill: SkillSummary = {
      ...helloSkill,
      id: 'skill-authoring',
      name: 'astra-authoring',
      qualified_identity: 'builtin:astra-authoring',
      origin: 'builtin',
      description: '创建和发布 Skill',
    };
    vi.mocked(listSkills).mockResolvedValueOnce([helloSkill, authoringSkill]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.type(textbox, '/');
    expect(screen.getAllByRole('option')).toHaveLength(2);
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(screen.getByLabelText('已选择 Skill')).toHaveTextContent('hello-astra');
    expect(textbox).toHaveValue('');

    await userEvent.type(textbox, '/missing');
    expect(screen.getByText('没有匹配的操作或 Skill')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(textbox).toHaveValue('/missing');

    await userEvent.clear(textbox);
    await userEvent.keyboard('{Backspace}');
    expect(screen.queryByLabelText('已选择 Skill')).not.toBeInTheDocument();
  });

  it('selects the active slash Skill with Tab and keeps focus in the composer', async () => {
    vi.mocked(listSkills).mockResolvedValueOnce([helloSkill]);
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await waitFor(() => expect(listSkills).toHaveBeenCalled());
    await userEvent.type(textbox, '/hel');
    expect(screen.getByRole('option', { name: /hello-astra/ })).toHaveClass('active');
    await userEvent.keyboard('{Tab}');

    expect(screen.getByLabelText('已选择 Skill')).toHaveTextContent('hello-astra');
    expect(screen.queryByRole('listbox', { name: '快捷操作和 Skill' })).not.toBeInTheDocument();
    expect(textbox).toHaveValue('');
    expect(textbox).toHaveFocus();
  });

  it('shares highlighted Skill state with the attachment menu and retains the draft after submission failure', async () => {
    vi.mocked(listSkills).mockResolvedValueOnce([helloSkill]);
    vi.mocked(createRun).mockRejectedValueOnce(new Error('network unavailable'));
    render(<App />);

    await waitFor(() => expect(listSkills).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: '添加内容' }));
    await userEvent.click(screen.getByRole('button', { name: /hello-astra/ }));
    expect(screen.getByLabelText('已选择 Skill')).toHaveTextContent('hello-astra');

    const textbox = screen.getByRole('textbox');
    await userEvent.type(textbox, '失败后保留');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('服务暂时出现异常，请稍后重试。')).toBeInTheDocument();
    expect(textbox).toHaveValue('失败后保留');
    expect(screen.getByLabelText('已选择 Skill')).toHaveTextContent('hello-astra');

    await userEvent.click(screen.getByRole('button', { name: '新对话' }));
    expect(screen.queryByLabelText('已选择 Skill')).not.toBeInTheDocument();
  });

  it('turns the send button into a stop button and restores it after cancellation', async () => {
    vi.mocked(getRun).mockImplementationOnce(() => new Promise(() => undefined));
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '生成较长回答');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    const stopButton = await screen.findByRole('button', { name: '终止回答' });
    await userEvent.click(stopButton);

    await waitFor(() => expect(cancelRun).toHaveBeenCalledWith('run-1'));
    expect(await screen.findByText('已终止本次运行。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument();
  });

  it('does not submit another message while the current run is active', async () => {
    vi.mocked(getRun).mockImplementationOnce(() => new Promise(() => undefined));
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '正在执行的任务');
    await userEvent.keyboard('{Enter}');
    await screen.findByRole('button', { name: '终止回答' });
    await userEvent.type(screen.getByRole('textbox'), '不应重复提交');
    await userEvent.keyboard('{Enter}');

    expect(createRun).toHaveBeenCalledTimes(1);
  });

  it('opens and closes the mobile navigation drawer through accessible controls', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(document.querySelector('.sidebar')).toHaveClass('mobile-open');
    await userEvent.click(screen.getByRole('button', { name: '关闭导航' }));
    expect(document.querySelector('.sidebar')).not.toHaveClass('mobile-open');
  });

  it('collapses, expands, and keyboard-resizes the desktop sidebar', async () => {
    render(<App />);

    const layout = document.querySelector('.app-layout') as HTMLElement;
    const resizeHandle = screen.getByRole('separator', { name: '调整侧边栏宽度' });
    expect(resizeHandle).toHaveAttribute('aria-valuenow', '260');

    fireEvent.keyDown(resizeHandle, { key: 'ArrowRight' });
    expect(resizeHandle).toHaveAttribute('aria-valuenow', '276');
    expect(layout.style.getPropertyValue('--sidebar-width')).toBe('276px');
    await waitFor(() => expect(window.localStorage.getItem('astra.sidebar-width.v2')).toBe('276'));

    await userEvent.click(screen.getByRole('button', { name: '收起侧边栏' }));
    expect(layout).toHaveClass('sidebar-collapsed');
    expect(screen.queryByRole('separator', { name: '调整侧边栏宽度' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Astra 图标' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开侧边栏' }).closest('aside')).toHaveClass('sidebar');
    expect(screen.getByRole('button', { name: '新对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已分享对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '用量统计' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置' })).toBeInTheDocument();
    await waitFor(() => expect(window.localStorage.getItem('astra.sidebar-collapsed.v2')).toBe('true'));

    await userEvent.click(screen.getByRole('button', { name: '展开侧边栏' }));
    expect(layout).not.toHaveClass('sidebar-collapsed');
    expect(screen.getByRole('separator', { name: '调整侧边栏宽度' })).toHaveAttribute('aria-valuenow', '276');
  });

  it('remembers a stop request while run creation is pending and allows a follow-up', async () => {
    let resolveCreate: ((value: { run_id: string; task_id: string; status: string; answer_mode?: 'standard' | 'trusted' }) => void) | undefined;
    vi.mocked(createRun).mockImplementationOnce(() => new Promise((resolve) => { resolveCreate = resolve; }));
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '创建期间终止');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await userEvent.click(await screen.findByRole('button', { name: '终止回答' }));
    await act(async () => { resolveCreate?.({ run_id: 'run-pending', task_id: 'task-1', status: 'created' }); });

    await waitFor(() => expect(cancelRun).toHaveBeenCalledWith('run-pending'));
    await userEvent.type(screen.getByRole('textbox'), '继续追问');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(createRun).toHaveBeenLastCalledWith('继续追问', 'task-1', 'standard', expect.anything(), expect.anything(), undefined));
  });

  it('promotes the primary referenced output and keeps remaining evidence supplementary', async () => {
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const firstFinding = await screen.findByText('第一个结论');
    const secondFinding = screen.getByText('第二个结论');
    const chart = screen.getByRole('img', { name: 'chart.png' });
    const html = screen.getByTitle('chart.html');
    expect(chart.compareDocumentPosition(firstFinding) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(firstFinding.compareDocumentPosition(secondFinding) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(secondFinding.compareDocumentPosition(html) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelectorAll('#artifact-output-a-chart')).toHaveLength(1);
    expect(screen.getAllByRole('link', { name: '查看主要结果' })[0]).toHaveAttribute('href', '#artifact-output-a-chart');
    expect(screen.queryByText('其他输出')).not.toBeInTheDocument();
    expect(screen.getByText('附件')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '1 个输出 · 查看输出' })).toHaveAttribute('href', '#artifact-output-a-chart');
  });

  it('promotes one unreferenced preview and unifies remaining files under supplementary information', async () => {
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('附加信息')).toBeInTheDocument();
    expect(screen.getByText('附件')).toBeInTheDocument();
    const primaryCards = [...document.querySelectorAll('.primary-result-output .artifact-card')];
    expect(primaryCards.map((card) => card.id)).toEqual(['artifact-output-a-chart']);
    const cards = [...document.querySelectorAll('.answer-supplementary-flat .artifact-card')];
    expect(cards.map((card) => card.id)).toEqual([
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

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
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('附加信息')).toBeInTheDocument();
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
        onEvent({ type: 'answer.content.completed', payload: { background_verification: true } });
      }, 0);
      window.setTimeout(() => onEvent({ type: 'answer.completed', payload: { content: '流式回答不会消失' } }), 300);
      return () => undefined;
    });
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '竞态测试');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const answerArticle = (await screen.findByText('流式回答不会消失')).closest('article');
    expect(answerArticle).not.toBeNull();
    expect(screen.getByText('后台校验中')).toBeInTheDocument();
    expect(screen.getByText('流式回答不会消失').closest('article')).not.toHaveClass('streaming-message');
    await new Promise((resolve) => window.setTimeout(resolve, 200));
    expect(screen.getByText('流式回答不会消失')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('已完成查询')).toBeInTheDocument(), { timeout: 4000 });
    expect(screen.getByText('已完成查询').closest('article')).toBe(answerArticle);
    expect(screen.queryByText('流式回答不会消失')).not.toBeInTheDocument();
    vi.mocked(getRun).mockResolvedValue(finalSnapshot);
    vi.mocked(streamRunEvents).mockImplementation(() => () => undefined);
  });

  it('keeps live reasoning collapsed by default and preserves the current panel through answer updates', async () => {
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const summary = await screen.findByText('思考中');
    const panel = summary.closest('details');
    expect(panel).not.toHaveAttribute('open');
    expect(screen.queryByText('实时更新')).not.toBeInTheDocument();
    expect(summary.querySelectorAll('.process-thinking-dots i')).toHaveLength(3);
    expect(panel?.querySelector('summary .process-loading-pane')).not.toBeInTheDocument();
    expect(panel?.querySelector('.process-step.status-running')).toBeInTheDocument();
    expect(panel?.querySelector('.process-live-dot')).not.toBeInTheDocument();
    await waitFor(() => expect(emit).toBeTypeOf('function'));
    const snapshotCalls = vi.mocked(getRun).mock.calls.length;

    act(() => {
      emit?.({ id: 9, type: 'reasoning.summary.delta', payload: { turn_index: 0, delta: '正在理解问题' } });
    });
    expect(panel).not.toHaveAttribute('open');
    await waitFor(() => expect(panel?.querySelector('summary .process-live-preview')).toHaveTextContent('正在理解问题'));
    act(() => {
      emit?.({ type: 'reasoning.summary.completed', payload: { turn_index: 0, summary: '正在理解问题' } });
    });

    await userEvent.click(summary);
    expect(panel).toHaveAttribute('open');
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem('astra.process-panel-default-open.v2') ?? 'false')).toBe(true));

    act(() => {
      emit?.({ id: 10, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 1 } });
      emit?.({ id: 11, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '正在选择可靠来源' } });
    });
    expect(await screen.findByText('正在选择可靠来源')).toBeInTheDocument();
    const decisionGroup = panel?.querySelector('[data-process-group="phase-selecting_action-1"]');
    expect(decisionGroup).not.toBeInTheDocument();
    expect(screen.getByText('正在选择可靠来源').closest('.process-step')).toHaveClass('process-reasoning');
    expect(panel?.querySelector('.process-step.status-running')).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 150));
    expect(vi.mocked(getRun)).toHaveBeenCalledTimes(snapshotCalls);

    act(() => {
      emit?.({ id: 12, type: 'tool_call.started', payload: { tool_call_id: 'call-live', tool_name: 'web_search' } });
      emit?.({ id: 13, type: 'tool_call.completed', payload: { tool_call_id: 'call-live', tool_name: 'web_search', status: 'succeeded' } });
    });
    expect(screen.queryByText('正在评估执行结果')).not.toBeInTheDocument();
    expect(await screen.findByText('web_search')).toBeInTheDocument();
    expect(panel?.querySelectorAll('.process-step.status-running')).toHaveLength(1);

    act(() => emit?.({ id: 14, type: 'reasoning.phase.started', payload: { phase: 'selecting_action', turn_index: 2 } }));
    expect(panel?.querySelector('[data-process-group="phase-selecting_action-2"]')).not.toBeInTheDocument();
    expect(screen.queryByText('正在评估执行结果')).not.toBeInTheDocument();
    expect(panel?.querySelectorAll('.process-step.status-running')).toHaveLength(1);

    act(() => emit?.({ id: 15, type: 'answer.delta', payload: { delta: '开始回答' } }));
    expect(await screen.findByText('开始回答')).toBeInTheDocument();
    expect(panel).toHaveAttribute('open');

    await userEvent.click(summary);
    expect(panel).not.toHaveAttribute('open');
    act(() => emit?.({ id: 16, type: 'reasoning.summary.delta', payload: { turn_index: 1, delta: '并继续验证' } }));
    await waitFor(() => expect(panel?.querySelector('summary .process-live-preview')).toHaveTextContent('正在选择可靠来源并继续验证'));
    expect(panel).not.toHaveAttribute('open');
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem('astra.process-panel-default-open.v2') ?? 'true')).toBe(false));

    vi.mocked(getRun).mockResolvedValue(finalSnapshot);
    vi.mocked(streamRunEvents).mockImplementation(() => () => undefined);
  });

  it('changes only the clicked panel and uses that choice for the next new panel', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '第一条');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const firstSummary = await screen.findByText('思考完成');
    const firstPanel = firstSummary.closest('details');
    expect(firstPanel).not.toHaveAttribute('open');
    await userEvent.click(firstSummary);
    expect(firstPanel).toHaveAttribute('open');

    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-2', task_id: 'task-1', status: 'created' });
    vi.mocked(getRun).mockResolvedValueOnce({
      ...snapshot,
      id: 'run-2',
      task_id: 'task-1',
      chat_messages: [{ id: 'u-2', role: 'user', content: '第二条', status: 'completed', metadata: {} }],
    });
    await userEvent.type(screen.getByRole('textbox'), '第二条');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(screen.getAllByText('思考完成')).toHaveLength(2));
    const summaries = screen.getAllByText('思考完成');
    const firstExistingPanel = summaries[0].closest('details');
    const secondPanel = summaries[1].closest('details');
    expect(firstExistingPanel).toHaveAttribute('open');
    expect(secondPanel).toHaveAttribute('open');

    await userEvent.click(summaries[1]);
    expect(firstExistingPanel).toHaveAttribute('open');
    expect(secondPanel).not.toHaveAttribute('open');

    await userEvent.click(screen.getByRole('button', { name: '新对话' }));
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-3', task_id: 'task-3', status: 'created' });
    vi.mocked(getRun).mockResolvedValueOnce({
      ...snapshot,
      id: 'run-3',
      task_id: 'task-3',
      chat_messages: [{ id: 'u-3', role: 'user', content: '新对话', status: 'completed', metadata: {} }],
    });
    await userEvent.type(screen.getByRole('textbox'), '新对话');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const newConversationSummary = await screen.findByText('思考完成');
    expect(newConversationSummary.closest('details')).not.toHaveAttribute('open');
  });

  it('sends selected reasoning policy with a run', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.type(screen.getByRole('textbox'), '分析复杂问题');
    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    await userEvent.click(screen.getByRole('button', { name: '深入' }));
    expect(screen.queryByRole('slider', { name: '工具调用上限' })).not.toBeInTheDocument();
    expect(screen.getByText('不限')).toBeInTheDocument();
    expect(screen.getByText('深入推理不限制工具调用次数')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(vi.mocked(createRun)).toHaveBeenLastCalledWith(
      expect.any(String),
      undefined,
      'trusted',
      expect.objectContaining({ reasoning_effort: 'deep', max_tool_calls: null }),
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
      'confirm',
    );
  });

  it('restores reasoning preferences but keeps a fresh conversation in quick mode', async () => {
    vi.mocked(getConversationStrategy).mockResolvedValueOnce({
      preferred_answer_mode: 'trusted',
      reasoning_effort: 'deep',
      max_tool_calls: null,
      reflection_enabled: true,
      reflection_trigger: 'every_turn',
    });
    vi.mocked(updateConversationStrategy).mockClear();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('button', { name: '当前模型：gpt-5' })).toHaveTextContent('快速策略 · 工具按需'));
    expect(screen.getByRole('switch', { name: '快速响应' })).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByRole('button', { name: '深入' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: '每轮' })).toHaveClass('active');

    await userEvent.click(screen.getByRole('button', { name: '快速' }));
    await waitFor(() => expect(updateConversationStrategy).toHaveBeenLastCalledWith({
      preferred_answer_mode: 'standard',
      reasoning_effort: 'fast',
      max_tool_calls: 5,
      reflection_enabled: true,
      reflection_trigger: 'every_turn',
    }));

    await userEvent.type(screen.getByRole('textbox'), '使用恢复后的策略');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(createRun).toHaveBeenLastCalledWith(
      expect.any(String),
      undefined,
      'trusted',
      expect.objectContaining({
        reasoning_effort: 'fast',
        max_tool_calls: 5,
        reflection_enabled: true,
        reflection_trigger: 'every_turn',
      }),
      expect.any(Object),
      'confirm',
    );
  });

  it('shows validation error for empty goal', async () => {
    render(<App />);
    const textbox = screen.getByRole('textbox');

    await userEvent.clear(textbox);
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

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

  it('replaces background activity with a new-message reminder that clears when opened', async () => {
    vi.useFakeTimers();
    const now = new Date().toISOString();
    const running = {
      id: 'background-chat', title: '后台任务', title_source: 'auto', pinned_at: null,
      created_at: now, updated_at: now, last_run_status: 'running', last_message_preview: '', has_active_share: false,
    };
    vi.mocked(listConversations)
      .mockResolvedValueOnce([running])
      .mockResolvedValueOnce([{ ...running, last_run_status: 'completed' }]);

    try {
      render(<App />);
      await act(async () => { await Promise.resolve(); });

      expect(screen.getByTestId('conversation-status-background-chat')).toHaveClass('running');
      expect(screen.getByRole('img', { name: '运行中' })).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(1500);
        await Promise.resolve();
      });

      const reminder = screen.getByTestId('conversation-status-background-chat');
      expect(reminder).toHaveClass('unread');
      expect(screen.queryByRole('img', { name: '已完成' })).not.toBeInTheDocument();

      fireEvent.click(reminder);
      expect(screen.queryByTestId('conversation-status-background-chat')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('separates pinned conversations and manages rename and delete with dialogs', async () => {
    const now = new Date().toISOString();
    vi.mocked(listConversations).mockResolvedValueOnce([
      { id: 'pinned', title: '重要对话', title_source: 'user', pinned_at: now, created_at: now, updated_at: now, last_run_status: 'completed', last_message_preview: '', has_active_share: false },
      { id: 'recent', title: '普通对话', title_source: 'auto', pinned_at: null, created_at: now, updated_at: now, last_run_status: 'completed', last_message_preview: '', has_active_share: false },
    ]);
    render(<App />);

    expect(await screen.findByText('置顶')).toBeInTheDocument();
    expect(screen.getByText('最近')).toBeInTheDocument();
    const moreButton = screen.getByRole('button', { name: '更多操作 重要对话' });
    await userEvent.click(moreButton);
    expect(moreButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menu')).toHaveClass('history-menu-portal');
    expect(screen.getByRole('menu').parentElement).toBe(document.body);
    expect(screen.getByRole('menu').style.left).toMatch(/px$/);
    expect(screen.getByRole('menu').style.top).toMatch(/px$/);
    expect(screen.getAllByRole('menuitem')).toHaveLength(4);
    act(() => document.dispatchEvent(new Event('scroll')));
    expect(moreButton).toHaveAttribute('aria-expanded', 'false');

    await userEvent.click(moreButton);
    await userEvent.click(screen.getByRole('heading', { name: 'Navigate Ideas. Create Reality.' }));
    expect(moreButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    await userEvent.click(moreButton);
    await userEvent.keyboard('{Escape}');
    expect(moreButton).toHaveAttribute('aria-expanded', 'false');

    await userEvent.click(moreButton);
    act(() => window.dispatchEvent(new Event('blur')));
    expect(moreButton).toHaveAttribute('aria-expanded', 'false');

    await userEvent.click(moreButton);
    await userEvent.click(screen.getByRole('menuitem', { name: '重命名' }));
    const input = screen.getByRole('dialog', { name: '重命名对话' }).querySelector('input') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '新的标题{Enter}');
    await waitFor(() => expect(updateConversation).toHaveBeenCalledWith('pinned', { title: '新的标题' }));

    await userEvent.click(screen.getByRole('button', { name: '更多操作 普通对话' }));
    await userEvent.click(screen.getByRole('menuitem', { name: '删除' }));
    expect(screen.getByRole('dialog', { name: '删除对话' })).toHaveTextContent('无法撤销');
    await userEvent.click(screen.getByRole('button', { name: '永久删除' }));
    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith('recent'));
  });

  it('lists active shares and opens the original conversation', async () => {
    const now = new Date().toISOString();
    vi.mocked(listConversationShares).mockResolvedValueOnce([{ conversation_id: 'shared-1', title: '分享测试', url: '/share/token-1', created_at: now, updated_at: now, message_count: 4 }]);
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /已分享对话/ }));
    expect(await screen.findByRole('heading', { name: '已分享对话' })).toBeInTheDocument();
    expect(screen.getByText(/4 条消息/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '打开分享页' })).toHaveAttribute('href', '/share/token-1');

    await userEvent.click(screen.getByRole('button', { name: '查看原对话' }));
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith('shared-1', expect.any(AbortSignal)));
  });

  it('groups and sorts files in the independent library view', async () => {
    const now = new Date().toISOString();
    vi.mocked(listLibraryDeliverables).mockResolvedValueOnce([
      { id: 'file-image', job_id: null, schedule_run_id: null, run_id: 'run-1', task_id: 'conversation-1', conversation_title: '图表任务', kind: 'file', title: 'chart.png', summary: 'outputs/chart.png', mime_type: 'image/png', size_bytes: 2048, content_url: '/api/files/chart', external_url: null, metadata: {}, created_at: now },
      { id: 'file-doc', job_id: 'job-1', job_name: '日报', job_kind: 'agent', schedule_run_id: 'scheduled-run-1', trigger_type: 'scheduled', run_id: 'run-2', task_id: 'conversation-2', conversation_title: '报告任务', kind: 'file', title: 'summary.pdf', summary: 'reports/summary.pdf', mime_type: 'application/pdf', size_bytes: 8192, content_url: '/api/files/report', external_url: null, metadata: {}, created_at: now },
      { id: 'result:scheduled-run-1', job_id: 'job-1', job_name: '日报', job_kind: 'agent', schedule_run_id: 'scheduled-run-1', trigger_type: 'scheduled', run_id: 'run-2', task_id: 'conversation-2', conversation_title: '报告任务', kind: 'result', title: '执行结果', summary: '日报生成完成', mime_type: null, size_bytes: null, content_url: null, external_url: null, metadata: {}, created_at: now },
    ]);
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '资料库' }));
    expect(await screen.findByRole('heading', { name: '资料库' })).toBeInTheDocument();
    expect(screen.getByText('chart.png')).toBeInTheDocument();
    expect(screen.getByText('summary.pdf')).toBeInTheDocument();
    expect(screen.getByText('日报生成完成')).toBeInTheDocument();
    expect(document.querySelector('a[href="/api/files/chart"]')).toHaveTextContent('打开');
    expect(document.querySelector('.library-groups')).toHaveClass('view-gallery');
    expect(screen.getByRole('button', { name: '时间' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '画廊视图' })).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(screen.getByRole('button', { name: '列表视图' }));
    expect(document.querySelector('.library-groups')).toHaveClass('view-list');
    expect(screen.getByRole('button', { name: '列表视图' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: '画廊视图' }));
    expect(document.querySelector('.library-groups')).toHaveClass('view-gallery');

    await userEvent.click(screen.getByRole('button', { name: '类型' }));
    expect(screen.getByRole('button', { name: '类型' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('heading', { name: '图片' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '文档' })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '资料库排序' }), 'size_desc');
    expect(screen.getByRole('combobox', { name: '资料库排序' })).toHaveValue('size_desc');
  });

  it('renders the library navigation and controls in English', async () => {
    window.localStorage.setItem('astra.language', 'en');
    vi.mocked(listLibraryDeliverables).mockResolvedValueOnce([]);
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: 'Library' }));
    expect(await screen.findByRole('heading', { name: 'Library' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search deliverables, tasks, or chats')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gallery view' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('combobox', { name: 'Library sort' })).toHaveValue('updated_desc');
    expect(document.documentElement).toHaveAttribute('lang', 'en');
  });

  it('updates and revokes selected shares in batches', async () => {
    const now = new Date().toISOString();
    const share = { conversation_id: 'shared-1', title: '分享测试', url: '/share/token-1', created_at: now, updated_at: now, message_count: 4 };
    vi.mocked(listConversationShares)
      .mockResolvedValueOnce([share])
      .mockResolvedValueOnce([{ ...share, updated_at: new Date(Date.now() + 1000).toISOString() }])
      .mockResolvedValueOnce([]);
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /已分享对话/ }));
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 分享测试' }));
    await userEvent.click(screen.getByRole('button', { name: '更新快照' }));
    await waitFor(() => expect(createConversationShare).toHaveBeenCalledWith('shared-1', true));
    expect(await screen.findByText('已更新 1 个分享快照。')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '取消分享' }));
    expect(screen.getByRole('alertdialog', { name: '取消分享链接？' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '确认取消分享' }));
    await waitFor(() => expect(revokeConversationShare).toHaveBeenCalledWith('shared-1'));
    expect(await screen.findByText('暂无已分享对话')).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText('已完成查询');
    await userEvent.type(screen.getByRole('textbox'), '继续追问{Enter}');
    await screen.findAllByText('已完成查询');

    expect(vi.mocked(createRun)).toHaveBeenLastCalledWith('继续追问', 'task-1', 'standard', expect.objectContaining({
      reasoning_effort: 'balanced',
      max_tool_calls: 8,
      reflection_enabled: true,
      execution_mode: 'request_approval',
    }), expect.objectContaining({ provider: 'openai', name: 'gpt-5' }), undefined);
    expect(screen.getAllByRole('button', { name: '查询 Astra' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /跳转到问题/ })).toHaveLength(2);
    const firstQuestion = screen.getByRole('button', { name: '跳转到问题 1' });
    const secondQuestion = screen.getByRole('button', { name: '跳转到问题 2' });
    expect(secondQuestion).toHaveAttribute('aria-current', 'true');
    await userEvent.click(firstQuestion);
    expect(firstQuestion).toHaveAttribute('aria-current', 'true');
    expect(secondQuestion).not.toHaveAttribute('aria-current');
  });

  it('jumps on pointer down before streaming layout changes can remove the button', async () => {
    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '生成一段较长回答');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText('已完成查询');

    const conversation = document.querySelector<HTMLElement>('.conversation');
    expect(conversation).not.toBeNull();
    Object.defineProperties(conversation!, {
      scrollHeight: { configurable: true, value: 2400 },
      clientHeight: { configurable: true, value: 600 },
      scrollTop: { configurable: true, writable: true, value: 200 },
    });
    const scrollTo = vi.fn();
    Object.defineProperty(conversation!, 'scrollTo', { configurable: true, value: scrollTo });
    fireEvent.scroll(conversation!);

    const jumpButton = screen.getByRole('button', { name: '回到最新' });
    fireEvent.pointerDown(jumpButton, { button: 0, pointerType: 'touch' });

    expect(scrollTo).toHaveBeenCalledWith({ top: 2400, behavior: 'smooth' });
    expect(screen.queryByRole('button', { name: '回到最新' })).not.toBeInTheDocument();
  });

  it('keeps one independently controlled process panel when resuming a clarification', async () => {
    const completedFixture = await vi.mocked(getRun)('fixture');
    const waitingSnapshot: RunView = {
      ...completedFixture,
      id: 'run-clarification',
      task_id: 'task-clarification',
      status: 'waiting_user',
      summary: '请告诉我你希望我完成的具体任务或问题。',
      result: null,
      waiting_state: {
        paused_node: 'select_action',
        continuation_token: 'continue-clarification',
        request: '请告诉我你希望我完成的具体任务或问题。',
      },
      events: [
        { id: 1, type: 'reasoning.summary.completed', payload: { turn_index: 1, summary: '需要澄清用户意图' }, created_at: 'now' },
        { id: 2, type: 'run.waiting_user', payload: { request: '请告诉我你希望我完成的具体任务或问题。' }, created_at: 'now' },
      ],
      chat_messages: [
        { id: 'clarification-user-1', role: 'user', content: '！', status: 'completed', metadata: {} },
        { id: 'clarification-question', role: 'assistant', content: '请告诉我你希望我完成的具体任务或问题。', status: 'waiting_user', metadata: {} },
      ],
    };
    const resumedSnapshot: RunView = {
      ...completedFixture,
      id: 'run-clarification',
      task_id: 'task-clarification',
      status: 'completed',
      summary: '明白了，你是在打招呼。你好！',
      result: { ...completedFixture.result!, summary: '明白了，你是在打招呼。你好！' },
      waiting_state: null,
      events: [
        ...waitingSnapshot.events,
        { id: 3, type: 'run.resumed', payload: { observation: { kind: 'user_response', summary: '只是在打招呼' } }, created_at: 'now' },
        { id: 4, type: 'reasoning.summary.completed', payload: { turn_index: 2, summary: '根据澄清直接回答' }, created_at: 'now' },
      ],
      chat_messages: [
        { id: 'clarification-user-1', role: 'user', content: '！', status: 'completed', metadata: {} },
        { id: 'clarification-question', role: 'assistant', content: '请告诉我你希望我完成的具体任务或问题。', status: 'ask_user', metadata: {} },
        { id: 'clarification-user-2', role: 'user', content: '只是在打招呼', status: 'completed', metadata: {} },
        { id: 'clarification-answer', role: 'assistant', content: '明白了，你是在打招呼。你好！', status: 'completed', metadata: {} },
      ],
    };
    vi.mocked(createRun).mockResolvedValueOnce({
      run_id: waitingSnapshot.id,
      task_id: waitingSnapshot.task_id,
      status: 'created',
      answer_mode: 'standard',
    });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValueOnce(waitingSnapshot).mockResolvedValue(resumedSnapshot);
    vi.mocked(resumeRun).mockResolvedValueOnce({
      run_id: waitingSnapshot.id,
      task_id: waitingSnapshot.task_id,
      status: 'executing',
    });

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '！');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('请告诉我你希望我完成的具体任务或问题。')).toBeInTheDocument();
    expect(document.querySelectorAll('.process-panel')).toHaveLength(1);

    await userEvent.type(screen.getByRole('textbox'), '只是在打招呼');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith(
      waitingSnapshot.id,
      '只是在打招呼',
      'continue-clarification',
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
    ));
    expect(await screen.findByText('明白了，你是在打招呼。你好！')).toBeInTheDocument();
    expect(screen.getAllByText('请告诉我你希望我完成的具体任务或问题。')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /跳转到问题/ })).toHaveLength(2);
    expect(document.querySelectorAll('.process-panel')).toHaveLength(1);
    expect(screen.getAllByText('思考完成')).toHaveLength(1);

    const processSummary = screen.getByText('思考完成').closest('summary');
    const processPanel = processSummary?.closest('details');
    expect(processPanel).not.toHaveAttribute('open');
    await userEvent.click(processSummary!);
    expect(processPanel).toHaveAttribute('open');

    vi.mocked(getRun).mockResolvedValue(completedFixture);
    vi.mocked(resumeRun).mockResolvedValue({ run_id: 'run-1', task_id: 'task-1', status: 'executing' });
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
    expect(screen.getByText('Swarm / 子 Agent')).toBeInTheDocument();
    expect(screen.queryByText('需要先启用受治理子 Agent 执行。')).not.toBeInTheDocument();
    expect(screen.queryByText('关闭 Swarm 会立即阻止创建新的子 Agent，但不会取消已经创建的子 Agent。')).not.toBeInTheDocument();
    expect(screen.getByText('需要先启用安全运行环境。')).toBeInTheDocument();
    const searchSwitch = screen.getByRole('switch', { name: /Web Search/ });
    await userEvent.click(searchSwitch);
    await waitFor(() => expect(updateToolSettings).toHaveBeenCalled());
    expect(searchSwitch).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByText('工具已启用，将用于之后新建的任务。')).not.toBeInTheDocument();
    expect(screen.queryByText('工具已停用，之后新建的任务不会调用它。')).not.toBeInTheDocument();
    expect(screen.queryByText('设置已保存，并会应用于之后创建的任务。')).not.toBeInTheDocument();
    const swarmSwitch = screen.getByRole('switch', { name: /Swarm \/ 子 Agent/ });
    await userEvent.click(swarmSwitch);
    await waitFor(() => expect(updateToolSettings).toHaveBeenLastCalledWith(
      expect.arrayContaining([expect.objectContaining({ name: 'swarm', enabled: false })]),
    ));
    expect(swarmSwitch).toHaveAttribute('aria-checked', 'false');
  });

  it('manages model providers and keeps API credentials masked by default', async () => {
    window.localStorage.removeItem('astra.model-providers.v2');
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '模型管理' }));

    expect(screen.getByRole('heading', { name: '模型管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Anthropic/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Google Gemini/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /DeepSeek/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /通义千问/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SiliconFlow/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Ollama/ })).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: '搜索供应商' }), 'Groq');
    expect(screen.getByRole('button', { name: /Groq/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /DeepSeek/ })).not.toBeInTheDocument();
    await userEvent.clear(screen.getByRole('textbox', { name: '搜索供应商' }));
    const keyInput = screen.getByPlaceholderText('sk-...');
    expect(keyInput).toHaveAttribute('type', 'password');
    expect(screen.queryByLabelText('gpt-5 窗口来源')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('gpt-5 上下文窗口')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('gpt-5 最大输出')).not.toBeInTheDocument();
    expect((await screen.findAllByText('上下文上限')).length).toBeGreaterThan(0);
    expect(screen.queryByText('官方模型目录')).not.toBeInTheDocument();
    expect(screen.queryByText('保守回退')).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /查看模型说明/ })[0]).toHaveAttribute(
      'href',
      'https://developers.openai.com/api/docs/models/gpt-5',
    );
    expect(resolveModelContextCapabilities).toHaveBeenCalled();
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem('astra.model-providers.v2') ?? '[]');
      expect(saved.find((item: { id: string }) => item.id === 'openai').models[0]).toEqual({ id: 'gpt-5' });
    });

    await userEvent.type(keyInput, 'secret-key');
    await userEvent.click(screen.getByRole('button', { name: '显示' }));
    expect(keyInput).toHaveAttribute('type', 'text');
    expect(screen.getByText('更改会自动保存到当前浏览器。')).toBeInTheDocument();
  });

  it('tests a configured model connection and shows the measured result', async () => {
    window.localStorage.removeItem('astra.model-providers.v2');
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '模型管理' }));
    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'connection-key');
    await userEvent.click(screen.getAllByRole('button', { name: '测试连接' })[0]);

    await waitFor(() => expect(testModelConnection).toHaveBeenCalledWith({
      provider: 'openai',
      name: 'gpt-5',
      api_key: 'connection-key',
      base_url: 'https://api.openai.com/v1',
    }));
    expect(await screen.findByRole('status')).toHaveTextContent('连接成功，模型已响应测试请求。 · 42 ms');
  });

  it('rejects obsolete model profile entries and never sends removed context overrides', async () => {
    window.localStorage.setItem('astra.model-providers.v2', JSON.stringify([
      {
        id: 'openai',
        name: 'OpenAI',
        enabled: true,
        endpoint: 'https://api.openai.com/v1',
        models: [
          { id: 'gpt-5', contextMode: 'manual', contextWindowTokens: 160000, maxOutputTokens: 64000 },
          'gpt-5-mini',
        ],
        apiKey: 'unit-test-key',
      },
    ]));
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '创建上下文');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(getConversationContext).toHaveBeenCalled());
    const contextCalls = vi.mocked(getConversationContext).mock.calls;
    expect(contextCalls[contextCalls.length - 1]).toHaveLength(5);
    expect(contextCalls[contextCalls.length - 1]?.[4]).toBeInstanceOf(AbortSignal);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '模型管理' }));
    expect(screen.queryByText('手动上限')).not.toBeInTheDocument();
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem('astra.model-providers.v2') ?? '[]');
      expect(saved.find((item: { id: string }) => item.id === 'openai').models).toEqual([
        { id: 'gpt-5' },
      ]);
    });
  });

  it('restores conversation history and model credentials after remount', async () => {
    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '持久化测试');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText('已完成查询');
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.clear(screen.getByPlaceholderText('sk-...'));
    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'persisted-secret');

    cleanup();
    render(<App />);

    expect(screen.getByRole('button', { name: '持久化测试' })).toBeInTheDocument();
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

    expect(await screen.findByRole('button', { name: '历史会话 8' })).toBeInTheDocument();
    expect(screen.getByText('最多显示最近 100 个会话')).toBeInTheDocument();
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

    const failed = await screen.findByRole('button', { name: '失败记录' });
    const empty = screen.getByRole('button', { name: '空数组记录' });
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
    await userEvent.type(screen.getByPlaceholderText('sk-...'), 'deepseek-test-key');
    await userEvent.click(screen.getByRole('button', { name: '关闭设置' }));
    await userEvent.click(screen.getByRole('button', { name: /当前模型/ }));

    expect(screen.getByRole('button', { name: /deepseek-v4-pro/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /deepseek-v4-flash/ })).toBeInTheDocument();
  });

  it('shows sandbox and execution policies in runtime settings', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '运行时' }));

    expect(screen.getByRole('heading', { name: '安全运行环境' })).toBeInTheDocument();
    expect(screen.getByText('隔离环境 · 已就绪')).toBeInTheDocument();
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
    expect(screen.queryByText('当前镜像')).not.toBeInTheDocument();
  });

  it('edits, saves, and restores the runtime Agent Profile', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Agent' }));
    const identity = await screen.findByLabelText('IDENTITY.md');
    fireEvent.change(identity, { target: { value: '# Astra Identity\n\n## Identity\nCustomized' } });

    expect(screen.getByText('有未保存修改')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保存 Agent Profile' }));
    await waitFor(() => expect(updateRuntimeAgentProfile).toHaveBeenCalledWith(expect.objectContaining({ identity: expect.stringContaining('Customized') })));
    expect(await screen.findByText('用户配置')).toBeInTheDocument();
    expect(screen.getByText('Agent Profile 已保存，将应用于之后新建的任务。')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '恢复内置默认' }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(resetRuntimeAgentProfile).toHaveBeenCalled());
    expect((identity as HTMLTextAreaElement).value).toContain('Default');
  });

  it('preserves unsaved Agent Profile text when validation fails', async () => {
    vi.mocked(updateRuntimeAgentProfile).mockRejectedValueOnce(new Error('IDENTITY.md 缺少必需章节'));
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Agent' }));
    const identity = await screen.findByLabelText('IDENTITY.md');
    fireEvent.change(identity, { target: { value: 'invalid profile text' } });
    await userEvent.click(screen.getByRole('button', { name: '保存 Agent Profile' }));

    expect(await screen.findByRole('status')).toHaveTextContent('IDENTITY.md 缺少必需章节');
    expect(identity).toHaveValue('invalid profile text');
    expect(screen.getByRole('button', { name: '保存 Agent Profile' })).toBeEnabled();
  });

  it('separates Agent, Runtime, Memory, and experimental improvement settings', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));

    await userEvent.click(screen.getByRole('button', { name: '运行时' }));
    expect(screen.getByRole('heading', { name: '安全运行环境' })).toBeInTheDocument();
    expect(screen.queryByLabelText('IDENTITY.md')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Agent' }));
    expect(await screen.findByLabelText('IDENTITY.md')).toBeInTheDocument();
    expect(screen.getByText(/不会开启记忆、工具或后台作业/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '记忆' }));
    expect(screen.getByRole('tab', { name: '记忆设置' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '已保存的记忆' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '整理与合并' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '活动与审计' })).not.toBeInTheDocument();
    expect(screen.queryByText('自动应用改进：关闭')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '实验功能' }));
    expect(screen.getByRole('heading', { name: 'Agent 改进' })).toBeInTheDocument();
    expect(screen.getByText(/不会自动改变正式运行行为/)).toBeInTheDocument();
  });

  it('saves enforced Memory runtime settings and preserves failed edits', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '记忆' }));

    const recallToggle = await screen.findByRole('switch', { name: '持久记忆召回' });
    await userEvent.click(recallToggle);
    fireEvent.change(screen.getByLabelText('每次最多召回'), { target: { value: '5' } });
    await userEvent.click(screen.getByRole('button', { name: '保存记忆设置' }));
    await waitFor(() => expect(updateRuntimeMemorySettings).toHaveBeenCalledWith(expect.objectContaining({ recall_enabled: true, retrieval_max_items: 5 })));
    expect(screen.getByText('记忆设置已保存，将应用于之后新建的任务。')).toBeInTheDocument();

    vi.mocked(updateRuntimeMemorySettings).mockRejectedValueOnce(new Error('最低相关度无效'));
    fireEvent.change(screen.getByLabelText('最低相关度'), { target: { value: '0.3' } });
    await userEvent.click(screen.getByRole('button', { name: '保存记忆设置' }));
    expect(await screen.findByRole('status')).toHaveTextContent('最低相关度无效');
    expect(screen.getByLabelText('最低相关度')).toHaveValue(0.3);
    expect(screen.getByRole('button', { name: '保存记忆设置' })).toBeEnabled();
  });

  it('exits loading and can retry when an older backend omits Memory settings', async () => {
    vi.mocked(getRuntimeProfile).mockResolvedValueOnce({
      dependencies: [],
      core_dependencies: [],
      active_image: 'astra-data-viz:0.1.0',
      dependency_digest: 'base',
      build: null,
    });
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '记忆' }));

    expect(await screen.findByRole('status')).toHaveTextContent('当前后端尚未提供记忆设置，请重启 Astra 后重试。');
    expect(screen.queryByText('正在读取记忆设置…')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('switch', { name: '持久记忆召回' })).toBeInTheDocument();
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

  it('does not expose validation settings and keeps data settings task agnostic', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    expect(screen.queryByRole('button', { name: '验证与安全' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '数据与隐私' }));
    expect(screen.getByText('工具内容保留')).toBeInTheDocument();
    expect(screen.queryByText('保存抓取正文')).not.toBeInTheDocument();
  });

  it('searches settings and tabs with exact and fuzzy matching', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    const search = screen.getByRole('combobox', { name: '搜索设置' });

    await userEvent.type(search, '主题模式');
    await userEvent.click(screen.getByRole('option', { name: /主题模式.*界面/ }));
    expect(screen.getByRole('button', { name: '界面' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByDisplayValue('跟随系统')).toBeInTheDocument();

    await userEvent.type(search, '主题模时');
    expect(screen.getByRole('option', { name: /主题模式.*界面/ })).toBeInTheDocument();
    await userEvent.clear(search);
    await userEvent.type(search, '子 Agent');
    await userEvent.click(screen.getByRole('option', { name: /Swarm \/ 子 Agent.*工具/ }));
    expect(screen.getByRole('button', { name: '工具' })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByText('Swarm / 子 Agent')).toBeInTheDocument();
    await userEvent.clear(search);
    await userEvent.type(search, '运行时');
    await userEvent.click(screen.getByRole('option', { name: /运行时.*Tab/ }));
    expect(screen.getByRole('button', { name: '运行时' })).toHaveAttribute('aria-current', 'page');
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
    await userEvent.click(screen.getByRole('switch', { name: 'Quick response' }));
    const modelSelector = screen.getByRole('button', { name: 'Current model: gpt-5' });
    expect(modelSelector).toHaveTextContent('Balanced · 8 tool calls · Adaptive reflection');
    await userEvent.click(modelSelector);
    expect(screen.getByText('8 calls')).toBeInTheDocument();
    expect(screen.getByText('Adjustable range for this effort: 6–15')).toBeInTheDocument();
    expect(screen.queryByText('8 次')).not.toBeInTheDocument();
    await userEvent.click(modelSelector);
    await userEvent.click(screen.getByRole('button', { name: /^Usage/ }));
    expect(screen.getByRole('dialog', { name: 'Usage' })).toBeInTheDocument();
    expect(await screen.findByText('Total tokens')).toBeInTheDocument();
    expect(screen.getByText('Usage data completeness 100%')).toBeInTheDocument();
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

    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));

    expect(screen.queryByText('规划策略')).not.toBeInTheDocument();
    expect(screen.getByText('触发方式')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '深入' })).toBeInTheDocument();
    expect(screen.queryByText('最大 Agent 轮次')).not.toBeInTheDocument();
    expect(screen.getByText('工具调用上限')).toBeInTheDocument();
    expect(screen.getByText('当前强度可调整范围：6–15 次')).toBeInTheDocument();
  });

  it('keeps model thinking independent from quick and trusted mode controls', async () => {
    render(<App />);

    await waitFor(() => expect(resolveModelThinkingCapabilities).toHaveBeenCalled());
    const selector = screen.getByRole('button', { name: '当前模型：gpt-5' });
    expect(selector).toHaveAccessibleDescription(/模型思考 · 中/);
    await userEvent.click(selector);
    const thinkingSwitch = screen.getByRole('switch', { name: '模型思考' });
    expect(thinkingSwitch).toBeChecked();
    expect(thinkingSwitch).toBeDisabled();
    expect(thinkingSwitch).not.toHaveAccessibleDescription();
    expect(screen.queryByText('此模型始终启用扩展思考')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '高' }));
    expect(screen.getByRole('button', { name: '高' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('当前深度可能显著增加首字和总响应延迟，但不会启用可信模式。')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByRole('button', { name: '高' })).toHaveClass('active');
    expect(screen.queryByText('当前深度将用于本次运行的多次模型调用，可能增加耗时与用量。')).not.toBeInTheDocument();
    expect(document.querySelector('.model-thinking-impact')).not.toBeInTheDocument();
  });

  it('persists optional model thinking per model and sends it independently from agent effort', async () => {
    window.localStorage.setItem('astra.model-providers.v2', JSON.stringify([
      { id: 'openai', name: 'OpenAI', enabled: false, endpoint: 'https://api.openai.com/v1', models: [{ id: 'gpt-5' }], apiKey: '' },
      { id: 'qwen', name: '通义千问', enabled: true, endpoint: 'https://example.test/v1', models: [{ id: 'qwen3.7-plus' }, { id: 'qwen-plus' }], apiKey: 'secret' },
    ]));
    window.localStorage.setItem('astra.selected-model.v2', 'qwen:qwen3.7-plus');
    window.localStorage.setItem('astra.model-thinking-preferences.v2', JSON.stringify({
      'qwen:qwen3.7-plus': { enabled: true, depth: 'xhigh', capability_version: 99 },
    }));
    vi.mocked(resolveModelThinkingCapabilities).mockResolvedValueOnce([
      {
        provider: 'qwen',
        model: 'qwen3.7-plus',
        supported: true,
        toggle: 'optional',
        depths: [{ id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }],
        default_enabled: true,
        default_depth: 'medium',
        reason: null,
        adapter: 'qwen-hybrid-thinking',
        capability_version: 2,
      },
      {
        provider: 'qwen',
        model: 'qwen-plus',
        supported: true,
        toggle: 'optional',
        depths: [{ id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }],
        default_enabled: false,
        default_depth: 'medium',
        reason: null,
        adapter: 'qwen-hybrid-thinking',
        capability_version: 2,
      },
    ]);
    render(<App />);

    await waitFor(() => expect(screen.getByRole('button', { name: '当前模型：qwen3.7-plus' })).toHaveTextContent('模型思考 · 中'));
    await userEvent.click(screen.getByRole('button', { name: '当前模型：qwen3.7-plus' }));
    expect(screen.getByRole('button', { name: '中' })).toHaveClass('active');
    await userEvent.click(screen.getByRole('switch', { name: '模型思考' }));
    expect(screen.queryByText('模型思考深度')).not.toBeInTheDocument();
    expect(screen.queryByText('已关闭，不影响 Astra 的公开执行过程。')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', { name: '模型思考' }));
    await userEvent.click(screen.getByRole('button', { name: '高' }));
    await userEvent.click(screen.getByRole('button', { name: /qwen-plus/ }));
    expect(screen.getByRole('switch', { name: '模型思考' })).not.toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: /qwen3.7-plus/ }));
    expect(screen.getByRole('button', { name: '高' })).toHaveClass('active');

    await userEvent.type(screen.getByRole('textbox'), '使用独立模型思考深度');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(createRun).toHaveBeenLastCalledWith(
      expect.any(String),
      undefined,
      'standard',
      expect.objectContaining({ reasoning_effort: 'balanced' }),
      expect.objectContaining({
        provider: 'qwen',
        name: 'qwen3.7-plus',
        thinking: { enabled: true, depth: 'high', capability_version: 2 },
      }),
      undefined,
    );
  });

  it('keeps the public process summary when provider model thinking is off', async () => {
    window.localStorage.setItem('astra.model-providers.v2', JSON.stringify([
      {
        id: 'openai',
        name: 'OpenAI',
        enabled: false,
        endpoint: 'https://api.openai.com/v1',
        models: [{ id: 'gpt-5' }],
        apiKey: '',
      },
      {
        id: 'qwen',
        name: '通义千问',
        enabled: true,
        endpoint: 'https://example.test/v1',
        models: [{ id: 'qwen-plus' }],
        apiKey: 'secret',
      },
    ]));
    window.localStorage.setItem('astra.selected-model.v2', 'qwen:qwen-plus');
    window.localStorage.setItem('astra.model-thinking-preferences.v2', JSON.stringify({
      'qwen:qwen-plus': { enabled: false, depth: null, capability_version: 2 },
    }));
    vi.mocked(resolveModelThinkingCapabilities).mockResolvedValueOnce([{
      provider: 'qwen',
      model: 'qwen-plus',
      supported: true,
      toggle: 'optional',
      depths: [{ id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }],
      default_enabled: false,
      default_depth: 'medium',
      reason: null,
      adapter: 'qwen-hybrid-thinking',
      capability_version: 2,
    }]);
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '关闭模型思考后继续展示过程');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(createRun).toHaveBeenLastCalledWith(
      expect.any(String),
      undefined,
      'standard',
      expect.any(Object),
      expect.objectContaining({
        thinking: { enabled: false, depth: null, capability_version: 2 },
      }),
      undefined,
    );
    expect(await screen.findByText('思考完成')).toBeInTheDocument();
    expect(screen.queryByText('这里展示 Astra 的公开执行过程摘要，不是模型隐藏思维链。')).not.toBeInTheDocument();
  });

  it('fails closed when model thinking capabilities cannot be loaded', async () => {
    vi.mocked(resolveModelThinkingCapabilities).mockRejectedValueOnce(new Error('offline'));
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(await screen.findByText('暂时无法读取模型思考能力，当前设置不可调整。')).toBeInTheDocument();
    expect(screen.queryByRole('switch', { name: '模型思考' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('switch', { name: '模型思考' })).toBeInTheDocument();
  });

  it('shows an explicit unsupported state without exposing inert controls', async () => {
    vi.mocked(resolveModelThinkingCapabilities).mockResolvedValueOnce([{
      provider: 'openai',
      model: 'gpt-5',
      supported: false,
      toggle: 'unavailable',
      depths: [],
      default_enabled: false,
      default_depth: null,
      reason: 'model_not_allowlisted_for_thinking_control',
      adapter: 'openai-unsupported-model',
      capability_version: 2,
    }]);
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(await screen.findByText('当前模型不支持可配置的思考参数。')).toBeInTheDocument();
    expect(screen.queryByRole('switch', { name: '模型思考' })).not.toBeInTheDocument();
  });

  it('does not submit before model thinking capabilities resolve', async () => {
    let releaseCapabilities!: (capabilities: ModelThinkingCapability[]) => void;
    vi.mocked(resolveModelThinkingCapabilities).mockReturnValueOnce(
      new Promise((resolve) => { releaseCapabilities = resolve; }),
    );
    render(<App />);

    await userEvent.type(screen.getByRole('textbox'), '等待能力解析');
    await waitFor(() => expect(resolveModelThinkingCapabilities).toHaveBeenCalled());
    const send = screen.getByRole('button', { name: '发送' });
    expect(send).toBeDisabled();
    await userEvent.click(send);
    expect(createRun).not.toHaveBeenCalled();

    await act(async () => {
      releaseCapabilities([{
        provider: 'openai',
        model: 'gpt-5',
        supported: true,
        toggle: 'always_on',
        depths: [{ id: 'minimal', label: 'Minimal' }, { id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }],
        default_enabled: true,
        default_depth: 'medium',
        reason: null,
        adapter: 'openai-gpt5',
        capability_version: 2,
      }]);
    });
    await waitFor(() => expect(send).toBeEnabled());
    await userEvent.click(send);
    expect(createRun).toHaveBeenCalledWith(
      '等待能力解析',
      undefined,
      'standard',
      expect.any(Object),
      expect.objectContaining({
        thinking: { enabled: true, depth: 'medium', capability_version: 2 },
      }),
      undefined,
    );
  });

  it('repairs invalid stored model thinking preferences to provider defaults', async () => {
    window.localStorage.setItem('astra.model-thinking-preferences.v2', JSON.stringify({
      'openai:gpt-5': { enabled: true, depth: 'not-a-depth', capability_version: 'stale' },
    }));
    render(<App />);

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem('astra.model-thinking-preferences.v2') ?? '{}');
      expect(saved['openai:gpt-5']).toEqual({
        enabled: true,
        depth: 'medium',
        capability_version: 2,
      });
    });
  });

  it('supports a provider max thinking depth', async () => {
    vi.mocked(resolveModelThinkingCapabilities).mockResolvedValueOnce([{
      provider: 'openai',
      model: 'gpt-5',
      supported: true,
      toggle: 'optional',
      depths: [{ id: 'low', label: 'Low' }, { id: 'high', label: 'High' }, { id: 'max', label: 'Max' }],
      default_enabled: true,
      default_depth: 'high',
      reason: null,
      adapter: 'openai-gpt5-modern',
      capability_version: 2,
    }]);
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    await userEvent.click(await screen.findByRole('button', { name: '最高' }));
    expect(screen.getByRole('button', { name: '最高' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '当前模型：gpt-5' })).toHaveAccessibleDescription(/模型思考 · 最高/);
  });

  it('resumes a waiting run with its frozen effective thinking snapshot', async () => {
    window.localStorage.setItem('astra.model-providers.v2', JSON.stringify([
      {
        id: 'openai',
        name: 'OpenAI',
        enabled: true,
        endpoint: 'https://api.openai.com/v1',
        models: [{ id: 'gpt-5' }],
        apiKey: 'runtime-secret',
      },
    ]));
    const completed = await vi.mocked(getRun)('fixture');
    const waiting: RunView = {
      ...completed,
      status: 'waiting_user',
      result: null,
      summary: null,
      model_policy: {
        provider: 'openai',
        model: 'gpt-5',
        thinking: {
          requested: { enabled: false, depth: null, capability_version: 1 },
          effective: { enabled: true, depth: 'high' },
          source: 'explicit_model_control',
          adapter: 'openai-gpt5',
          adjustments: [{
            field: 'enabled',
            requested: false,
            effective: true,
            reason: 'model_thinking_always_on',
          }],
          capability_version: 2,
        },
      },
      waiting_state: { continuation_token: 'continue-thinking' },
      chat_messages: [{
        id: 'u-thinking',
        role: 'user',
        content: '需要补充',
        status: 'completed',
        metadata: {},
      }],
    };
    vi.mocked(createRun).mockResolvedValueOnce({
      run_id: 'run-1',
      task_id: 'task-1',
      status: 'waiting_user',
      answer_mode: 'standard',
    });
    vi.mocked(getRun).mockResolvedValueOnce(waiting as never);

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '需要补充');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(screen.getByRole('textbox')).toBeEnabled());
    await userEvent.type(screen.getByRole('textbox'), '这是补充信息');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(resumeRun).toHaveBeenCalledWith(
      'run-1',
      '这是补充信息',
      'continue-thinking',
      {
        provider: 'openai',
        name: 'gpt-5',
        api_key: 'runtime-secret',
        base_url: 'https://api.openai.com/v1',
        thinking: {
          enabled: true,
          depth: 'high',
          capability_version: 2,
        },
      },
    );
  });

  it('defaults every new conversation to quick mode', async () => {
    render(<App />);

    const trustedSwitch = screen.getByRole('switch', { name: '快速响应' });
    expect(trustedSwitch).toHaveAttribute('aria-checked', 'false');
    expect(trustedSwitch).toHaveTextContent('快速响应');
    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.queryByText('开启可信执行后会先生成完整计划并进行结果校验。')).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '模型思考' })).toBeInTheDocument();

    await userEvent.click(trustedSwitch);
    expect(trustedSwitch).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(screen.getByRole('button', { name: '新对话' }));
    expect(screen.getByRole('switch', { name: '快速响应' })).toHaveAttribute('aria-checked', 'false');
    expect(updateConversationStrategy).not.toHaveBeenCalled();
  });

  it('restores and persists trusted mode independently for an existing conversation', async () => {
    const now = new Date().toISOString();
    const trustedRunSnapshot = await getRun('trusted-run');
    vi.mocked(listConversations).mockResolvedValueOnce([{
      id: 'trusted-chat',
      title: '可信对话',
      title_source: 'auto',
      preferred_answer_mode: 'trusted',
      pinned_at: null,
      created_at: now,
      updated_at: now,
      last_run_status: 'completed',
      last_message_preview: '',
      has_active_share: false,
    }]);
    vi.mocked(getConversation).mockResolvedValueOnce({
      id: 'trusted-chat',
      title: '可信对话',
      title_source: 'auto',
      preferred_answer_mode: 'trusted',
      pinned_at: null,
      created_at: now,
      updated_at: now,
      last_run_status: 'completed',
      last_message_preview: '',
      has_active_share: false,
      runs: [{ ...trustedRunSnapshot, id: 'trusted-run', task_id: 'trusted-chat', answer_mode: 'trusted' }],
    });
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: '可信对话' }));
    const trustedSwitch = screen.getByRole('switch', { name: '可信执行' });
    expect(trustedSwitch).toHaveAttribute('aria-checked', 'true');

    await userEvent.click(trustedSwitch);
    expect(trustedSwitch).toHaveAttribute('aria-checked', 'false');
    await waitFor(() => expect(updateConversation).toHaveBeenCalledWith(
      'trusted-chat',
      { preferred_answer_mode: 'standard' },
    ));
  });

  it('shows a non-blocking full-screen transition when trusted mode is enabled', async () => {
    render(<App />);

    const trustedSwitch = screen.getByRole('switch', { name: '快速响应' });
    await userEvent.click(trustedSwitch);

    expect(screen.getByTestId('trusted-mode-transition')).toHaveAttribute('aria-hidden', 'true');

    await userEvent.click(trustedSwitch);
    expect(screen.queryByTestId('trusted-mode-transition')).not.toBeInTheDocument();
  });

  it('reveals the Astra brand easter egg after five rapid trusted-mode toggles', async () => {
    render(<App />);

    const trustedSwitch = screen.getByRole('switch', { name: '快速响应' });
    for (let index = 0; index < 4; index += 1) await userEvent.click(trustedSwitch);
    expect(screen.queryByTestId('trusted-easter-egg')).not.toBeInTheDocument();

    await userEvent.click(trustedSwitch);
    const easterEgg = screen.getByTestId('trusted-easter-egg');
    expect(easterEgg).toHaveAttribute('role', 'status');
    expect(easterEgg).toHaveTextContent('Astra');
    expect(easterEgg).toHaveTextContent('Navigate Ideas. Create Reality.');
  });

  it('shows the full verification outcome on trusted answers', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-trusted', task_id: 'task-trusted', status: 'created', answer_mode: 'trusted' });
    vi.mocked(getRun).mockResolvedValueOnce({
      ...snapshot,
      id: 'run-trusted',
      task_id: 'task-trusted',
      answer_mode: 'trusted',
      status: 'completed_with_warnings',
      result: snapshot.result ? { ...snapshot.result, answer_mode: 'trusted', assurance_level: 'full' } : null,
    });
    render(<App />);

    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.type(screen.getByRole('textbox'), '执行可信回答');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const verificationStatus = await screen.findByText('可信执行 · 校验带警告');
    expect(verificationStatus).toBeInTheDocument();
    const identityRow = verificationStatus.closest('.answer-identity-row');
    expect(identityRow).toHaveTextContent('Astra');
    expect(identityRow?.nextElementSibling).toHaveClass('answer-content');
  });

  it('keeps the task input focused when non-interactive composer content is clicked', async () => {
    const { container } = render(<App />);
    const composer = container.querySelector<HTMLElement>('.chat-composer');
    const input = screen.getByRole('textbox');

    expect(composer).not.toBeNull();
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(composer!);
    expect(input).toHaveFocus();

    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    await userEvent.click(screen.getByText('工具调用上限'));

    expect(input).toHaveFocus();
  });

  it('links trusted strategy controls to the complete runtime guide', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    for (const name of ['了解计划执行', '了解推理强度', '了解工具调用上限', '了解反思循环', '了解触发方式']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }

    const guide = screen.getByRole('link', { name: /查看全部模型与运行设置/ });
    expect(guide).toHaveAttribute('href', '/help#runtime-settings-overview');
    expect(guide).toHaveAttribute('target', '_blank');
    expect(screen.getAllByRole('link', { name: '查看说明' }).map((link) => link.getAttribute('href'))).toEqual([
      '/help#runtime-settings-model-thinking',
      '/help#runtime-settings-plan-execution',
      '/help#runtime-settings-reasoning',
      '/help#runtime-settings-reflection',
    ]);
  });

  it('switches execution modes and confirms before enabling bypass', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /请求批准/ }));
    expect(screen.getByRole('link', { name: '查看批准方式说明' })).toHaveAttribute('href', '/help#runtime-settings-approvals');
    await userEvent.click(screen.getByRole('button', { name: /自动批准/ }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看自动批准边界' })).toHaveAttribute('href', '/help#runtime-settings-approvals');
    await userEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /请求批准/ }));
    await userEvent.click(screen.getByRole('button', { name: /自动批准/ }));
    await userEvent.click(screen.getByRole('button', { name: '确认启用自动批准' }));
    expect(screen.getByRole('button', { name: /自动批准/ })).toHaveClass('mode-bypass');
  });

  it('offers trusted plan auto-execution independently from tool approval', async () => {
    vi.mocked(getConversationStrategy).mockResolvedValueOnce({
      preferred_answer_mode: 'trusted',
      reasoning_effort: 'balanced',
      max_tool_calls: 8,
      reflection_enabled: true,
      reflection_trigger: 'adaptive',
    });
    render(<App />);

    expect(screen.queryByRole('switch', { name: '计划生成后直接执行' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByRole('region', { name: '计划执行' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '推理强度' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '反思循环' })).toBeInTheDocument();
    expect(document.querySelectorAll('.trusted-strategy-section')).toHaveLength(3);
    const directExecution = screen.getByRole('switch', { name: '计划生成后直接执行' });
    expect(directExecution).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(directExecution);
    expect(directExecution).toHaveAttribute('aria-checked', 'true');
    await userEvent.type(screen.getByRole('textbox'), '生成并执行计划');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(vi.mocked(createRun)).toHaveBeenLastCalledWith(
      '生成并执行计划',
      undefined,
      'trusted',
      expect.objectContaining({ execution_mode: 'request_approval' }),
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
      'auto',
    );
    expect(confirmPlanExecution).not.toHaveBeenCalled();
  });

  it('keeps plan auto-execution out of the quick-mode model menu', async () => {
    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.queryByRole('switch', { name: '计划生成后直接执行' })).not.toBeInTheDocument();
  });

  it('renders the waiting DAG and confirms its bound plan version', async () => {
    const completed = await vi.mocked(getRun)('fixture');
    const waiting: RunView = {
      ...completed,
      id: 'run-plan',
      task_id: 'task-plan',
      answer_mode: 'trusted' as const,
      status: 'waiting_user',
      result: null,
      pending_approval: null,
      state_version: 3,
      task_contract: {
        success_criteria: [{
          id: 'criterion-result',
          description: '正确回应用户请求：Conversation context: 不应展示的内部上下文',
          status: 'pending',
        }],
      },
      plan_graph: {
        schema_version: 2 as const,
        id: 'plan-7',
        run_id: 'run-plan',
        version: 7,
        status: 'planned' as const,
        nodes: [
          { id: 'node-1', plan_id: 'plan-7', plan_version: 7, node_key: 'inspect', index: 1, title: '检查输入', intent: '确认范围', status: 'pending', depends_on: [], required_capabilities: [], success_criteria_refs: [], risk_level: 'low', optional: false, evidence_refs: [] },
          { id: 'node-2', plan_id: 'plan-7', plan_version: 7, node_key: 'execute', index: 2, title: '执行任务', intent: '完成目标', status: 'pending', depends_on: ['inspect'], required_capabilities: [], success_criteria_refs: [], risk_level: 'low', optional: false, evidence_refs: [] },
        ],
        edges: [{
          id: 'edge-1',
          plan_id: 'plan-7',
          predecessor_node_id: 'node-1',
          successor_node_id: 'node-2',
          dependency_type: 'hard',
        }],
      },
      waiting_state: {
        kind: 'plan_confirmation' as const,
        continuation_token: 'plan-token',
        plan_id: 'plan-7',
        plan_version: 7,
        state_version: 3,
        request: '计划已生成，确认后执行。',
      },
      chat_messages: [{ id: 'user-plan', role: 'user' as const, content: '执行复杂任务', status: 'completed', metadata: {} }],
    };
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-plan', task_id: 'task-plan', status: 'waiting_user', answer_mode: 'trusted' });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValueOnce(waiting).mockResolvedValue({
      ...waiting,
      status: 'executing',
      waiting_state: null,
      plan_graph: { ...(waiting.plan_graph as PlanGraphSnapshot), status: 'active' },
    });

    render(<App />);
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.type(screen.getByRole('textbox'), '执行复杂任务');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('计划已生成，等待执行确认')).toBeInTheDocument();
    expect(screen.queryByText(/不应展示的内部上下文/)).not.toBeInTheDocument();
    expect(screen.getByText('计划已生成，等待执行确认').closest('.composer-dock')).toHaveClass('has-plan-confirmation');
    const graphPane = await screen.findByRole('complementary', { name: '执行图谱窗格' });
    expect(graphPane).toContainElement(await screen.findByRole('region', { name: '可信执行图谱' }));
    expect(screen.getByText('计划已生成，等待执行确认').closest('.plan-confirmation-card')?.querySelector('.trusted-graph-workbench')).toBeNull();
    expect(screen.getByRole('button', { name: '放大图谱' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '缩小图谱' })).toBeInTheDocument();
    const expandPane = screen.getByRole('button', { name: '扩大图谱窗格' });
    expect(expandPane).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(expandPane);
    expect(graphPane).toHaveClass('expanded');
    expect(graphPane.closest('.chat-surface')).toHaveClass('trusted-graph-pane-expanded');
    expect(graphPane.querySelector('.trusted-graph-workbench')).not.toHaveClass('compact');
    const restorePane = screen.getByRole('button', { name: '恢复图谱窗格' });
    expect(restorePane).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(restorePane);
    expect(graphPane).not.toHaveClass('expanded');
    expect(graphPane.querySelector('.trusted-graph-workbench')).toHaveClass('compact');
    expect(screen.getAllByText('检查输入').length).toBeGreaterThan(0);
    expect(screen.getAllByText('执行任务').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', {
      name: '节点 2：执行任务，等待依赖，依赖 inspect',
    })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '收起图谱' }));
    expect(screen.queryByRole('complementary', { name: '执行图谱窗格' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '打开执行图谱' }));
    expect(await screen.findByRole('complementary', { name: '执行图谱窗格' })).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '执行计划' }));

    expect(confirmPlanExecution).toHaveBeenCalledWith(
      'run-plan',
      {
        continuationToken: 'plan-token',
        planId: 'plan-7',
        planVersion: 7,
        stateVersion: 3,
      },
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
    );
    await waitFor(() => expect(screen.queryByText('计划已生成，等待执行确认')).not.toBeInTheDocument());
    vi.mocked(getRun).mockResolvedValue(completed);
  });

  it('keeps prior trusted graphs on their turns while the floating pane follows the latest run', async () => {
    const completed = await vi.mocked(getRun)('fixture');
    const trustedRun = (id: string, nodeTitle: string): RunView => ({
      ...completed,
      id,
      task_id: 'task-graph-history',
      answer_mode: 'trusted' as const,
      summary: nodeTitle,
      plan_graph: {
        schema_version: 2 as const,
        id: `plan-${id}`,
        run_id: id,
        version: 1,
        status: 'completed' as const,
        nodes: [{ id: `node-${id}`, plan_id: `plan-${id}`, plan_version: 1, node_key: `node-${id}`, index: 1, title: nodeTitle, intent: nodeTitle, status: 'completed', depends_on: [], required_capabilities: [], success_criteria_refs: [], risk_level: 'low', optional: false, evidence_refs: [] }],
        edges: [],
      },
      chat_messages: [{ id: `user-${id}`, role: 'user' as const, content: nodeTitle, status: 'completed', metadata: {} }],
    });
    const first = trustedRun('run-graph-1', '第一轮图谱节点');
    const second = trustedRun('run-graph-2', '第二轮图谱节点');
    vi.mocked(createRun)
      .mockResolvedValueOnce({ run_id: first.id, task_id: first.task_id, status: 'created', answer_mode: 'trusted' })
      .mockResolvedValueOnce({ run_id: second.id, task_id: second.task_id, status: 'created', answer_mode: 'trusted' });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValueOnce(first).mockResolvedValueOnce(second).mockResolvedValue(second);

    render(<App />);
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.type(screen.getByRole('textbox'), '第一轮图谱节点');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    expect((await screen.findAllByText('第一轮图谱节点')).length).toBeGreaterThan(0);
    await screen.findByRole('button', { name: '发送' });

    await userEvent.type(screen.getByRole('textbox'), '第二轮图谱节点');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    const pane = await screen.findByRole('complementary', { name: '执行图谱窗格' });
    expect(pane).toHaveTextContent('第二轮图谱节点');
    expect(pane).not.toHaveTextContent('第一轮图谱节点');

    const historicalToggle = screen.getByRole('button', { name: '打开此对话图谱' });
    expect(historicalToggle).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(historicalToggle);
    expect(await screen.findByRole('region', { name: '历史执行图谱' })).toHaveTextContent('第一轮图谱节点');
    expect(historicalToggle).toHaveAttribute('aria-expanded', 'true');
    await userEvent.click(historicalToggle);
    expect(screen.queryByRole('region', { name: '历史执行图谱' })).not.toBeInTheDocument();
    vi.mocked(getRun).mockResolvedValue(completed);
  });

  it('submits a natural-language revision bound to the waiting plan version', async () => {
    const completed = await vi.mocked(getRun)('fixture');
    const waiting: RunView = {
      ...completed,
      id: 'run-revision',
      task_id: 'task-revision',
      answer_mode: 'trusted' as const,
      status: 'waiting_user',
      result: null,
      state_version: 4,
      plan_graph: {
        schema_version: 2 as const,
        id: 'plan-4',
        run_id: 'run-revision',
        version: 4,
        status: 'planned' as const,
        nodes: [{ id: 'node-4', plan_id: 'plan-4', plan_version: 4, node_key: 'work', index: 1, title: '执行任务', intent: '完成目标', status: 'pending', depends_on: [], required_capabilities: [], success_criteria_refs: [], risk_level: 'low', optional: false, evidence_refs: [] }],
        edges: [],
      },
      waiting_state: {
        kind: 'plan_confirmation' as const,
        continuation_token: 'revision-token',
        plan_id: 'plan-4',
        plan_version: 4,
        state_version: 4,
        request: '确认执行',
      },
    };
    const revised: RunView = {
      ...waiting,
      state_version: 5,
      plan_graph: { ...(waiting.plan_graph as PlanGraphSnapshot), id: 'plan-5', version: 5 },
      waiting_state: {
        ...waiting.waiting_state,
        continuation_token: 'revision-token-2',
        plan_id: 'plan-5',
        plan_version: 5,
        state_version: 5,
      },
    };
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-revision', task_id: 'task-revision', status: 'waiting_user', answer_mode: 'trusted' });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValueOnce(waiting).mockResolvedValue(revised);

    render(<App />);
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.type(screen.getByRole('textbox'), '执行复杂任务');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await userEvent.click(await screen.findByRole('button', { name: '调整计划' }));
    const request = screen.getByLabelText('如何调整这个计划？');
    await userEvent.type(request, '拆成两个并行分支');
    await userEvent.click(screen.getByRole('button', { name: '生成调整后的计划' }));

    await waitFor(() => expect(revisePlan).toHaveBeenCalledWith(
      'run-revision',
      '拆成两个并行分支',
      {
        continuationToken: 'revision-token',
        planId: 'plan-4',
        planVersion: 4,
        stateVersion: 4,
      },
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
    ));
    await waitFor(() => expect(screen.getByText('v5')).toBeInTheDocument());
    vi.mocked(getRun).mockResolvedValue(completed);
  });

  it('keeps the waiting plan visible when confirmation is rejected as stale', async () => {
    const completed = await vi.mocked(getRun)('fixture');
    const waiting: RunView = {
      ...completed,
      id: 'run-stale',
      task_id: 'task-stale',
      answer_mode: 'trusted' as const,
      status: 'waiting_user',
      result: null,
      state_version: 2,
      plan_graph: {
        schema_version: 2 as const,
        id: 'plan-stale',
        run_id: 'run-stale',
        version: 2,
        status: 'planned' as const,
        nodes: [{ id: 'node-stale', plan_id: 'plan-stale', plan_version: 2, node_key: 'work', index: 1, title: '执行任务', intent: '完成目标', status: 'pending', depends_on: [], required_capabilities: [], success_criteria_refs: [], risk_level: 'low', optional: false, evidence_refs: [] }],
        edges: [],
      },
      waiting_state: {
        kind: 'plan_confirmation' as const,
        continuation_token: 'stale-token',
        plan_id: 'plan-stale',
        plan_version: 2,
        state_version: 2,
        request: '确认执行',
      },
    };
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-stale', task_id: 'task-stale', status: 'waiting_user', answer_mode: 'trusted' });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValue(waiting);
    vi.mocked(confirmPlanExecution).mockRejectedValueOnce(new Error('stale'));

    render(<App />);
    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.type(screen.getByRole('textbox'), '执行过期计划');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await userEvent.click(await screen.findByRole('button', { name: '执行计划' }));

    expect(await screen.findByText('计划确认已失效，请刷新后核对最新计划。')).toBeInTheDocument();
    expect(screen.getByText('计划已生成，等待执行确认')).toBeInTheDocument();
    vi.mocked(confirmPlanExecution).mockResolvedValue({ run_id: 'run-1', task_id: 'task-1', status: 'executing' });
    vi.mocked(getRun).mockResolvedValue(completed);
  });

  it('restores a pending approval above the composer and submits allow similar', async () => {
    const completed = await vi.mocked(getRun)('fixture');
    const pending = {
      ...completed,
      status: 'waiting_user',
      result: null,
      summary: null,
      pending_approval: {
        id: 'approval-1',
        tool_call_id: 'call-1',
        tool_name: 'bash_execute',
        preview: 'pytest tests/test_api.py -q',
        permission: 'command_execute',
        impact: 'external_side_effect',
        decisions: ['approve_once', 'allow_similar', 'reject'] as Array<'approve_once' | 'allow_similar' | 'reject'>,
        created_at: 'now',
      },
      waiting_state: { kind: 'tool_approval', continuation_token: 'continue-1' },
      chat_messages: [{ id: 'u-approval', role: 'user', content: '运行测试', status: 'completed', metadata: {} }],
    };
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-1', task_id: 'task-1', status: 'waiting_user' });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValueOnce(pending).mockResolvedValue(completed);

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '运行测试');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    const approvalCard = await screen.findByRole('group', { name: '需要你的确认' });
    const composerDock = approvalCard.closest('.composer-dock');
    expect(composerDock).not.toBeNull();
    expect(composerDock?.querySelector('.chat-composer')).toBeInTheDocument();
    expect(approvalCard.nextElementSibling).toHaveClass('chat-composer');
    expect(screen.getByText('pytest tests/test_api.py -q')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '当前运行内允许' }));

    expect(decideToolApproval).toHaveBeenCalledWith(
      'run-1',
      'approval-1',
      'allow_similar',
      'continue-1',
      expect.objectContaining({ provider: 'openai', name: 'gpt-5' }),
    );
    await waitFor(() => expect(screen.queryByRole('group', { name: '需要你的确认' })).not.toBeInTheDocument());
  });

  it('omits similar-command approval when the backend does not offer it', async () => {
    const snapshot = await vi.mocked(getRun)('fixture');
    const pending = {
      ...snapshot,
      status: 'waiting_user',
      result: null,
      pending_approval: {
        id: 'approval-exact', tool_call_id: 'call-exact', tool_name: 'web_search',
        preview: '{"query":"Astra"}', permission: 'network_read', impact: 'read_only',
        decisions: ['approve_once', 'reject'] as Array<'approve_once' | 'allow_similar' | 'reject'>, created_at: 'now',
      },
      waiting_state: { continuation_token: 'continue-exact' },
    };
    vi.mocked(createRun).mockResolvedValueOnce({ run_id: 'run-1', task_id: 'task-1', status: 'waiting_user' });
    vi.mocked(getRun).mockReset();
    vi.mocked(getRun).mockResolvedValue(pending);

    render(<App />);
    await userEvent.type(screen.getByRole('textbox'), '需要搜索');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByRole('button', { name: '允许这次' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '拒绝' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '当前运行内允许' })).not.toBeInTheDocument();
  });

  it('uses translated execution mode names in the English interface', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '界面' }));
    await userEvent.selectOptions(screen.getByDisplayValue('中文'), 'en');
    await userEvent.click(screen.getByRole('button', { name: 'Close settings' }));
    await userEvent.click(screen.getByRole('button', { name: /Request approval/ }));

    expect(screen.getByRole('button', { name: /Auto approve/ })).toBeInTheDocument();
    expect(screen.queryByText('仅规划')).not.toBeInTheDocument();
    expect(screen.queryByText('自动批准')).not.toBeInTheDocument();
  });

  it('hides reflection trigger choices when reflection is disabled', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByText('触发方式')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', { name: '反思循环' }));
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

    await userEvent.click(screen.getByRole('switch', { name: '快速响应' }));
    await userEvent.click(screen.getByRole('button', { name: '当前模型：gpt-5' }));
    expect(screen.getByText('可信对话策略')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByText('可信对话策略')).not.toBeInTheDocument();
  });
});
