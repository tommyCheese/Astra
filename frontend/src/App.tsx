import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createRun, getRun } from './api';
import { I18nProvider, useI18n } from './i18n';
import { ThemeProvider, useTheme } from './theme';
import type { AgentTurnView, ChatMessage, RunView, ToolCallView } from './types';

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked']);
type ConversationEntry = { id: string; run: RunView; priorMessages: ChatMessage[] };

export function App() {
  return <I18nProvider><ThemeProvider><AppContent /></ThemeProvider></I18nProvider>;
}

function AppContent() {
  const { language, t } = useI18n();
  const [goal, setGoal] = useState('帮我总结 Astra 当前 Web Agent 能验证哪些证据');
  const [run, setRun] = useState<RunView | null>(null);
  const [conversationHistory, setConversationHistory] = useState<ConversationEntry[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [priorMessages, setPriorMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'chat' | 'settings'>('chat');
  const [usageOpen, setUsageOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);
  const [executionMenuOpen, setExecutionMenuOpen] = useState(false);
  const [executionMode, setExecutionMode] = useState<'plan' | 'default' | 'bypass'>('default');
  const [bypassConfirmOpen, setBypassConfirmOpen] = useState(false);
  const [providerConfigs, setProviderConfigs] = useState<ModelProviderConfig[]>(() => initialProviderConfigs);
  const [selectedModelKey, setSelectedModelKey] = useState('openai:gpt-5');
  const [reflectionEnabled, setReflectionEnabled] = useState(true);
  const [reasoningEffort, setReasoningEffort] = useState('均衡');
  const [planningStrategy, setPlanningStrategy] = useState('自适应');
  const [reflectionTrigger, setReflectionTrigger] = useState('按需');
  const [settingsCategory, setSettingsCategory] = useState('工具');
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const executionMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const availableModels = useMemo(() => providerConfigs
    .filter((provider) => provider.enabled)
    .flatMap((provider) => parseModelIds(provider.models).map((model) => ({ key: `${provider.id}:${model}`, model, providerId: provider.id, providerName: provider.name }))), [providerConfigs]);
  const selectedModel = availableModels.find((item) => item.key === selectedModelKey)?.model ?? '';

  useEffect(() => {
    if (availableModels.length && !availableModels.some((item) => item.key === selectedModelKey)) {
      setSelectedModelKey(availableModels[0].key);
    } else if (!availableModels.length && selectedModelKey) {
      setSelectedModelKey('');
    }
  }, [availableModels, selectedModelKey]);

  useEffect(() => {
    if (!attachOpen && !modelOpen && !executionMenuOpen) {
      return;
    }

    function closeOnOutsideInteraction(event: PointerEvent) {
      const target = event.target as Node;
      if (!attachMenuRef.current?.contains(target)) {
        setAttachOpen(false);
      }
      if (!modelMenuRef.current?.contains(target)) {
        setModelOpen(false);
      }
      if (!executionMenuRef.current?.contains(target)) {
        setExecutionMenuOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setAttachOpen(false);
        setModelOpen(false);
        setExecutionMenuOpen(false);
      }
    }

    document.addEventListener('pointerdown', closeOnOutsideInteraction);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideInteraction);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [attachOpen, modelOpen, executionMenuOpen]);

  function rememberConversation(nextRun: RunView, previousMessages: ChatMessage[] = priorMessages) {
    const conversationId = activeConversationId ?? nextRun.task_id;
    setActiveConversationId(conversationId);
    setConversationHistory((items) => [{ id: conversationId, run: nextRun, priorMessages: previousMessages }, ...items.filter((item) => item.id !== conversationId)]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError(t('请输入任务目标'));
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const previousMessages = run ? messages : [];
      const created = await createRun(trimmedGoal, run?.task_id);
      const current = await getRun(created.run_id);
      setPriorMessages(previousMessages);
      setRun(current);
      rememberConversation(current, previousMessages);
      setGoal('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('创建 run 失败'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!run || terminalStatuses.has(run.status)) {
      return;
    }
    const refresh = window.setInterval(async () => {
      const next = await getRun(run.id);
      setRun(next);
      rememberConversation(next);
      if (terminalStatuses.has(next.status)) {
        window.clearInterval(refresh);
      }
    }, 700);
    return () => {
      window.clearInterval(refresh);
    };
  }, [run?.id, run?.status]);

  const messages = useMemo(() => {
    const currentMessages = buildConversation(run).map((message) => ({ ...message, id: `${run?.id ?? 'idle'}:${priorMessages.length}:${message.id}` }));
    return [...priorMessages, ...currentMessages];
  }, [priorMessages, run]);

  function changeView(nextView: 'chat' | 'settings') {
    setView(nextView);
  }

  function startNewChat() {
    setRun(null);
    setActiveConversationId(null);
    setPriorMessages([]);
    setError(null);
    setGoal('');
    changeView('chat');
  }

  return (
    <main className="app-layout">
      <Sidebar
        run={run}
        conversations={conversationHistory}
        activeView={view}
        onNewChat={startNewChat}
        onSelectConversation={(conversation) => {
          setActiveConversationId(conversation.id);
          setPriorMessages(conversation.priorMessages);
          setRun(conversation.run);
          changeView('chat');
        }}
        onOpenSettings={() => changeView('settings')}
        onOpenUsage={() => setUsageOpen(true)}
      />

      <section className="workspace">
        {view === 'settings' ? (
          <SettingsView
            activeCategory={settingsCategory}
            onCategoryChange={setSettingsCategory}
            onClose={() => changeView('chat')}
            providerConfigs={providerConfigs}
            onProviderConfigsChange={setProviderConfigs}
          />
        ) : <>
        <section className="chat-topbar">
          <div>
            <h1>Astra</h1>
            <p>{t('Web Agent · 可审计搜索与抓取')}</p>
          </div>
          <span className={`status status-${run?.status ?? 'idle'}`}>{statusLabel(run?.status)}</span>
        </section>

        <section className="chat-surface">
          <QuestionRail messages={messages} />
          <div className="conversation">
            {!messages.length && (
              <div className="welcome">
                <h2>{t('今天想研究什么？')}</h2>
                <p>{t('我会使用 Web 搜索和自适应抓取，边行动边留下可审计证据。')}</p>
              </div>
            )}
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} run={run} />
            ))}
            {run && !terminalStatuses.has(run.status) && (
              <div className="bubble assistant">
                <span className="bubble-label">Astra</span>
                <p>{t(activeState(run))}</p>
              </div>
            )}
          </div>

          <form className="chat-composer" onSubmit={submit}>
            <div className="composer-menu-wrap" ref={attachMenuRef}>
              <button
                className="composer-icon-button"
                type="button"
                aria-label={t('添加内容')}
                title={t('添加内容')}
                onClick={() => {
                  setAttachOpen((open) => !open);
                  setModelOpen(false);
                  setExecutionMenuOpen(false);
                }}
              >+</button>
              {attachOpen && (
                <div className="floating-menu attachment-menu">
                  <button type="button"><span>↥</span><div><strong>{t('上传文件')}</strong><small>{t('文档、代码与数据')}</small></div></button>
                  <button type="button"><span>▧</span><div><strong>{t('添加图片')}</strong><small>{t('分析图像内容')}</small></div></button>
                  <button type="button"><span>⌁</span><div><strong>{t('连接来源')}</strong><small>{t('即将支持')}</small></div></button>
                </div>
              )}
            </div>
            <div className="execution-menu-wrap" ref={executionMenuRef}>
              <button className={`execution-mode-button mode-${executionMode}`} type="button" onClick={() => {
                setExecutionMenuOpen((open) => !open);
                setAttachOpen(false);
                setModelOpen(false);
              }}>
                <Icon name={executionMode === 'plan' ? 'route' : executionMode === 'bypass' ? 'autoApprove' : 'requestApprove'} />
                <span>{executionMode === 'plan' ? t('仅规划') : executionMode === 'bypass' ? t('自动批准') : t('请求批准')}</span>
                <i className="execution-mode-chevron" aria-hidden="true" />
              </button>
              {executionMenuOpen && <ExecutionModeMenu value={executionMode} onChange={(mode) => {
                if (mode === 'bypass') setBypassConfirmOpen(true);
                else {
                  setExecutionMode(mode);
                  setExecutionMenuOpen(false);
                }
              }} />}
            </div>
            <textarea
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder={t('输入任务 / 继续追问...')}
            />
            <div className="model-menu-wrap" ref={modelMenuRef}>
              <button className="model-selector" type="button" aria-label={`${t('当前模型')}${language === 'zh-CN' ? '：' : ': '}${selectedModel || t('未配置模型')}`} onClick={() => {
                setModelOpen((open) => !open);
                setAttachOpen(false);
                setExecutionMenuOpen(false);
              }}>
                <span>{selectedModel || t('未配置模型')}</span><small>{t(reasoningEffort)} · {reflectionEnabled ? `${t(reflectionTrigger)} ${t('反思')}` : t('反思关闭')}</small><b>⌄</b>
              </button>
              {modelOpen && (
                <ModelMenu
                  selectedModelKey={selectedModelKey}
                  onModelChange={setSelectedModelKey}
                  modelOptions={availableModels}
                  reasoningEffort={reasoningEffort}
                  onReasoningEffortChange={setReasoningEffort}
                  planningStrategy={planningStrategy}
                  onPlanningStrategyChange={setPlanningStrategy}
                  reflectionEnabled={reflectionEnabled}
                  onReflectionChange={setReflectionEnabled}
                  reflectionTrigger={reflectionTrigger}
                  onReflectionTriggerChange={setReflectionTrigger}
                />
              )}
            </div>
            <button className="send-button" type="submit" disabled={loading}>{loading ? '...' : '↑'}</button>
          </form>
          {error && <div className="notice error">{error}</div>}
        </section>

        </>}
      </section>
      {usageOpen && <UsageModal run={run} onClose={() => setUsageOpen(false)} />}
      {bypassConfirmOpen && <BypassConfirmation onCancel={() => setBypassConfirmOpen(false)} onConfirm={() => {
        setExecutionMode('bypass');
        setExecutionMenuOpen(false);
        setBypassConfirmOpen(false);
      }} />}
    </main>
  );
}

