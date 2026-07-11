import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { createRun, getRun } from './api';
import { I18nProvider, useI18n } from './i18n';
import { ThemeProvider, useTheme } from './theme';
import type { AgentTurnView, ChatMessage, RunEvent, RunView, ToolCallView } from './types';

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked']);

export function App() {
  return <I18nProvider><ThemeProvider><AppContent /></ThemeProvider></I18nProvider>;
}

function AppContent() {
  const { language, t } = useI18n();
  const [goal, setGoal] = useState('帮我总结 Astra 当前 Web Agent 能验证哪些证据');
  const [run, setRun] = useState<RunView | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'chat' | 'settings'>('chat');
  const [usageOpen, setUsageOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState('Astra Pro');
  const [reflectionEnabled, setReflectionEnabled] = useState(true);
  const [reasoningEffort, setReasoningEffort] = useState('均衡');
  const [planningStrategy, setPlanningStrategy] = useState('自适应');
  const [reflectionTrigger, setReflectionTrigger] = useState('按需');
  const [settingsCategory, setSettingsCategory] = useState('工具');
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!attachOpen && !modelOpen) {
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
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setAttachOpen(false);
        setModelOpen(false);
      }
    }

    document.addEventListener('pointerdown', closeOnOutsideInteraction);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideInteraction);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [attachOpen, modelOpen]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError(t('请输入任务目标'));
      return;
    }
    setError(null);
    setLoading(true);
    setEvents([]);
    try {
      const created = await createRun(trimmedGoal);
      const current = await getRun(created.run_id);
      setRun(current);
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
    const source = new EventSource(`/api/runs/${run.id}/events`);
    const eventTypes = [
      'run.created',
      'run.status_changed',
      'step.created',
      'step.updated',
      'tool_call.started',
      'tool_call.completed',
      'artifact.created',
      'agent_turn.created',
      'agent_turn.updated',
      'memory.read',
      'memory.write',
      'memory.write_rejected',
      'reflection.created',
      'verification.created',
    ];
    for (const type of eventTypes) {
      source.addEventListener(type, (message) => {
        const event = JSON.parse((message as MessageEvent).data) as RunEvent;
        setEvents((items) => mergeEvents(items, [event]));
      });
    }
    const refresh = window.setInterval(async () => {
      const next = await getRun(run.id);
      setRun(next);
      setEvents((items) => mergeEvents(items, next.events));
      if (terminalStatuses.has(next.status)) {
        source.close();
        window.clearInterval(refresh);
      }
    }, 700);
    return () => {
      source.close();
      window.clearInterval(refresh);
    };
  }, [run?.id, run?.status]);

  const visibleEvents = useMemo(() => mergeEvents(events, run?.events ?? []), [events, run]);
  const messages = useMemo(() => buildConversation(run), [run]);

  function startNewChat() {
    setRun(null);
    setEvents([]);
    setError(null);
    setGoal('');
    setView('chat');
  }

  return (
    <main className="app-layout">
      <Sidebar
        run={run}
        activeView={view}
        onNewChat={startNewChat}
        onOpenSettings={() => setView('settings')}
        onOpenUsage={() => setUsageOpen(true)}
      />

      <section className="workspace">
        {view === 'settings' ? (
          <SettingsView
            activeCategory={settingsCategory}
            onCategoryChange={setSettingsCategory}
            onClose={() => setView('chat')}
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
            <textarea
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder={t('输入任务 / 继续追问...')}
            />
            <div className="model-menu-wrap" ref={modelMenuRef}>
              <button className="model-selector" type="button" aria-label={`${t('当前模型')}${language === 'zh-CN' ? '：' : ': '}${selectedModel}`} onClick={() => {
                setModelOpen((open) => !open);
                setAttachOpen(false);
              }}>
                <span>{selectedModel}</span><small>{t(reasoningEffort)} · {reflectionEnabled ? `${t(reflectionTrigger)} ${t('反思')}` : t('反思关闭')}</small><b>⌄</b>
              </button>
              {modelOpen && (
                <ModelMenu
                  selectedModel={selectedModel}
                  onModelChange={setSelectedModel}
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

        {run && <AuditDrawer run={run} events={visibleEvents} />}
        </>}
      </section>
      {usageOpen && <UsageModal run={run} onClose={() => setUsageOpen(false)} />}
    </main>
  );
}

function Sidebar({ run, activeView, onNewChat, onOpenSettings, onOpenUsage }: {
  run: RunView | null;
  activeView: 'chat' | 'settings';
  onNewChat: () => void;
  onOpenSettings: () => void;
  onOpenUsage: () => void;
}) {
  const { t } = useI18n();
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">A</div>
        <div>
          <strong>Astra</strong>
          <span>Agent Console</span>
        </div>
      </div>

      <button className="new-chat-button" type="button" onClick={onNewChat}>
        <span>+</span>
        {t('新对话')}
      </button>

      <nav className="side-section">
        <span className="side-title">{t('历史对话')}</span>
        <button className={`history-item ${run ? 'active' : ''}`} type="button">
          <span>{run ? run.summary || t('当前 Web Agent 会话') : t('暂无会话')}</span>
          {run && <small>{statusLabel(run.status)}</small>}
        </button>
      </nav>

      <div className="sidebar-bottom">
        <button className="side-action" type="button" onClick={onOpenUsage}>
          <span>{t('用量统计')}</span>
          <small>{run?.tool_calls.length ?? 0} calls</small>
        </button>
        <button className={`side-action ${activeView === 'settings' ? 'active' : ''}`} type="button" onClick={onOpenSettings}>
          <span>{t('设置')}</span>
          <small>{t('本地配置')}</small>
        </button>
      </div>
    </aside>
  );
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

const settingCategories = ['工具', '运行时', '记忆', '验证与安全', '界面', '数据与隐私'];

function SettingsView({ activeCategory, onCategoryChange, onClose }: {
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  onClose: () => void;
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
            <button className={category === activeCategory ? 'active' : ''} type="button" key={category} onClick={() => onCategoryChange(category)}>{t(category)}</button>
          ))}
        </nav>
        <div className="settings-content">
          <SettingSection category={activeCategory} />
        </div>
      </div>
    </section>
  );
}

