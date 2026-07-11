import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';

export type Language = 'zh-CN' | 'en';

const english: Record<string, string> = {
  'Web Agent · 可审计搜索与抓取': 'Web Agent · Auditable search and retrieval',
  '今天想研究什么？': 'What would you like to research?',
  '我会使用 Web 搜索和自适应抓取，边行动边留下可审计证据。': 'I can search the web and adaptively extract sources while keeping an auditable evidence trail.',
  '添加内容': 'Add content', '上传文件': 'Upload file', '文档、代码与数据': 'Documents, code, and data',
  '添加图片': 'Add image', '分析图像内容': 'Analyze image content', '连接来源': 'Connect source', '即将支持': 'Coming soon',
  '输入任务 / 继续追问...': 'Enter a task or ask a follow-up...', '请输入任务目标': 'Enter a task goal', '创建 run 失败': 'Failed to create run',
  '新对话': 'New chat', '历史对话': 'Chat history', '当前 Web Agent 会话': 'Current Web Agent chat', '暂无会话': 'No chats yet',
  '用量统计': 'Usage', '设置': 'Settings', '本地配置': 'Local preferences', '工作区': 'Workspace', '关闭设置': 'Close settings', '设置类别': 'Settings categories',
  '工具': 'Tools', '运行时': 'Runtime', '记忆': 'Memory', '验证与安全': 'Verification & safety', '界面': 'Interface', '数据与隐私': 'Data & privacy',
  '管理 Agent 可用工具及其调用策略。': 'Manage the tools available to the agent and how they are invoked.',
  '搜索公开网页并生成候选来源': 'Search the public web for candidate sources', '自适应提取页面主要内容': 'Adaptively extract primary page content',
  '文件分析': 'File analysis', '解析上传的文档、代码与数据': 'Parse uploaded documents, code, and data', '图像理解': 'Image understanding', '识别并分析图片内容': 'Understand and analyze images',
  '已启用': 'Enabled', '工具调用确认': 'Tool confirmation', '工具可能修改数据、产生费用或影响外部系统时请求确认': 'Ask before tools modify data, incur costs, or affect external systems',
  '仅高风险工具': 'High-risk tools only', '每次调用': 'Every call', '从不确认': 'Never ask', '工具调用上限': 'Tool call limit', '限制单次任务可执行的工具调用总数': 'Limit total tool calls per task',
  '并行工具调用': 'Parallel tool calls', '并发执行相互独立且无副作用冲突的工具': 'Run independent tools concurrently when side effects do not conflict',
  '工具失败重试': 'Tool retries', '仅重试临时网络错误和明确标记为可恢复的工具错误': 'Retry only transient network and explicitly recoverable tool errors', '不重试': 'No retries',
  '管理 Agent 的执行环境、生命周期和任务级资源边界。': 'Manage the agent execution environment, lifecycle, and task-level limits.',
  '执行环境': 'Environment', '本地沙盒': 'Local sandbox', '任务状态': 'Task state', '可恢复': 'Recoverable', '网络': 'Network', '按需授权': 'On-demand approval',
  '沙盒模式': 'Sandbox mode', '限定 Agent 可读取和修改的文件系统范围': 'Limit the filesystem scope the agent can read and modify', '只读': 'Read only', '工作区可写': 'Workspace write', '隔离容器': 'Isolated container',
  '网络策略': 'Network policy', '限制运行环境可访问的外部网络范围': 'Limit external network access for the runtime', '禁用': 'Disabled', '公开网络，按需授权': 'Public network with approval', '仅允许列表': 'Allowlist only',
  '命令执行确认': 'Command confirmation', '命令可能修改环境或影响外部系统时请求确认': 'Ask before commands modify the environment or affect external systems', '仅高风险命令': 'High-risk commands only', '每次执行': 'Every execution',
  '最大 Agent 轮次': 'Maximum agent turns', '达到上限后停止循环并输出当前结果': 'Stop the loop at the limit and return the current result',
  '单次运行时限': 'Run timeout', '超时后停止任务并保留可恢复的运行状态': 'Stop timed-out tasks and preserve recoverable state', '分钟': 'minutes',
  '后台继续运行': 'Continue in background', '离开当前对话后继续执行，并保留状态通知': 'Keep running after leaving the chat and retain status notifications',
  '保留运行工件': 'Retain run artifacts', '保存运行状态、验证报告和失败现场用于审计': 'Keep run state, verification reports, and failure context for auditing',
  '管理 Agent 在单次任务和不同对话之间保留的信息。': 'Manage information retained within a task and across chats.', '运行记忆': 'Run memory', '在当前任务中保留来源摘要和决策线索': 'Keep source summaries and decision context within the task',
  '跨对话记忆': 'Cross-chat memory', '在新对话中使用已确认的偏好与事实': 'Use confirmed preferences and facts in new chats', '写入阈值': 'Write threshold', '仅保存高于该置信度的结构化记忆': 'Save structured memories only above this confidence',
  '记忆保留期': 'Memory retention', '到期后自动清理非固定记忆': 'Automatically remove unpinned memories after expiry', '天': 'days', '永久': 'Forever',
  '定义最终答案必须满足的证据和质量标准。': 'Define the evidence and quality standards required for final answers.', '回答前验证': 'Verify before answering', '生成最终答案前检查证据覆盖、来源冲突和失败项': 'Check evidence coverage, source conflicts, and failures before answering',
  '最低独立来源数': 'Minimum independent sources', '总结类任务至少需要多少个相互独立的来源': 'Independent sources required for summarization tasks', '来源质量阈值': 'Source quality threshold', '低于阈值的来源会被标记，并降低其证据权重': 'Flag sources below the threshold and reduce their evidence weight',
  '冲突处理': 'Conflict handling', '来源结论不一致时保留分歧，不强行合并为单一答案': 'Preserve disagreements instead of forcing conflicting sources into one answer', '披露冲突': 'Disclose conflicts', '继续查证': 'Research further', '阻止回答': 'Block answer',
  '调整工作区的信息密度和运行过程展示。': 'Adjust workspace density and execution visibility.', '语言': 'Language', '选择界面显示语言': 'Choose the interface language', '中文': 'Chinese', '英文': 'English',
  '主题模式': 'Theme', '选择界面外观，或随操作系统自动切换': 'Choose an appearance or follow the operating system', '跟随系统': 'System', '浅色模式': 'Light', '暗色模式': 'Dark',
  '过程展示': 'Process visibility', '在对话中显示工具调用和反思摘要': 'Show tool calls and reflection summaries in chat', '审计面板': 'Audit panel', '任务完成后显示证据、事件和记忆': 'Show evidence, events, and memory after task completion', '信息密度': 'Information density', '控制对话和面板的间距': 'Control spacing in chats and panels', '紧凑': 'Compact', '舒适': 'Comfortable',
  '控制运行数据、抓取内容和诊断信息的保存方式。': 'Control storage of run data, fetched content, and diagnostics.', '保存运行记录': 'Save run history', '保留对话、工具调用和验证报告': 'Retain chats, tool calls, and verification reports', '保存抓取正文': 'Save fetched content', '将网页正文写入本地工件存储': 'Store extracted page content in local artifacts', '诊断日志': 'Diagnostic logs', '记录不包含正文的性能与错误信息': 'Record performance and errors without page content', '清除本地运行数据': 'Clear local run data',
  '模型': 'Model', '复杂研究与多步任务': 'Complex research and multi-step tasks', '快速问答与轻量搜索': 'Fast answers and lightweight search', '通用推理模型': 'General reasoning model', '对话策略': 'Chat strategy',
  '推理强度': 'Reasoning effort', '快速': 'Fast', '均衡': 'Balanced', '深入': 'Deep', '规划策略': 'Planning strategy', '直接': 'Direct', '自适应': 'Adaptive', '先规划': 'Plan first',
  '反思循环': 'Reflection loop', '检查结果并修订下一步策略': 'Review results and revise the next action', '触发方式': 'Trigger', '失败时': 'On failure', '按需': 'Adaptive', '每轮': 'Every turn', '反思关闭': 'Reflection off', '反思': 'reflection',
  '当前模型': 'Current model', '当前对话': 'Current chat', '关闭用量统计': 'Close usage', '模型调用': 'Model calls', '次决策 / 生成': 'decisions / generations', 'Token 用量': 'Token usage', '前端估算': 'Frontend estimate',
  '工具调用': 'Tool calls', '成功率': 'Success rate', '证据来源': 'Evidence sources', 'Agent 轮次': 'Agent turns', 'Memory 写入': 'Memory writes', '验证警告': 'Verification warnings', '精确输入、输出和缓存 Token 将在模型网关接入后由后端返回。': 'Exact input, output, and cached token usage will come from the model gateway.',
  '你': 'You', '审计详情': 'Audit details', '暂无 Memory 写入。': 'No memory writes yet.', '提交了一个任务': 'Submitted a task',
  '正在搜索候选来源...': 'Searching for candidate sources...', '正在阅读和验证来源...': 'Reading and verifying sources...', '正在反思并调整策略...': 'Reflecting and adjusting strategy...', '正在验证证据...': 'Verifying evidence...', '正在处理...': 'Working...',
};

type I18nValue = { language: Language; setLanguage: (language: Language) => void; t: (text: string) => string };
const I18nContext = createContext<I18nValue | null>(null);

function initialLanguage(): Language {
  const saved = globalThis.localStorage?.getItem('astra.language');
  if (saved === 'zh-CN' || saved === 'en') return saved;
  return 'zh-CN';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(initialLanguage);
  useEffect(() => {
    globalThis.localStorage?.setItem('astra.language', language);
    document.documentElement.lang = language;
  }, [language]);
  const value = useMemo<I18nValue>(() => ({ language, setLanguage, t: (text) => language === 'en' ? english[text] ?? text : text }), [language]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside I18nProvider');
  return value;
}
