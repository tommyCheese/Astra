import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { createRun } from '../src/api';

vi.mock('../src/api', () => ({
  createRun: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'created' })),
  getRun: vi.fn(async () => ({
    id: 'run-1',
    task_id: 'task-1',
    status: 'completed',
    mode: 'web_agent',
    summary: '完成',
    result: {
      summary: '已完成查询',
      findings: [{ text: '发现一条证据', source_urls: ['https://example.com'] }],
      sources: [{ url: 'https://example.com', title: 'Example' }],
      source_quality: [
        {
          url: 'https://example.com',
          quality_score: 0.92,
          extraction_strategy: 'readability',
          warnings: ['正文与查询词重叠较少'],
        },
      ],
      failed_sources: [{ url: 'https://bad.example', category: 'fetch_failed' }],
      conflicts: [],
      caveats: ['部分来源抓取失败'],
      verification_notes: ['验证通过'],
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
        metadata: { turn_index: 2 },
      },
    ],
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
    globalThis.localStorage?.clear();
  });

  it('submits a goal and renders the result', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '↑' }));

    expect(await screen.findByText('已完成查询')).toBeInTheDocument();
    expect(screen.getByText('发现一条证据')).toBeInTheDocument();
    expect(screen.getByText(/92%/)).toBeInTheDocument();
    expect(screen.queryByText('审计详情')).not.toBeInTheDocument();
    expect(screen.getAllByText(/web_search/).length).toBeGreaterThan(0);
    expect(screen.getByRole('navigation', { name: '问题导航' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跳转到问题 1' })).toBeInTheDocument();
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
  });

  it('reveals the local star burst after five quick logo clicks', async () => {
    render(<App />);
    const logo = screen.getByRole('button', { name: 'Astra 图标' });

    for (let index = 0; index < 5; index += 1) await userEvent.click(logo);

    expect(screen.getByTestId('astra-burst')).toBeInTheDocument();
  });

  it('keeps follow-up messages in the same history conversation', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: '↑' }));
    await screen.findByText('已完成查询');
    await userEvent.type(screen.getByRole('textbox'), '继续追问{Enter}');
    await screen.findAllByText('已完成查询');

    expect(vi.mocked(createRun)).toHaveBeenLastCalledWith('继续追问', 'task-1');
    expect(screen.getAllByRole('button', { name: /完成 completed/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /跳转到问题/ })).toHaveLength(2);
  });

  it('opens settings and moves capabilities into the settings view', async () => {
    render(<App />);

    expect(screen.queryByText('Web Fetch')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '工具' }));

    expect(screen.getByRole('heading', { name: '工具' })).toBeInTheDocument();
    expect(screen.getByText('Web Fetch')).toBeInTheDocument();
    expect(screen.getByText('工具调用上限')).toBeInTheDocument();
    expect(screen.getByText('并行工具调用')).toBeInTheDocument();
    expect(screen.getByText('工具失败重试')).toBeInTheDocument();
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

    expect(screen.getByRole('heading', { name: '运行时' })).toBeInTheDocument();
    expect(screen.getByText('沙盒模式')).toBeInTheDocument();
    expect(screen.getByText('最大 Agent 轮次')).toBeInTheDocument();
    expect(screen.queryByText('工具调用上限')).not.toBeInTheDocument();
    expect(screen.queryByText('并行工具调用')).not.toBeInTheDocument();
    expect(screen.queryByText('工具失败重试')).not.toBeInTheDocument();
    expect(screen.queryByText('命令执行确认')).not.toBeInTheDocument();
    expect(screen.getByText('保留运行工件')).toBeInTheDocument();
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

    await userEvent.click(screen.getByRole('button', { name: /默认/ }));
    await userEvent.click(screen.getByRole('button', { name: /仅规划/ }));
    expect(screen.getByRole('button', { name: /仅规划/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /仅规划/ }));
    await userEvent.click(screen.getByRole('button', { name: /全自动/ }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /仅规划/ }));
    await userEvent.click(screen.getByRole('button', { name: /全自动/ }));
    await userEvent.click(screen.getByRole('button', { name: '确认启用全自动' }));
    expect(screen.getByRole('button', { name: /全自动/ })).toHaveClass('mode-bypass');
  });

  it('uses Plan and ByPass names only in the English interface', async () => {
    render(<App />);

    await userEvent.click(screen.getByRole('button', { name: /设置/ }));
    await userEvent.click(screen.getByRole('button', { name: '界面' }));
    await userEvent.selectOptions(screen.getByDisplayValue('中文'), 'en');
    await userEvent.click(screen.getByRole('button', { name: 'Close settings' }));
    await userEvent.click(screen.getByRole('button', { name: /Default/ }));

    expect(screen.getByRole('button', { name: /Plan/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /ByPass/ })).toBeInTheDocument();
    expect(screen.queryByText('仅规划')).not.toBeInTheDocument();
    expect(screen.queryByText('全自动')).not.toBeInTheDocument();
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
    expect(screen.getByText('Token 用量')).toBeInTheDocument();
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