function SettingSection({ category }: { category: string }) {
  const { language, setLanguage, t } = useI18n();
  const { mode, setMode } = useTheme();
  if (category === '工具') return <SettingsGroup title="工具" description="管理 Agent 可用工具及其调用策略。"><div className="capability-settings"><CapabilityItem title="Web Search" detail="搜索公开网页并生成候选来源" state="已启用" /><CapabilityItem title="Web Fetch" detail="自适应提取页面主要内容" state="已启用" /><CapabilityItem title="文件分析" detail="解析上传的文档、代码与数据" state="即将支持" enabled={false} /><CapabilityItem title="图像理解" detail="识别并分析图片内容" state="即将支持" enabled={false} /></div><SettingRow title="工具调用确认" description="工具可能修改数据、产生费用或影响外部系统时请求确认"><TranslatedSelect defaultValue="risk" options={[['risk', '仅高风险工具'], ['always', '每次调用'], ['never', '从不确认']]} /></SettingRow><SettingRow title="工具调用上限" description="限制单次任务可执行的工具调用总数"><TranslatedSelect defaultValue="10" options={['5', '10', '20']} /></SettingRow><SettingRow title="并行工具调用" description="并发执行相互独立且无副作用冲突的工具"><Toggle checked /></SettingRow><SettingRow title="工具失败重试" description="仅重试临时网络错误和明确标记为可恢复的工具错误"><TranslatedSelect defaultValue="2" options={[['0', '不重试'], ['1', '1'], ['2', '2'], ['3', '3']]} /></SettingRow></SettingsGroup>;
  if (category === '运行时') return <SettingsGroup title="运行时" description="管理 Agent 的执行环境、生命周期和任务级资源边界。"><div className="runtime-summary"><div><span>{t('执行环境')}</span><strong>{t('本地沙盒')}</strong></div><div><span>{t('任务状态')}</span><strong>{t('可恢复')}</strong></div><div><span>{t('网络')}</span><strong>{t('按需授权')}</strong></div></div><SettingRow title="沙盒模式" description="限定 Agent 可读取和修改的文件系统范围"><TranslatedSelect defaultValue="workspace" options={[['readonly', '只读'], ['workspace', '工作区可写'], ['container', '隔离容器']]} /></SettingRow><SettingRow title="网络策略" description="限制运行环境可访问的外部网络范围"><TranslatedSelect defaultValue="approval" options={[['off', '禁用'], ['approval', '公开网络，按需授权'], ['allowlist', '仅允许列表']]} /></SettingRow><SettingRow title="命令执行确认" description="命令可能修改环境或影响外部系统时请求确认"><TranslatedSelect defaultValue="risk" options={[['risk', '仅高风险命令'], ['always', '每次执行'], ['never', '从不确认']]} /></SettingRow><SettingRow title="最大 Agent 轮次" description="达到上限后停止循环并输出当前结果"><TranslatedSelect defaultValue="12" options={['6', '12', '20']} /></SettingRow><SettingRow title="单次运行时限" description="超时后停止任务并保留可恢复的运行状态"><TranslatedSelect defaultValue="30" options={[['10', `10 ${t('分钟')}`], ['30', `30 ${t('分钟')}`], ['60', `60 ${t('分钟')}`]]} /></SettingRow><SettingRow title="后台继续运行" description="离开当前对话后继续执行，并保留状态通知"><Toggle checked /></SettingRow><SettingRow title="保留运行工件" description="保存运行状态、验证报告和失败现场用于审计"><Toggle checked /></SettingRow></SettingsGroup>;
  if (category === '记忆') return <SettingsGroup title="记忆" description="管理 Agent 在单次任务和不同对话之间保留的信息。"><SettingRow title="运行记忆" description="在当前任务中保留来源摘要和决策线索"><Toggle checked /></SettingRow><SettingRow title="跨对话记忆" description="在新对话中使用已确认的偏好与事实"><Toggle /></SettingRow><SettingRow title="写入阈值" description="仅保存高于该置信度的结构化记忆"><TranslatedSelect defaultValue="80" options={[['70', '70%'], ['80', '80%'], ['90', '90%']]} /></SettingRow><SettingRow title="记忆保留期" description="到期后自动清理非固定记忆"><TranslatedSelect defaultValue="30" options={[['7', `7 ${t('天')}`], ['30', `30 ${t('天')}`], ['forever', '永久']]} /></SettingRow></SettingsGroup>;
  if (category === '验证与安全') return <SettingsGroup title="验证与安全" description="定义最终答案必须满足的证据和质量标准。"><SettingRow title="回答前验证" description="生成最终答案前检查证据覆盖、来源冲突和失败项"><Toggle checked /></SettingRow><SettingRow title="最低独立来源数" description="总结类任务至少需要多少个相互独立的来源"><TranslatedSelect defaultValue="2" options={['1', '2', '3']} /></SettingRow><SettingRow title="来源质量阈值" description="低于阈值的来源会被标记，并降低其证据权重"><TranslatedSelect defaultValue="70" options={[['50', '50%'], ['70', '70%'], ['85', '85%']]} /></SettingRow><SettingRow title="冲突处理" description="来源结论不一致时保留分歧，不强行合并为单一答案"><TranslatedSelect defaultValue="disclose" options={[['disclose', '披露冲突'], ['retry', '继续查证'], ['block', '阻止回答']]} /></SettingRow></SettingsGroup>;
  if (category === '界面') return <SettingsGroup title="界面" description="调整工作区的信息密度和运行过程展示。"><SettingRow title="语言" description="选择界面显示语言"><select value={language} onChange={(event) => setLanguage(event.target.value as 'zh-CN' | 'en')}><option value="zh-CN">中文</option><option value="en">English</option></select></SettingRow><SettingRow title="主题模式" description="选择界面外观，或随操作系统自动切换"><select value={mode} onChange={(event) => setMode(event.target.value as 'system' | 'light' | 'dark')}><option value="system">{t('跟随系统')}</option><option value="light">{t('浅色模式')}</option><option value="dark">{t('暗色模式')}</option></select></SettingRow><SettingRow title="过程展示" description="在对话中显示工具调用和反思摘要"><Toggle checked /></SettingRow><SettingRow title="审计面板" description="任务完成后显示证据、事件和记忆"><Toggle checked /></SettingRow><SettingRow title="信息密度" description="控制对话和面板的间距"><TranslatedSelect defaultValue="compact" options={[['compact', '紧凑'], ['comfortable', '舒适']]} /></SettingRow></SettingsGroup>;
  if (category === '数据与隐私') return <SettingsGroup title="数据与隐私" description="控制运行数据、抓取内容和诊断信息的保存方式。"><SettingRow title="保存运行记录" description="保留对话、工具调用和验证报告"><Toggle checked /></SettingRow><SettingRow title="保存抓取正文" description="将网页正文写入本地工件存储"><Toggle /></SettingRow><SettingRow title="诊断日志" description="记录不包含正文的性能与错误信息"><Toggle checked /></SettingRow><button className="danger-button" type="button">{t('清除本地运行数据')}</button></SettingsGroup>;
  return null;
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
  return <div className="setting-row"><div><strong>{t(title)}</strong><span>{t(description)}</span></div>{children}</div>;
}

