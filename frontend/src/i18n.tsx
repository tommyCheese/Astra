import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';

export type Language = 'zh-CN' | 'en';

const english: Record<string, string> = {
  'Web Agent · 可审计搜索与抓取': 'Web Agent · Auditable search and retrieval',
  'Navigate Ideas. Create Reality.': 'Navigate Ideas. Create Reality.',
  '今天想完成点什么？': 'What would you like to accomplish today?',
  '添加内容': 'Add content', '上传文件': 'Upload file', '文档、代码与数据': 'Documents, code, and data',
  '添加图片': 'Add image', '分析图像内容': 'Analyze image content', '连接来源': 'Connect source', '即将支持': 'Coming soon',
  '输入任务 / 继续追问...': 'Enter a task or ask a follow-up...', '请输入任务目标': 'Enter a task goal', '创建 run 失败': 'Failed to create run',
  '请输入你想完成的目标。': 'Enter what you would like to accomplish.', '服务暂时出现异常，请稍后重试。': 'The service is temporarily unavailable. Please try again shortly.', '发送': 'Send', '终止回答': 'Stop response', '已终止': 'Stopped', '终止回答失败，当前回答可能仍在继续。': 'Unable to stop the response. It may still be running.',
  '新对话': 'New chat', '历史对话': 'Chat history', '当前 Web Agent 会话': 'Current Web Agent chat', '暂无会话': 'No chats yet', '暂无对话': 'No chats yet', 'Astra 图标': 'Astra icon',
  '切换对话': 'Switch chat', '最近对话': 'Recent chats', '未命名对话': 'Untitled chat', '还没有历史对话': 'No chat history yet',
  '最多保留最近 {count} 个会话': 'Keeps up to {count} recent chats', '较早的会话会自动移出此列表': 'Older chats are automatically removed from this list',
  '问题导航': 'Question navigation', '跳转到问题': 'Jump to question', '问题': 'Question', '回到最新': 'Jump to latest',
  '用量统计': 'Usage', '设置': 'Settings', '本地配置': 'Local preferences', '工作区': 'Workspace', '关闭设置': 'Close settings', '设置类别': 'Settings categories',
  '模型管理': 'Model providers', '工具': 'Tools', '运行时': 'Runtime', '记忆': 'Memory', '界面': 'Interface', '数据与隐私': 'Data & privacy',
  '配置模型供应商连接、凭据和 Agent 可选模型。': 'Configure model provider connections, credentials, and models available to the agent.',
  '模型供应商': 'Model providers', '供应商': 'Providers', '添加供应商': 'Add provider', 'OpenAI 兼容': 'OpenAI compatible', 'Ollama、vLLM、OpenRouter': 'Ollama, vLLM, OpenRouter',
  '通义千问': 'Qwen', 'DeepSeek 开放平台': 'DeepSeek Platform', '阿里云百炼': 'Alibaba Cloud Model Studio', '硅基流动模型广场': 'SiliconFlow Model Hub',
  '启用': 'Enabled', 'API 地址': 'API endpoint', '供应商 API 的基础地址': 'Base URL for the provider API', 'API Key': 'API key',
  '凭据仅在当前页面内存中暂存，接入后端后应写入加密密钥存储': 'Credentials are held only in page memory; the backend integration should store them in encrypted secret storage.',
  '显示': 'Show', '隐藏': 'Hide', '组织或项目 ID': 'Organization or project ID', '可选，用于供应商侧的项目隔离与计费': 'Optional provider-side project isolation and billing identifier',
  'API 版本': 'API version', 'Azure OpenAI 请求使用的 API 版本': 'API version used for Azure OpenAI requests', '可选': 'Optional',
  '可用模型 ID': 'Available model IDs', '使用逗号分隔，模型选择器将使用这些标识': 'Comma-separated identifiers exposed in the model selector',
  '请求兼容性': 'API compatibility', '自动检测': 'Auto detect', '未验证': 'Not verified', '连接正常': 'Connected', '缺少连接信息': 'Connection details required', '配置已更新': 'Configuration updated',
  '测试连接': 'Test connection', '保存配置': 'Save configuration', '未配置模型': 'No model configured', '请先在模型管理中启用供应商并配置模型': 'Enable a provider and configure models in Model providers first.',
  '管理 Agent 可用工具及其调用策略。': 'Manage the tools available to the agent and how they are invoked.', '管理 Agent 可用工具。修改会应用到之后新建的任务，运行中的任务不受影响。': 'Manage tools available to the agent. Changes apply to newly created tasks and do not affect running tasks.',
  '凭据保存在当前浏览器本地，不会写入运行记录': 'Credentials are stored in this browser only and are never written to run history.',
  '搜索公开网页并生成候选来源': 'Search the public web for candidate sources', '自适应提取页面主要内容': 'Adaptively extract primary page content', '在隔离的 Docker 运行时中生成图表': 'Generate charts in an isolated Docker runtime', 'Chart Render': 'Chart Render',
  '文件分析': 'File analysis', '解析上传的文档、代码与数据': 'Parse uploaded documents, code, and data', '图像理解': 'Image understanding', '识别并分析图片内容': 'Understand and analyze images',
  '已启用': 'Enabled', '已停用': 'Disabled', '当前不可用': 'Currently unavailable', '需要先启用 Docker 沙箱。': 'Enable the Docker sandbox first.', 'Docker 当前不可用。': 'Docker is currently unavailable.', '正在读取工具配置…': 'Loading tool settings…', '无法读取工具配置': 'Unable to load tool settings', '工具已启用，将用于之后新建的任务。': 'Tool enabled for newly created tasks.', '工具已停用，之后新建的任务不会调用它。': 'Tool disabled for newly created tasks.', '保存工具配置失败，已恢复原状态。': 'Unable to save tool settings; the previous state was restored.', '开关保存在数据库中，服务重启后仍会保持当前状态。': 'Switches are stored in the database and retained after backend restarts.', '工具调用确认': 'Tool confirmation', '工具可能修改数据、产生费用或影响外部系统时请求确认': 'Ask before tools modify data, incur costs, or affect external systems',
  '仅高风险工具': 'High-risk tools only', '每次调用': 'Every call', '从不确认': 'Never ask', '工具调用上限': 'Tool call limit', '限制单次任务可执行的工具调用总数': 'Limit total tool calls per task',
  '次工具': 'tool calls', '当前强度可调整范围：{min}–{max} 次': 'Adjustable range for this effort: {min}–{max}', '允许 0–5 次工具调用，简单任务更快。': 'Allows 0–5 tool calls for faster simple tasks.', '允许 6–15 次工具调用，兼顾速度与检查深度。': 'Allows 6–15 tool calls, balancing speed and depth.', '允许 16–50 次工具调用，为复杂任务提供更多执行预算。': 'Allows 16–50 tool calls for complex tasks.',
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
  '控制任务记录、工具内容和诊断信息的保存方式。': 'Control storage of task history, tool content, and diagnostics.', '保留对话、工具调用元数据和验证报告': 'Retain chats, tool call metadata, and verification reports', '工具内容保留': 'Tool content retention', '决定是否保存工具返回的正文、文件内容或结构化结果': 'Choose whether to retain text, files, or structured results returned by tools', '不保留内容': 'Do not retain content', '仅保留元数据': 'Metadata only', '保留完整输出': 'Retain full output', '记录不包含工具内容的性能与错误信息': 'Record performance and errors without tool content',
  '模型': 'Model', '复杂研究与多步任务': 'Complex research and multi-step tasks', '快速问答与轻量搜索': 'Fast answers and lightweight search', '通用推理模型': 'General reasoning model', '对话策略': 'Chat strategy',
  '请求批准': 'Request approval', '仅规划': 'Plan', '自动批准': 'Auto approve', '执行模式': 'Execution mode', '只规划任务，不调用工具或执行命令': 'Plan the task without calling tools or running commands', '自动执行低风险操作，高风险权限需要确认': 'Run low-risk actions automatically and confirm high-risk permissions', '自动执行所有命令和工具，不再请求确认': 'Run all commands and tools automatically without confirmation',
  '启用自动批准模式？': 'Enable auto approval?', '自动批准模式将允许 Agent 自动执行所有命令和工具，包括可能修改文件、访问网络或影响外部系统的高风险操作。': 'Auto approval allows the agent to run every command and tool automatically, including high-risk actions that may modify files, access the network, or affect external systems.', '仅在你信任当前任务和运行环境时启用。': 'Enable this only when you trust the current task and runtime environment.', '取消': 'Cancel', '确认启用自动批准': 'Enable auto approval',
  '推理强度': 'Reasoning effort', '快速': 'Fast', '均衡': 'Balanced', '深入': 'Deep', '规划策略': 'Planning strategy', '直接': 'Direct', '自适应': 'Adaptive', '先规划': 'Plan first',
  '反思循环': 'Reflection loop', '检查结果并修订下一步策略': 'Review results and revise the next action', '触发方式': 'Trigger', '失败时': 'On failure', '按需': 'Adaptive', '每轮': 'Every turn', '反思关闭': 'Reflection off', '反思': 'reflection', '保存对话策略失败，当前选择可能无法在重启后恢复。': 'Unable to save the chat strategy. The current selection may not be restored after restart.',
  '了解对话策略': 'About chat strategies', '对话策略说明': 'Chat strategy guide', '策略说明': 'Strategy guide', '关闭策略说明': 'Close strategy guide', '关闭': 'Off',
  '减少思考轮次与工具预算，简单任务更快。': 'Uses fewer reasoning turns and tools, making simple tasks faster.', '兼顾速度与检查深度，适合多数任务。': 'Balances speed and review depth for most tasks.', '增加思考、工具与反思预算，复杂任务更稳。': 'Adds reasoning, tool, and reflection budget for complex tasks.',
  '生成单步计划后立即处理，启动最快。': 'Starts immediately with a single-step plan.', '轻量启动，按结果决定是否调整计划。': 'Starts light and adapts the plan to results.', '执行前生成完整计划，适合多步骤任务。': 'Builds a full plan before action for multi-step tasks.',
  '不调用额外反思；安全与完成检查仍保留。': 'Skips optional reflection; safety and completion checks remain.', '只在工具、模型输出或完成检查失败时反思。': 'Reflects only when tools, model output, or completion checks fail.', '失败、低置信度、冲突或无进展时反思。': 'Reflects on failure, low confidence, conflicts, or stalled progress.', '每轮结束都反思，更审慎但更慢、更耗用量。': 'Reflects after every turn; more cautious, but slower and costlier.',
  '当前模型': 'Current model', '当前对话': 'Current chat', '关闭用量统计': 'Close usage', '模型调用': 'Model calls', '次决策 / 生成': 'decisions / generations', 'Token 用量': 'Token usage', '前端估算': 'Frontend estimate',
  '工具调用': 'Tool calls', '成功率': 'Success rate', '证据来源': 'Evidence sources', 'Agent 轮次': 'Agent turns', 'Memory 写入': 'Memory writes', '验证警告': 'Verification warnings', '精确输入、输出和缓存 Token 将在模型网关接入后由后端返回。': 'Exact input, output, and cached token usage will come from the model gateway.',
  '任务步骤': 'Task steps', '消息轮次': 'Message turns',
  '持久化统计': 'Persisted analytics', '用量看板': 'Usage dashboard', '数据来自数据库与模型供应商，不使用前端估算。': 'Data comes from the database and model providers, without frontend estimates.',
  '统计范围': 'Analytics range', '全部历史': 'All time', '最近 7 天': 'Last 7 days', '最近 30 天': 'Last 30 days', '当前运行': 'Current run',
  '正在读取持久化用量…': 'Loading persisted usage…', '加载失败': 'Unable to load', '重试': 'Retry', '无法加载用量数据。': 'Unable to load usage data.',
  '所选范围暂无用量记录': 'No usage in this range', '完成一次任务后，模型、工具与产物用量会在此显示。': 'Model, tool, and artifact usage will appear here after a task completes.',
  'Token 总量': 'Total tokens', '{success} 成功 · {failed} 失败': '{success} succeeded · {failed} failed', '{reported}/{total} 次已报告': '{reported}/{total} reported',
  '暂无已完成调用': 'No completed calls', '{rate}% 成功率': '{rate}% success rate', '{count} 条 Memory': '{count} memories',
  'Token 报告覆盖率 {coverage}%': 'Token reporting coverage {coverage}%', '供应商已为全部调用返回精确用量': 'The provider reported exact usage for every call', '未报告的调用保持未知，不计为 0 或估算值': 'Unreported calls remain unknown and are neither zeroed nor estimated',
  'Token 构成': 'Token breakdown', '输入': 'Input', '缓存输入': 'Cached input', '输出': 'Output', '推理': 'Reasoning', '模型明细': 'Model breakdown', '工具明细': 'Tool breakdown',
  '{reported}/{total} 已报告': '{reported}/{total} reported', '暂无模型调用': 'No model calls', '暂无工具调用': 'No tool calls', '{count} 次': '{count} calls', '按日趋势': 'Daily trend', '{models} 次模型 · {tools} 次工具': '{models} model calls · {tools} tool calls',
  'Docker 运行时': 'Docker runtime', '管理绘图工具使用的隔离镜像与 Python 依赖。只有构建阶段联网，工具执行始终断网。': 'Manage the isolated image and Python dependencies used by chart tools. Only builds have network access; tool execution stays offline.',
  'Docker 运行状态': 'Docker runtime status', '运行引擎': 'Runtime engine', '一次性强化容器': 'One-shot hardened container', '已就绪': 'Ready', '等待构建': 'Build queued', '构建中': 'Building', '构建成功': 'Build succeeded', '构建失败': 'Build failed', '已取消': 'Cancelled',
  '当前镜像': 'Current image', '读取中': 'Loading', '依赖摘要': 'Dependency digest', '基础依赖': 'Base dependencies', '断网执行': 'Offline execution', '只读根目录': 'Read-only root filesystem', '非 root': 'Non-root', '资源受限': 'Resource limited',
  'Python 依赖管理': 'Python dependency management', '版本可留空，构建时将安装最新版本。核心绘图库由基础镜像锁定。': 'Leave the version blank to install the latest release at build time. Core plotting libraries are locked by the base image.',
  '基础镜像核心依赖': 'Core base-image dependencies', '核心依赖': 'Core dependencies', '随基础镜像提供，不允许修改或删除': 'Provided by the base image and cannot be edited or removed', '{count} 项已锁定': '{count} locked', '依赖名称': 'Dependency name', '锁定版本': 'Locked version', '状态': 'Status', '已锁定': 'Locked',
  '自定义依赖': 'Custom dependencies', '可编辑、删除，并在下一次构建后生效': 'Editable and removable; changes take effect after the next build', '{count} 项': '{count} items', '选择全部依赖': 'Select all dependencies', '选择全部': 'Select all', '删除所选': 'Delete selected', '版本': 'Version',
  '尚未添加自定义依赖': 'No custom dependencies', '可以添加额外的 Python 包扩展工具能力。': 'Add Python packages to extend tool capabilities.', '未命名依赖': 'unnamed dependency', '选择 {name}': 'Select {name}', '例如 polars': 'e.g. polars', '依赖': 'dependency', '{name}版本': '{name} version', '最新版本': 'Latest version', '删除 {name}': 'Delete {name}',
  '添加依赖': 'Add dependency', '批量添加': 'Bulk add', '每行一个依赖，可填写 `package` 或 `package==version`': 'Enter one dependency per line as `package` or `package==version`', '添加到列表': 'Add to list',
  '准备构建': 'Preparing build', '{count} 个自定义依赖': '{count} custom dependencies', '依赖构建进度': 'Dependency build progress', '正在等待构建输出': 'Waiting for build output', '取消构建': 'Cancel build', '有未应用修改': 'unapplied changes', '正在提交…': 'Submitting…', '构建并激活': 'Build and activate', '配置已同步': 'Configuration synced',
  '思考中': 'Thinking', '思考完成': 'Thinking complete', '进行中': 'In progress', '失败': 'Failed', '已完成': 'Completed', '正在理解任务并制定计划': 'Understanding the task and planning', '正在执行计划': 'Executing the plan', '正在分析下一步': 'Analyzing the next step', '正在评估执行结果': 'Evaluating the result', '正在组织回答': 'Composing the answer', '正在验证结果': 'Verifying the result', '正在处理': 'Working', '思考': 'Reasoning', '验证': 'Verification', '数据与证据 · {count}': 'Data & evidence · {count}', '关联来源': 'Related source', '来源 · {count}': 'Sources · {count}', '运行工件': 'Run artifacts', '关联输出': 'Related outputs', '其他输出': 'Other outputs', '其他输出 · {count}': 'Other outputs · {count}', '{count} 个输出 · 查看输出': '{count} outputs · View outputs', '查看上方已展示的输出': 'View the output shown above', '预览加载失败': 'Preview failed to load', '隔离预览': 'Isolated preview',
  '生效策略': 'Effective policy', '状态版本': 'State version', '策略调整': 'Policy adjustment', '终态原因': 'Terminal reason', '未记录镜像': 'Image not recorded', '截断日志': 'Truncated logs',
  '推广内容': 'Promoted content', '广告': 'Ad', '关闭广告': 'Close ad',
  '数据存储不可用': 'Data storage unavailable', '大模型尚未配置': 'Model is not configured', '大模型服务异常': 'Model service error', '搜索服务异常': 'Search service error', '网页访问服务异常': 'Web access service error', '后端错误未分类': 'Unclassified backend error', '内部运行时异常': 'Internal runtime error', '无法完成此操作': 'Unable to complete this action', '错误类型：': 'Error type: ', '诊断编号：': 'Diagnostic ID: ', '知道了': 'Dismiss',
  '你': 'You', '审计详情': 'Audit details', '暂无 Memory 写入。': 'No memory writes yet.', '提交了一个任务': 'Submitted a task',
  '正在搜索候选来源...': 'Searching for candidate sources...', '正在阅读和验证来源...': 'Reading and verifying sources...', '正在反思并调整策略...': 'Reflecting and adjusting strategy...', '正在验证证据...': 'Verifying evidence...', '正在处理...': 'Working...',
  '正在整理并验证结果…': 'Structuring and verifying the result…',
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