function Sidebar({ run, conversations, activeView, onNewChat, onSelectConversation, onOpenSettings, onOpenUsage }: {
  run: RunView | null;
  conversations: ConversationEntry[];
  activeView: 'chat' | 'settings';
  onNewChat: () => void;
  onSelectConversation: (conversation: ConversationEntry) => void;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
}) {
  const { t } = useI18n();
  return (
    <aside className="sidebar">
      <div className="brand">
        <AstraBrandIcon />
        <div>
          <strong>Astra</strong>
          <span>Agent Console</span>
        </div>
      </div>

      <button className="new-chat-button" type="button" onClick={onNewChat}>
        <span className="button-icon"><Icon name="plus" /></span>
        {t('新对话')}
      </button>

      <nav className="side-section">
        <span className="side-title">{t('历史对话')}</span>
        {conversations.length ? conversations.slice(0, 6).map((conversation) => <button className={`history-item ${run?.task_id === conversation.id ? 'active' : ''}`} type="button" key={conversation.id} onClick={() => onSelectConversation(conversation)}><Icon name="message" /><span>{conversationTitle(conversation.run, t('当前 Web Agent 会话'))}</span><small>{statusLabel(conversation.run.status)}</small></button>) : <div className="history-empty">{t('暂无对话')}</div>}
      </nav>

      <div className="sidebar-bottom">
        <button className="side-action" type="button" onClick={onOpenUsage}>
          <Icon name="chart" />
          <span>{t('用量统计')}</span>
          <small>{run?.tool_calls.length ?? 0} calls</small>
        </button>
        <button className={`side-action ${activeView === 'settings' ? 'active' : ''}`} type="button" onClick={onOpenSettings}>
          <Icon name="settings" />
          <span>{t('设置')}</span>
          <small>{t('本地配置')}</small>
        </button>
      </div>
    </aside>
  );
}