function Toggle({ checked = false, onChange }: { checked?: boolean; onChange?: (checked: boolean) => void }) {
  const [localChecked, setLocalChecked] = useState(checked);
  const value = onChange ? checked : localChecked;
  return <button className={`toggle ${value ? 'on' : ''}`} type="button" role="switch" aria-checked={value} onClick={() => onChange ? onChange(!value) : setLocalChecked(!value)}><span /></button>;
}

function ModelMenu({ selectedModel, onModelChange, reasoningEffort, onReasoningEffortChange, planningStrategy, onPlanningStrategyChange, reflectionEnabled, onReflectionChange, reflectionTrigger, onReflectionTriggerChange }: {
  selectedModel: string;
  onModelChange: (model: string) => void;
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
  return <div className="floating-menu model-menu"><div className="menu-heading">{t('模型')}</div>{[['Astra Pro', '复杂研究与多步任务'], ['Astra Flash', '快速问答与轻量搜索'], ['GPT-5', '通用推理模型']].map(([model, detail]) => <button className={`model-option ${selectedModel === model ? 'selected' : ''}`} type="button" key={model} onClick={() => onModelChange(model)}><div><strong>{model}</strong><small>{t(detail)}</small></div><span>{selectedModel === model ? '✓' : ''}</span></button>)}<div className="menu-divider" /><div className="menu-heading">{t('对话策略')}</div><MenuChoice label="推理强度" value={reasoningEffort} options={['快速', '均衡', '深入']} onChange={onReasoningEffortChange} /><MenuChoice label="规划策略" value={planningStrategy} options={['直接', '自适应', '先规划']} onChange={onPlanningStrategyChange} /><div className="menu-toggle"><div><strong>{t('反思循环')}</strong><small>{t('检查结果并修订下一步策略')}</small></div><Toggle checked={reflectionEnabled} onChange={onReflectionChange} /></div>{reflectionEnabled && <MenuChoice label="触发方式" value={reflectionTrigger} options={['失败时', '按需', '每轮']} onChange={onReflectionTriggerChange} />}</div>;
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
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="usage-modal" role="dialog" aria-modal="true" aria-label={t('用量统计')} onMouseDown={(event) => event.stopPropagation()}><header><div><span>{t('当前对话')}</span><h2>{t('用量统计')}</h2></div><button className="close-button" type="button" aria-label={t('关闭用量统计')} onClick={onClose}>×</button></header><div className="usage-primary"><div><span>{t('模型调用')}</span><strong>{turns}</strong><small>{t('次决策 / 生成')}</small></div><div><span>{t('Token 用量')}</span><strong>{estimatedTokens.toLocaleString(language)}</strong><small>{t('前端估算')}</small></div></div><div className="usage-grid"><div><span>{t('工具调用')}</span><strong>{calls}</strong></div><div><span>{t('成功率')}</span><strong>{successRate}%</strong></div><div><span>{t('证据来源')}</span><strong>{run?.result?.sources.length ?? 0}</strong></div><div><span>{t('Agent 轮次')}</span><strong>{turns}</strong></div><div><span>{t('Memory 写入')}</span><strong>{run?.memories?.length ?? 0}</strong></div><div><span>{t('验证警告')}</span><strong>{run?.verification_report?.caveat_count ?? 0}</strong></div></div><p className="usage-note">{t('精确输入、输出和缓存 Token 将在模型网关接入后由后端返回。')}</p></section></div>;
}

function MessageBubble({ message, run }: { message: ChatMessage; run: RunView | null }) {
  const { t } = useI18n();
  const role = message.role === 'user' ? 'user' : message.role === 'tool' ? 'tool' : 'assistant';
  const turnIndex = Number(message.metadata.turn_index ?? 0);
  const turn = run?.turns?.find((item) => item.turn_index === turnIndex);

  return (
    <article className={`bubble ${role}`}>
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

function AuditDrawer({ run, events }: { run: RunView; events: RunEvent[] }) {
  const { t } = useI18n();
  return (
    <details className="audit-drawer">
      <summary>{t('审计详情')}</summary>
      <div className="audit-grid">
        <section>
          <h3>Turns</h3>
          {run.turns?.map((turn) => (
            <div className="audit-row" key={turn.id}>
              <strong>{turn.turn_index}. {turn.decision_type}</strong>
              <span>{turn.reasoning_summary}</span>
            </div>
          ))}
        </section>
        <section>
          <h3>Memory</h3>
          {run.memories?.length ? run.memories.map((memory) => (
            <div className="audit-row" key={memory.id}>
              <strong>{memory.scope}/{memory.kind} · {Math.round(memory.confidence * 100)}%</strong>
              <span>{memory.content}</span>
            </div>
          )) : <p className="empty">{t('暂无 Memory 写入。')}</p>}
        </section>
        <section>
          <h3>Timeline</h3>
          {run.steps.map((step) => (
            <div className="audit-row" key={step.id}>
              <strong>{step.index}. {step.title}</strong>
              <span>{step.status}</span>
            </div>
          ))}
        </section>
        <section>
          <h3>Events</h3>
          {events.slice(-10).map((event) => (
            <div className="audit-row" key={event.id}>
              <strong>{event.type}</strong>
              <code>{JSON.stringify(event.payload)}</code>
            </div>
          ))}
        </section>
      </div>
    </details>
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

function mergeEvents(left: RunEvent[], right: RunEvent[]) {
  const map = new Map<number, RunEvent>();
  for (const event of [...left, ...right]) {
    map.set(event.id, event);
  }
  return [...map.values()].sort((a, b) => a.id - b.id);
}