function conversationTitle(run: RunView, fallback: string) {
  return run.summary?.trim() || run.chat_messages?.find((message) => message.role === 'user')?.content || fallback;
}

function QuestionRail({ messages }: { messages: ChatMessage[] }) {
  const { t } = useI18n();
  const questions = messages.filter((message) => message.role === 'user');
  if (!questions.length) return null;
  return <nav className="question-rail" aria-label={t('问题导航')}>{questions.map((question, index) => <button className={index === questions.length - 1 ? 'active' : ''} type="button" key={question.id} aria-label={`${t('跳转到问题')} ${index + 1}`} onClick={() => document.getElementById(`message-${question.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}><span /><div className="question-preview"><strong>{t('问题')} {index + 1}</strong><p>{question.content}</p></div></button>)}</nav>;
}

function CapabilityItem({ title, detail, state, enabled = true }: { title: string; detail: string; state: string; enabled?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="capability-item">
      <div>
        <strong>{t(title)}</strong>
        <span>{t(detail)}</span>
      </div>
      <span className={`capability-state ${enabled ? 'enabled' : ''}`}>{t(state)}</span>
    </div>
  );
}

function AstraBrandIcon() {
  const { t } = useI18n();
  const clickTimes = useRef<number[]>([]);
  const clearEffectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [burstId, setBurstId] = useState<number | null>(null);

  useEffect(() => () => {
    if (clearEffectTimer.current) clearTimeout(clearEffectTimer.current);
  }, []);

  function handleClick() {
    const now = Date.now();
    clickTimes.current = [...clickTimes.current.filter((time) => now - time < 1200), now];
    if (clickTimes.current.length < 5) return;

    clickTimes.current = [];
    setBurstId((current) => (current ?? 0) + 1);
    if (clearEffectTimer.current) clearTimeout(clearEffectTimer.current);
    clearEffectTimer.current = setTimeout(() => setBurstId(null), 1600);
  }

  return (
    <div className="brand-icon-wrap">
      <button className="brand-mark-trigger" type="button" aria-label={t('Astra 图标')} onClick={handleClick}>
        <img className="brand-mark" src="/astra.svg" alt="" />
      </button>
      {burstId !== null && (
        <div className="astra-burst" data-testid="astra-burst" key={burstId} aria-hidden="true">
          <span className="astra-core-flash" />
          <span className="astra-glow" />
          <span className="astra-orbit" />
          <span className="astra-orbit orbit-two" />
          <span className="astra-firework firework-one" />
          <span className="astra-firework firework-two" />
          <span className="astra-firework firework-three" />
          {Array.from({ length: 22 }, (_, index) => <i className={`astra-star star-${index + 1}`} key={index} />)}
        </div>
      )}
    </div>
  );
}

const settingCategories = ['模型管理', '工具', '运行时', '记忆', '验证与安全', '界面', '数据与隐私'];
const settingCategoryIcons: Record<string, IconName> = {
  '模型管理': 'sparkle',
  '工具': 'tools',
  '运行时': 'terminal',
  '记忆': 'brain',
  '验证与安全': 'shield',
  '界面': 'palette',
  '数据与隐私': 'lock',
};

function SettingsView({ activeCategory, onCategoryChange, onClose, providerConfigs, onProviderConfigsChange }: {
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  onClose: () => void;
  providerConfigs: ModelProviderConfig[];
  onProviderConfigsChange: (configs: ModelProviderConfig[]) => void;
}) {
  const { t } = useI18n();
  return (
    <section className="settings-page">
      <header className="settings-header">
        <div><span>{t('工作区')}</span><h1>{t('设置')}</h1></div>
        <button className="close-button" type="button" aria-label={t('关闭设置')} onClick={onClose}>×</button>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label={t('设置类别')}>
          {settingCategories.map((category) => (
            <button className={category === activeCategory ? 'active' : ''} type="button" key={category} onClick={() => onCategoryChange(category)}><Icon name={settingCategoryIcons[category]} /><span>{t(category)}</span></button>
          ))}
        </nav>
        <div className="settings-content">
          <SettingSection category={activeCategory} providerConfigs={providerConfigs} onProviderConfigsChange={onProviderConfigsChange} />
        </div>
      </div>
    </section>
  );
}

function SettingSection({ category, providerConfigs, onProviderConfigsChange }: { category: string; providerConfigs: ModelProviderConfig[]; onProviderConfigsChange: (configs: ModelProviderConfig[]) => void }) {
  const { language, setLanguage, t } = useI18n();
  const { mode, setMode } = useTheme();
  if (category === '模型管理') return <ModelManagement providers={providerConfigs} onChange={onProviderConfigsChange} />;
  if (category === '工具') return <SettingsGroup title="工具" description="管理 Agent 可用工具及其调用策略。"><div className="capability-settings"><CapabilityItem title="Web Search" detail="搜索公开网页并生成候选来源" state="已启用" /><CapabilityItem title="Web Fetch" detail="自适应提取页面主要内容" state="已启用" /><CapabilityItem title="文件分析" detail="解析上传的文档、代码与数据" state="即将支持" enabled={false} /><CapabilityItem title="图像理解" detail="识别并分析图片内容" state="即将支持" enabled={false} /></div><SettingRow title="工具调用确认" description="工具可能修改数据、产生费用或影响外部系统时请求确认"><TranslatedSelect defaultValue="risk" options={[['risk', '仅高风险工具'], ['always', '每次调用'], ['never', '从不确认']]} /></SettingRow><SettingRow title="工具调用上限" description="限制单次任务可执行的工具调用总数"><TranslatedSelect defaultValue="10" options={['5', '10', '20']} /></SettingRow><SettingRow title="并行工具调用" description="并发执行相互独立且无副作用冲突的工具"><Toggle checked /></SettingRow><SettingRow title="工具失败重试" description="仅重试临时网络错误和明确标记为可恢复的工具错误"><TranslatedSelect defaultValue="2" options={[['0', '不重试'], ['1', '1'], ['2', '2'], ['3', '3']]} /></SettingRow></SettingsGroup>;
  if (category === '运行时') return <SettingsGroup title="运行时" description="管理 Agent 的执行环境、生命周期和任务级资源边界。"><div className="runtime-summary"><div><span>{t('执行环境')}</span><strong>{t('本地沙盒')}</strong></div><div><span>{t('任务状态')}</span><strong>{t('可恢复')}</strong></div><div><span>{t('网络')}</span><strong>{t('按需授权')}</strong></div></div><SettingRow title="沙盒模式" description="限定 Agent 可读取和修改的文件系统范围"><TranslatedSelect defaultValue="workspace" options={[['readonly', '只读'], ['workspace', '工作区可写'], ['container', '隔离容器']]} /></SettingRow><SettingRow title="网络策略" description="限制运行环境可访问的外部网络范围"><TranslatedSelect defaultValue="approval" options={[['off', '禁用'], ['approval', '公开网络，按需授权'], ['allowlist', '仅允许列表']]} /></SettingRow><SettingRow title="命令执行确认" description="命令可能修改环境或影响外部系统时请求确认"><TranslatedSelect defaultValue="risk" options={[['risk', '仅高风险命令'], ['always', '每次执行'], ['never', '从不确认']]} /></SettingRow><SettingRow title="最大 Agent 轮次" description="达到上限后停止循环并输出当前结果"><TranslatedSelect defaultValue="12" options={['6', '12', '20']} /></SettingRow><SettingRow title="单次运行时限" description="超时后停止任务并保留可恢复的运行状态"><TranslatedSelect defaultValue="30" options={[['10', `10 ${t('分钟')}`], ['30', `30 ${t('分钟')}`], ['60', `60 ${t('分钟')}`]]} /></SettingRow><SettingRow title="后台继续运行" description="离开当前对话后继续执行，并保留状态通知"><Toggle checked /></SettingRow><SettingRow title="保留运行工件" description="保存运行状态、验证报告和失败现场用于审计"><Toggle checked /></SettingRow></SettingsGroup>;
  if (category === '记忆') return <SettingsGroup title="记忆" description="管理 Agent 在单次任务和不同对话之间保留的信息。"><SettingRow title="运行记忆" description="在当前任务中保留来源摘要和决策线索"><Toggle checked /></SettingRow><SettingRow title="跨对话记忆" description="在新对话中使用已确认的偏好与事实"><Toggle /></SettingRow><SettingRow title="写入阈值" description="仅保存高于该置信度的结构化记忆"><TranslatedSelect defaultValue="80" options={[['70', '70%'], ['80', '80%'], ['90', '90%']]} /></SettingRow><SettingRow title="记忆保留期" description="到期后自动清理非固定记忆"><TranslatedSelect defaultValue="30" options={[['7', `7 ${t('天')}`], ['30', `30 ${t('天')}`], ['forever', '永久']]} /></SettingRow></SettingsGroup>;
  if (category === '验证与安全') return <SettingsGroup title="验证与安全" description="定义 Agent 在报告完成前必须满足的通用验证要求。"><SettingRow title="完成前验证" description="提交结果前运行与任务类型匹配的验证器"><Toggle checked /></SettingRow><SettingRow title="验证强度" description="控制验证覆盖范围以及失败后的检查深度"><TranslatedSelect defaultValue="standard" options={[['basic', '基础'], ['standard', '标准'], ['strict', '严格']]} /></SettingRow><SettingRow title="验证失败处理" description="验证未通过时决定继续修复、带警告返回或停止任务"><TranslatedSelect defaultValue="repair" options={[['repair', '自动修复'], ['warn', '带警告返回'], ['block', '停止任务']]} /></SettingRow></SettingsGroup>;
  if (category === '界面') return <SettingsGroup title="界面" description="调整工作区的信息密度和运行过程展示。"><SettingRow title="语言" description="选择界面显示语言"><select value={language} onChange={(event) => setLanguage(event.target.value as 'zh-CN' | 'en')}><option value="zh-CN">中文</option><option value="en">English</option></select></SettingRow><SettingRow title="主题模式" description="选择界面外观，或随操作系统自动切换"><select value={mode} onChange={(event) => setMode(event.target.value as 'system' | 'light' | 'dark')}><option value="system">{t('跟随系统')}</option><option value="light">{t('浅色模式')}</option><option value="dark">{t('暗色模式')}</option></select></SettingRow><SettingRow title="过程展示" description="在对话中显示工具调用和反思摘要"><Toggle checked /></SettingRow><SettingRow title="审计面板" description="任务完成后显示证据、事件和记忆"><Toggle checked /></SettingRow><SettingRow title="信息密度" description="控制对话和面板的间距"><TranslatedSelect defaultValue="compact" options={[['compact', '紧凑'], ['comfortable', '舒适']]} /></SettingRow></SettingsGroup>;
  if (category === '数据与隐私') return <SettingsGroup title="数据与隐私" description="控制任务记录、工具内容和诊断信息的保存方式。"><SettingRow title="保存运行记录" description="保留对话、工具调用元数据和验证报告"><Toggle checked /></SettingRow><SettingRow title="工具内容保留" description="决定是否保存工具返回的正文、文件内容或结构化结果"><TranslatedSelect defaultValue="metadata" options={[['none', '不保留内容'], ['metadata', '仅保留元数据'], ['full', '保留完整输出']]} /></SettingRow><SettingRow title="诊断日志" description="记录不包含工具内容的性能与错误信息"><Toggle checked /></SettingRow><button className="danger-button" type="button">{t('清除本地运行数据')}</button></SettingsGroup>;
  return null;
}

type ModelProviderId = 'openai' | 'anthropic' | 'google' | 'deepseek' | 'qwen' | 'siliconflow' | 'azure' | 'compatible';
type ModelProviderConfig = {
  id: ModelProviderId;
  name: string;
  enabled: boolean;
  endpoint: string;
  models: string;
  organization: string;
  apiKey: string;
};

const modelProviders: Array<{ id: ModelProviderId; name: string; detail: string; mark: string }> = [
  { id: 'openai', name: 'OpenAI', detail: 'Responses API', mark: 'O' },
  { id: 'anthropic', name: 'Anthropic', detail: 'Claude API', mark: 'A' },
  { id: 'google', name: 'Google Gemini', detail: 'Generative Language API', mark: 'G' },
  { id: 'deepseek', name: 'DeepSeek', detail: 'DeepSeek 开放平台', mark: 'D' },
  { id: 'qwen', name: '通义千问', detail: '阿里云百炼', mark: 'Q' },
  { id: 'siliconflow', name: 'SiliconFlow', detail: '硅基流动模型广场', mark: 'S' },
  { id: 'azure', name: 'Azure OpenAI', detail: 'Azure AI Foundry', mark: 'Az' },
  { id: 'compatible', name: 'OpenAI 兼容', detail: 'Ollama、vLLM、OpenRouter', mark: '↗' },
];

const providerDefaults: Record<ModelProviderId, { endpoint: string; models: string; organization: string }> = {
  openai: { endpoint: 'https://api.openai.com/v1', models: 'gpt-5, gpt-5-mini', organization: '' },
  anthropic: { endpoint: 'https://api.anthropic.com', models: 'claude-sonnet-4, claude-opus-4', organization: '' },
  google: { endpoint: 'https://generativelanguage.googleapis.com', models: 'gemini-2.5-pro, gemini-2.5-flash', organization: '' },
  deepseek: { endpoint: 'https://api.deepseek.com', models: 'deepseek-v4-pro, deepseek-v4-flash', organization: '' },
  qwen: { endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: 'qwen3.7-plus, qwen-plus', organization: '' },
  siliconflow: { endpoint: 'https://api.siliconflow.cn/v1', models: 'deepseek-ai/DeepSeek-V3, Qwen/Qwen2.5-72B-Instruct', organization: '' },
  azure: { endpoint: '', models: '', organization: '2025-04-01-preview' },
  compatible: { endpoint: 'http://127.0.0.1:11434/v1', models: '', organization: '' },
};

const initialProviderConfigs: ModelProviderConfig[] = modelProviders.map((provider) => ({
  id: provider.id,
  name: provider.name,
  enabled: provider.id === 'openai',
  ...providerDefaults[provider.id],
  apiKey: '',
}));

function parseModelIds(models: string) {
  return [...new Set(models.split(',').map((model) => model.trim()).filter(Boolean))];
}

function ModelManagement({ providers, onChange }: { providers: ModelProviderConfig[]; onChange: (providers: ModelProviderConfig[]) => void }) {
  const { t } = useI18n();
  const [selectedProvider, setSelectedProvider] = useState<ModelProviderId>('openai');
  const [showKey, setShowKey] = useState(false);
  const [connectionState, setConnectionState] = useState('未验证');
  const provider = providers.find((item) => item.id === selectedProvider)!;
  const providerMeta = modelProviders.find((item) => item.id === selectedProvider)!;

  function selectProvider(id: ModelProviderId) {
    setSelectedProvider(id);
    setShowKey(false);
    setConnectionState('未验证');
  }

  function updateProvider(patch: Partial<ModelProviderConfig>) {
    onChange(providers.map((item) => item.id === selectedProvider ? { ...item, ...patch } : item));
    setConnectionState('未验证');
  }

  function toggleProvider() {
    updateProvider({ enabled: !provider.enabled });
  }

  return (
    <SettingsGroup title="模型管理" description="配置模型供应商连接、凭据和 Agent 可选模型。">
      <div className="provider-workspace">
        <aside className="provider-list" aria-label={t('模型供应商')}>
          <div className="provider-list-heading"><span>{t('供应商')}</span><button type="button" aria-label={t('添加供应商')} title={t('添加供应商')} onClick={() => selectProvider('compatible')}>+</button></div>
          {modelProviders.map((item) => (
            <button className={`provider-item ${item.id === selectedProvider ? 'active' : ''}`} type="button" key={item.id} onClick={() => selectProvider(item.id)}>
              <span className={`provider-mark provider-${item.id}`}>{item.mark}</span>
              <span><strong>{item.name}</strong><small>{t(item.detail)}</small></span>
              <i className={providers.find((provider) => provider.id === item.id)?.enabled ? 'connected' : ''} />
            </button>
          ))}
        </aside>

        <section className="provider-editor">
          <header className="provider-editor-header">
            <div><span className={`provider-mark provider-${provider.id}`}>{providerMeta.mark}</span><div><h3>{provider.name}</h3><p>{t(providerMeta.detail)}</p></div></div>
            <label className="provider-enabled"><span>{t('启用')}</span><Toggle checked={provider.enabled} onChange={toggleProvider} /></label>
          </header>

          <div className="provider-form">
            <label><span>{t('API 地址')}</span><small>{t('供应商 API 的基础地址')}</small><input value={provider.endpoint} onChange={(event) => updateProvider({ endpoint: event.target.value })} spellCheck={false} /></label>
            <label><span>{t('API Key')}</span><small>{t('凭据仅在当前页面内存中暂存，接入后端后应写入加密密钥存储')}</small><div className="secret-input"><input type={showKey ? 'text' : 'password'} value={provider.apiKey} onChange={(event) => updateProvider({ apiKey: event.target.value })} placeholder={selectedProvider === 'google' ? 'AIza...' : 'sk-...'} autoComplete="off" /><button type="button" onClick={() => setShowKey((visible) => !visible)}>{t(showKey ? '隐藏' : '显示')}</button></div></label>
            <label><span>{t(selectedProvider === 'azure' ? 'API 版本' : '组织或项目 ID')}</span><small>{t(selectedProvider === 'azure' ? 'Azure OpenAI 请求使用的 API 版本' : '可选，用于供应商侧的项目隔离与计费')}</small><input value={provider.organization} onChange={(event) => updateProvider({ organization: event.target.value })} placeholder={selectedProvider === 'azure' ? '2025-04-01-preview' : t('可选')} /></label>
            <label><span>{t('可用模型 ID')}</span><small>{t('使用逗号分隔，模型选择器将使用这些标识')}</small><textarea value={provider.models} onChange={(event) => updateProvider({ models: event.target.value })} placeholder="model-id-1, model-id-2" /></label>
          </div>

          <div className="provider-advanced">
            <div><strong>{t('请求兼容性')}</strong><small>{t(selectedProvider === 'anthropic' ? 'Anthropic Messages API' : selectedProvider === 'google' ? 'Google generateContent API' : 'OpenAI Responses / Chat Completions')}</small></div>
            <TranslatedSelect defaultValue="auto" options={[['auto', '自动检测'], ['responses', 'Responses API'], ['chat', 'Chat Completions']]} />
          </div>

          <footer className="provider-actions">
            <span className={`connection-state ${connectionState === '连接正常' ? 'success' : ''}`}><i />{t(connectionState)}</span>
            <button className="secondary-button" type="button" onClick={() => setConnectionState(provider.endpoint && (provider.apiKey || selectedProvider === 'compatible') ? '连接正常' : '缺少连接信息')}>{t('测试连接')}</button>
            <button className="primary-button" type="button" onClick={() => setConnectionState('配置已更新')}>{t('保存配置')}</button>
          </footer>
        </section>
      </div>
    </SettingsGroup>
  );
}

function TranslatedSelect({ defaultValue, options }: { defaultValue: string; options: Array<string | [string, string]> }) {
  const { t } = useI18n();
  return <select defaultValue={defaultValue}>{options.map((option) => { const [value, label] = Array.isArray(option) ? option : [option, option]; return <option value={value} key={value}>{t(label)}</option>; })}</select>;
}

function SettingsGroup({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  const { t } = useI18n();
  return <section className="settings-group"><header><h2>{t(title)}</h2><p>{t(description)}</p></header>{children}</section>;
}

function SettingRow({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  const { t } = useI18n();
  if (title === '命令执行确认') return null;
  return <div className="setting-row"><div><strong>{t(title)}</strong><span>{t(description)}</span></div>{children}</div>;
}

function Toggle({ checked = false, onChange }: { checked?: boolean; onChange?: (checked: boolean) => void }) {
  const [localChecked, setLocalChecked] = useState(checked);
  const value = onChange ? checked : localChecked;
  return <button className={`toggle ${value ? 'on' : ''}`} type="button" role="switch" aria-checked={value} onClick={() => onChange ? onChange(!value) : setLocalChecked(!value)}><span /></button>;
}

function ExecutionModeMenu({ value, onChange }: { value: 'plan' | 'default' | 'bypass'; onChange: (mode: 'plan' | 'default' | 'bypass') => void }) {
  const { t } = useI18n();
  const modes = [
    { id: 'plan' as const, title: '仅规划', detail: '只规划任务，不调用工具或执行命令', icon: 'route' as const },
    { id: 'default' as const, title: '请求批准', detail: '自动执行低风险操作，高风险权限需要确认', icon: 'requestApprove' as const },
    { id: 'bypass' as const, title: '自动批准', detail: '自动执行所有命令和工具，不再请求确认', icon: 'autoApprove' as const },
  ];
  return <div className="floating-menu execution-menu"><div className="menu-heading">{t('执行模式')}</div>{modes.map((mode) => <button className={value === mode.id ? 'selected' : ''} type="button" key={mode.id} onClick={() => onChange(mode.id)}><Icon name={mode.icon} /><div><strong>{t(mode.title)}</strong><small>{t(mode.detail)}</small></div><span className="mode-selected-mark">{value === mode.id ? '✓' : ''}</span></button>)}</div>;
}

function BypassConfirmation({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const { t } = useI18n();
  return <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}><section className="confirmation-modal" role="alertdialog" aria-modal="true" aria-labelledby="bypass-title" onMouseDown={(event) => event.stopPropagation()}><div className="warning-mark">!</div><h2 id="bypass-title">{t('启用自动批准模式？')}</h2><p>{t('自动批准模式将允许 Agent 自动执行所有命令和工具，包括可能修改文件、访问网络或影响外部系统的高风险操作。')}</p><div className="confirmation-note"><strong>{t('仅在你信任当前任务和运行环境时启用。')}</strong></div><div className="confirmation-actions"><button className="secondary-button" type="button" onClick={onCancel}>{t('取消')}</button><button className="danger-confirm-button" type="button" onClick={onConfirm}>{t('确认启用自动批准')}</button></div></section></div>;
}

function ModelMenu({ selectedModelKey, onModelChange, modelOptions, reasoningEffort, onReasoningEffortChange, planningStrategy, onPlanningStrategyChange, reflectionEnabled, onReflectionChange, reflectionTrigger, onReflectionTriggerChange }: {
  selectedModelKey: string;
  onModelChange: (modelKey: string) => void;
  modelOptions: Array<{ key: string; model: string; providerId: ModelProviderId; providerName: string }>;
  reasoningEffort: string;
  onReasoningEffortChange: (effort: string) => void;
  planningStrategy: string;
  onPlanningStrategyChange: (strategy: string) => void;
  reflectionEnabled: boolean;
  onReflectionChange: (enabled: boolean) => void;
  reflectionTrigger: string;
  onReflectionTriggerChange: (trigger: string) => void;
}) {
  const { t } = useI18n();
  const groups = modelOptions.reduce<Array<{ providerId: ModelProviderId; providerName: string; models: Array<{ key: string; model: string }> }>>((result, option) => {
    const group = result.find((item) => item.providerId === option.providerId);
    if (group) group.models.push({ key: option.key, model: option.model });
    else result.push({ providerId: option.providerId, providerName: option.providerName, models: [{ key: option.key, model: option.model }] });
    return result;
  }, []);
  return <div className="floating-menu model-menu"><div className="menu-heading">{t('模型')}</div>{groups.length ? groups.map((group) => <div className="model-provider-group" key={group.providerId}><div className="model-provider-heading"><span className={`provider-mark provider-${group.providerId}`}>{modelProviders.find((provider) => provider.id === group.providerId)?.mark}</span><span>{group.providerName}</span></div>{group.models.map((item) => <button className={`model-option ${selectedModelKey === item.key ? 'selected' : ''}`} type="button" key={item.key} onClick={() => onModelChange(item.key)}><div><strong>{item.model}</strong><small>{group.providerName}</small></div><span>{selectedModelKey === item.key ? '✓' : ''}</span></button>)}</div>) : <div className="model-menu-empty">{t('请先在模型管理中启用供应商并配置模型')}</div>}<div className="menu-divider" /><div className="menu-heading">{t('对话策略')}</div><MenuChoice label="推理强度" value={reasoningEffort} options={['快速', '均衡', '深入']} onChange={onReasoningEffortChange} /><MenuChoice label="规划策略" value={planningStrategy} options={['直接', '自适应', '先规划']} onChange={onPlanningStrategyChange} /><div className="menu-toggle"><div><strong>{t('反思循环')}</strong><small>{t('检查结果并修订下一步策略')}</small></div><Toggle checked={reflectionEnabled} onChange={onReflectionChange} /></div>{reflectionEnabled && <MenuChoice label="触发方式" value={reflectionTrigger} options={['失败时', '按需', '每轮']} onChange={onReflectionTriggerChange} />}</div>;
}

function MenuChoice({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  const { t } = useI18n();
  return <div className="menu-choice"><span>{t(label)}</span><div className="segmented-control">{options.map((option) => <button className={value === option ? 'active' : ''} type="button" key={option} onClick={() => onChange(option)}>{t(option)}</button>)}</div></div>;
}

function UsageModal({ run, onClose }: { run: RunView | null; onClose: () => void }) {
  const { language, t } = useI18n();
  const calls = run?.tool_calls.length ?? 0;
  const turns = run?.turns?.length ?? 0;
  const estimatedTokens = run ? Math.max(640, turns * 760 + calls * 420 + (run.result?.findings.length ?? 0) * 180) : 0;
  const succeeded = run?.tool_calls.filter((call) => call.status === 'succeeded').length ?? 0;
  const successRate = calls ? Math.round((succeeded / calls) * 100) : 0;
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="usage-modal" role="dialog" aria-modal="true" aria-label={t('用量统计')} onMouseDown={(event) => event.stopPropagation()}><header><div><span>{t('当前对话')}</span><h2><Icon name="chart" />{t('用量统计')}</h2></div><button className="close-button" type="button" aria-label={t('关闭用量统计')} onClick={onClose}>×</button></header><div className="usage-primary"><div><Icon name="sparkle" /><span>{t('模型调用')}</span><strong>{turns}</strong><small>{t('次决策 / 生成')}</small></div><div><Icon name="token" /><span>{t('Token 用量')}</span><strong>{estimatedTokens.toLocaleString(language)}</strong><small>{t('前端估算')}</small></div></div><div className="usage-grid"><div><Icon name="tools" /><span>{t('工具调用')}</span><strong>{calls}</strong></div><div><Icon name="check" /><span>{t('成功率')}</span><strong>{successRate}%</strong></div><div><Icon name="route" /><span>{t('任务步骤')}</span><strong>{run?.steps.length ?? 0}</strong></div><div><Icon name="refresh" /><span>{t('Agent 轮次')}</span><strong>{turns}</strong></div><div><Icon name="message" /><span>{t('消息轮次')}</span><strong>{run?.chat_messages?.filter((message) => message.role === 'user').length ?? 0}</strong></div><div><Icon name="brain" /><span>{t('Memory 写入')}</span><strong>{run?.memories?.length ?? 0}</strong></div></div><p className="usage-note">{t('精确输入、输出和缓存 Token 将在模型网关接入后由后端返回。')}</p></section></div>;
}

type IconName = 'plus' | 'message' | 'chart' | 'settings' | 'sparkle' | 'tools' | 'terminal' | 'brain' | 'shield' | 'palette' | 'lock' | 'token' | 'check' | 'route' | 'refresh' | 'requestApprove' | 'autoApprove';

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />,
    message: <path d="M20 11.5a7.5 7.5 0 0 1-8 7.48 8.9 8.9 0 0 1-3.63-.78L4 20l1.34-3.58A7.34 7.34 0 0 1 4 12a7.5 7.5 0 0 1 8-7.48A7.5 7.5 0 0 1 20 11.5Z" />,
    chart: <><path d="M4 19V5M4 19h16" /><path d="m7 15 3-3 3 2 5-6" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.06 2.06-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.55V20h-2.9v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06-2.06-2.06.06-.06A1.7 1.7 0 0 0 7.3 14.8a1.7 1.7 0 0 0-1.55-1H5.7v-2.9h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06L9 5.9l.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1-1.55V4.7h2.9v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.06 2.06-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.55 1h.09v2.9h-.09a1.7 1.7 0 0 0-1.55 1Z" /></>,
    sparkle: <path d="m12 3 .9 5.1L18 9l-5.1.9L12 15l-.9-5.1L6 9l5.1-.9L12 3Zm6 12 .45 2.55L21 18l-2.55.45L18 21l-.45-2.55L15 18l2.55-.45L18 15Z" />,
    tools: <><path d="M14 6a4 4 0 0 0-5.48 5.48L3.5 16.5a2.12 2.12 0 0 0 3 3l5.02-5.02A4 4 0 0 0 17 9l-3 1-2-2 1-3Z" /><path d="m15 15 4 4" /></>,
    terminal: <><path d="m5 7 4 4-4 4M12 17h7" /><rect x="3" y="4" width="18" height="16" rx="2" /></>,
    brain: <path d="M9 5.2A3.4 3.4 0 0 0 4.7 8.5 3.2 3.2 0 0 0 5 14.7 3.1 3.1 0 0 0 8 19h1.2V5.2Zm6 0a3.4 3.4 0 0 1 4.3 3.3 3.2 3.2 0 0 1-.3 6.2 3.1 3.1 0 0 1-3 4.3h-1.2V5.2ZM9 9H7m2 4H6m9-4h2m-2 4h3" />,
    shield: <path d="M12 3 19 6v5c0 4.6-3 7.7-7 10-4-2.3-7-5.4-7-10V6l7-3Zm-3 9 2 2 4-4" />,
    palette: <path d="M12 3a9 9 0 1 0 0 18h1.1a1.9 1.9 0 0 0 .5-3.73 1.5 1.5 0 0 1 .4-2.95H16A5 5 0 0 0 21 9c0-3.3-4-6-9-6ZM7.5 11.5h.01M9 7.5h.01m6 0h.01m1.5 4h.01" />,
    lock: <><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v2" /></>,
    token: <><circle cx="12" cy="12" r="8" /><path d="M9 9h6v6H9zM12 6v3m0 6v3m-6-6h3m6 0h3" /></>,
    check: <><circle cx="12" cy="12" r="8" /><path d="m8.5 12 2.2 2.2 4.8-5" /></>,
    route: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M8 6h4a3 3 0 0 1 3 3v6" /></>,
    refresh: <><path d="M20 11a8 8 0 0 0-14.8-3M4 5v3h3" /><path d="M4 13a8 8 0 0 0 14.8 3M20 19v-3h-3" /></>,
    requestApprove: <><path d="M12 3 19 6v5c0 4.6-3 7.7-7 10-4-2.3-7-5.4-7-10V6l7-3Z" /><path d="M12 8v4m0 4h.01" /></>,
    autoApprove: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /><path d="M17.5 4.5 19 3m.5 4H22" /></>,
  };
  return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function MessageBubble({ message, run }: { message: ChatMessage; run: RunView | null }) {
  const { t } = useI18n();
  const role = message.role === 'user' ? 'user' : message.role === 'tool' ? 'tool' : 'assistant';
  const turnIndex = Number(message.metadata.turn_index ?? 0);
  const turn = run?.turns?.find((item) => item.turn_index === turnIndex);

  return (
    <article className={`bubble ${role}`} id={`message-${message.id}`}>
      <span className="bubble-label">{t(labelForRole(message.role))}</span>
      <p>{message.content}</p>
      {turn?.selected_tool && <ToolEvent turn={turn} toolCalls={run?.tool_calls ?? []} />}
      {turn?.reflection && (
        <div className="reflection-card">
          <strong>{t('反思')}</strong>
          <span>{String(turn.reflection.summary ?? message.content)}</span>
        </div>
      )}
      {message.role === 'assistant' && run?.result && <FinalAnswer run={run} />}
    </article>
  );
}

function ToolEvent({ turn, toolCalls }: { turn: AgentTurnView; toolCalls: ToolCallView[] }) {
  const call = toolCalls.find((item) => item.id === turn.tool_call_id);
  const output = call?.output ?? {};
  const url = typeof output.url === 'string' ? output.url : undefined;
  const warnings = Array.isArray(output.warnings) ? output.warnings : [];

  return (
    <div className="tool-event">
      <div>
        <strong>{turn.selected_tool}</strong>
        <span>{call?.status ?? turn.status}{toolCallDetail(output)}</span>
      </div>
      {url && <a href={url} target="_blank" rel="noreferrer">{url}</a>}
      {warnings.map((warning, index) => (
        <p key={index}>{String(warning)}</p>
      ))}
    </div>
  );
}

function FinalAnswer({ run }: { run: RunView }) {
  const result = run.result;
  if (!result) {
    return null;
  }
  const report = run.verification_report ?? result.verification_report;
  return (
    <div className="answer-block">
      {result.findings.map((finding, index) => (
        <p key={index}>{finding.text}</p>
      ))}
      {result.sources.length ? (
        <div className="source-grid">
          {result.sources.map((source) => {
            const quality = result.source_quality?.find((item) => item.url === source.url);
            return (
              <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="source-card">
                <strong>{source.title || source.url}</strong>
                {quality && (
                  <span>{formatScore(quality.quality_score)} · {quality.extraction_strategy || 'unknown'}</span>
                )}
              </a>
            );
          })}
        </div>
      ) : null}
      {[...result.caveats, ...result.verification_notes, ...(report?.notes ?? [])].map((item, index) => (
        <p key={`note-${index}`} className="note">{item}</p>
      ))}
    </div>
  );
}

function buildConversation(run: RunView | null): ChatMessage[] {
  if (!run) {
    return [];
  }
  if (run.chat_messages?.length) {
    return run.chat_messages;
  }
  const messages: ChatMessage[] = [
    {
      id: `${run.id}-user`,
      role: 'user',
      content: run.summary || '提交了一个任务',
      status: 'completed',
      metadata: {},
    },
  ];
  for (const call of run.tool_calls) {
    messages.push({
      id: call.id,
      role: 'tool',
      content: call.tool_name,
      status: call.status,
      metadata: { selected_tool: call.tool_name, output: call.output },
    });
  }
  if (run.result) {
    messages.push({
      id: `${run.id}-answer`,
      role: 'assistant',
      content: run.result.summary,
      status: run.status,
      metadata: {},
    });
  }
  return messages;
}

function activeState(run: RunView) {
  const latest = [...(run.turns ?? [])].sort((a, b) => b.turn_index - a.turn_index)[0];
  if (latest?.selected_tool === 'web_search') return '正在搜索候选来源...';
  if (latest?.selected_tool === 'web_fetch') return '正在阅读和验证来源...';
  if (latest?.decision_type === 'reflect') return '正在反思并调整策略...';
  if (run.status === 'verifying') return '正在验证证据...';
  return '正在处理...';
}

function statusLabel(status?: string) {
  return status ?? 'idle';
}

function labelForRole(role: string) {
  if (role === 'user') return '你';
  if (role === 'tool') return '工具';
  if (role === 'reflection') return '反思';
  return 'Astra';
}

function formatScore(score?: number | null) {
  if (typeof score !== 'number') {
    return 'n/a';
  }
  return `${Math.round(score * 100)}%`;
}

function toolCallDetail(output?: Record<string, unknown> | null) {
  if (!output) {
    return '';
  }
  if (typeof output.candidate_count === 'number') {
    return ` · ${output.candidate_count} candidates`;
  }
  if (typeof output.quality_score === 'number' || typeof output.extraction_strategy === 'string') {
    const strategy = typeof output.extraction_strategy === 'string' ? output.extraction_strategy : 'read';
    const score = typeof output.quality_score === 'number' ? ` · ${formatScore(output.quality_score)}` : '';
    return ` · ${strategy}${score}`;
  }
  return '';
}
