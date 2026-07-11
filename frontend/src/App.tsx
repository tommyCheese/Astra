import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { createRun, getRun } from './api';
import type { AgentTurnView, ChatMessage, RunEvent, RunView, ToolCallView } from './types';

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked']);

export function App() {
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
  const [settingsCategory, setSettingsCategory] = useState('模型与推理');
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
      setError('请输入任务目标');
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
      setError(err instanceof Error ? err.message : '创建 run 失败');
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
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
            reflectionEnabled={reflectionEnabled}
            onReflectionChange={setReflectionEnabled}
            onClose={() => setView('chat')}
          />
        ) : <>
        <section className="chat-topbar">
          <div>
            <h1>Astra</h1>
            <p>Web Agent · 可审计搜索与抓取</p>
          </div>
          <span className={`status status-${run?.status ?? 'idle'}`}>{statusLabel(run?.status)}</span>
        </section>

        <section className="chat-surface">
          <div className="conversation">
            {!messages.length && (
              <div className="welcome">
                <h2>今天想研究什么？</h2>
                <p>我会使用 Web 搜索和自适应抓取，边行动边留下可审计证据。</p>
              </div>
            )}
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} run={run} />
            ))}
            {run && !terminalStatuses.has(run.status) && (
              <div className="bubble assistant">
                <span className="bubble-label">Astra</span>
                <p>{activeState(run)}</p>
              </div>
            )}
          </div>

          <form className="chat-composer" onSubmit={submit}>
            <div className="composer-menu-wrap" ref={attachMenuRef}>
              <button
                className="composer-icon-button"
                type="button"
                aria-label="添加内容"
                title="添加内容"
                onClick={() => {
                  setAttachOpen((open) => !open);
                  setModelOpen(false);
                }}
              >+</button>
              {attachOpen && (
                <div className="floating-menu attachment-menu">
                  <button type="button"><span>↥</span><div><strong>上传文件</strong><small>文档、代码与数据</small></div></button>
                  <button type="button"><span>▧</span><div><strong>添加图片</strong><small>分析图像内容</small></div></button>
                  <button type="button"><span>⌁</span><div><strong>连接来源</strong><small>即将支持</small></div></button>
                </div>
              )}
            </div>
            <textarea
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="输入任务 / 继续追问..."
            />
            <div className="model-menu-wrap" ref={modelMenuRef}>
              <button className="model-selector" type="button" onClick={() => {
                setModelOpen((open) => !open);
                setAttachOpen(false);
              }}>
                <span>{selectedModel}</span><small>{reflectionEnabled ? '反思开启' : '快速模式'}</small><b>⌄</b>
              </button>
              {modelOpen && (
                <ModelMenu
                  selectedModel={selectedModel}
                  onModelChange={(model) => { setSelectedModel(model); setModelOpen(false); }}
                  reflectionEnabled={reflectionEnabled}
                  onReflectionChange={setReflectionEnabled}
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
        新对话
      </button>

      <nav className="side-section">
        <span className="side-title">历史对话</span>
        <button className={`history-item ${run ? 'active' : ''}`} type="button">
          <span>{run ? run.summary || '当前 Web Agent 会话' : '暂无会话'}</span>
          {run && <small>{statusLabel(run.status)}</small>}
        </button>
      </nav>

      <div className="sidebar-bottom">
        <button className="side-action" type="button" onClick={onOpenUsage}>
          <span>用量统计</span>
          <small>{run?.tool_calls.length ?? 0} calls</small>
        </button>
        <button className={`side-action ${activeView === 'settings' ? 'active' : ''}`} type="button" onClick={onOpenSettings}>
          <span>设置</span>
          <small>本地配置</small>
        </button>
      </div>
    </aside>
  );
}

function CapabilityItem({ title, detail, state, enabled = true }: { title: string; detail: string; state: string; enabled?: boolean }) {
  return (
    <div className="capability-item">
      <div>
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
      <span className={`capability-state ${enabled ? 'enabled' : ''}`}>{state}</span>
    </div>
  );
}

const settingCategories = ['模型与推理', '能力', '运行时', '记忆', '验证与安全', '界面', '数据与隐私'];

function SettingsView({ activeCategory, onCategoryChange, selectedModel, onModelChange, reflectionEnabled, onReflectionChange, onClose }: {
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  selectedModel: string;
  onModelChange: (model: string) => void;
  reflectionEnabled: boolean;
  onReflectionChange: (enabled: boolean) => void;
  onClose: () => void;
}) {
  return (
    <section className="settings-page">
      <header className="settings-header">
        <div><span>工作区</span><h1>设置</h1></div>
        <button className="close-button" type="button" aria-label="关闭设置" onClick={onClose}>×</button>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置类别">
          {settingCategories.map((category) => (
            <button className={category === activeCategory ? 'active' : ''} type="button" key={category} onClick={() => onCategoryChange(category)}>{category}</button>
          ))}
        </nav>
        <div className="settings-content">
          <SettingSection category={activeCategory} selectedModel={selectedModel} onModelChange={onModelChange} reflectionEnabled={reflectionEnabled} onReflectionChange={onReflectionChange} />
        </div>
      </div>
    </section>
  );
}

function SettingSection({ category, selectedModel, onModelChange, reflectionEnabled, onReflectionChange }: {
  category: string;
  selectedModel: string;
  onModelChange: (model: string) => void;
  reflectionEnabled: boolean;
  onReflectionChange: (enabled: boolean) => void;
}) {
  if (category === '能力') return <SettingsGroup title="能力" description="选择 Agent 可以使用的工具。具体权限和执行限制由运行时统一管理。"><div className="capability-settings"><CapabilityItem title="Web Search" detail="搜索公开网页并生成候选来源" state="已启用" /><CapabilityItem title="Web Fetch" detail="自适应提取页面主要内容" state="已启用" /><CapabilityItem title="文件分析" detail="解析上传的文档、代码与数据" state="即将支持" enabled={false} /><CapabilityItem title="图像理解" detail="识别并分析图片内容" state="即将支持" enabled={false} /></div></SettingsGroup>;
  if (category === '运行时') return <SettingsGroup title="运行时" description="统一管理任务执行的权限边界、资源上限和故障恢复。"><div className="runtime-summary"><div><span>执行环境</span><strong>本地沙盒</strong></div><div><span>资源预算</span><strong>标准</strong></div><div><span>网络</span><strong>按需授权</strong></div></div><SettingRow title="沙盒模式" description="限定 Agent 可读取和修改的文件系统范围"><select defaultValue="workspace"><option value="readonly">只读</option><option value="workspace">工作区可写</option><option value="container">隔离容器</option></select></SettingRow><SettingRow title="网络策略" description="限制外部网络访问范围，并决定是否需要授权"><select defaultValue="approval"><option value="off">禁用</option><option value="approval">公开网络，按需授权</option><option value="allowlist">仅允许列表</option></select></SettingRow><SettingRow title="执行确认" description="工具或命令可能修改环境、产生费用或影响外部系统时请求确认"><select defaultValue="risk"><option value="risk">仅高风险操作</option><option value="always">每次执行</option><option value="never">从不确认</option></select></SettingRow><SettingRow title="最大 Agent 轮次" description="达到上限后停止循环并输出当前结果"><select defaultValue="12"><option>6</option><option>12</option><option>20</option></select></SettingRow><SettingRow title="工具调用上限" description="限制单次任务可执行的工具调用总数"><select defaultValue="10"><option>5</option><option>10</option><option>20</option></select></SettingRow><SettingRow title="单次运行时限" description="超时后停止任务并保留可恢复的运行状态"><select defaultValue="30"><option value="10">10 分钟</option><option value="30">30 分钟</option><option value="60">60 分钟</option></select></SettingRow><SettingRow title="并行工具调用" description="并发执行相互独立且无副作用冲突的工具"><Toggle checked /></SettingRow><SettingRow title="失败自动重试" description="仅重试临时网络错误和明确标记为可恢复的工具错误"><select defaultValue="2"><option value="0">不重试</option><option value="1">1 次</option><option value="2">2 次</option><option value="3">3 次</option></select></SettingRow><SettingRow title="后台继续运行" description="离开当前对话后继续执行，并保留状态通知"><Toggle checked /></SettingRow><SettingRow title="保留运行工件" description="保存工具输出、验证报告和失败现场用于审计"><Toggle checked /></SettingRow></SettingsGroup>;
  if (category === '记忆') return <SettingsGroup title="记忆" description="管理 Agent 在单次任务和不同对话之间保留的信息。"><SettingRow title="运行记忆" description="在当前任务中保留来源摘要和决策线索"><Toggle checked /></SettingRow><SettingRow title="跨对话记忆" description="在新对话中使用已确认的偏好与事实"><Toggle /></SettingRow><SettingRow title="写入阈值" description="仅保存高于该置信度的结构化记忆"><select defaultValue="80"><option value="70">70%</option><option value="80">80%</option><option value="90">90%</option></select></SettingRow><SettingRow title="记忆保留期" description="到期后自动清理非固定记忆"><select defaultValue="30"><option value="7">7 天</option><option value="30">30 天</option><option value="forever">永久</option></select></SettingRow></SettingsGroup>;
  if (category === '验证与安全') return <SettingsGroup title="验证与安全" description="定义最终答案必须满足的证据和质量标准。"><SettingRow title="回答前验证" description="生成最终答案前检查证据覆盖、来源冲突和失败项"><Toggle checked /></SettingRow><SettingRow title="最低独立来源数" description="总结类任务至少需要多少个相互独立的来源"><select defaultValue="2"><option>1</option><option>2</option><option>3</option></select></SettingRow><SettingRow title="来源质量阈值" description="低于阈值的来源会被标记，并降低其证据权重"><select defaultValue="70"><option value="50">50%</option><option value="70">70%</option><option value="85">85%</option></select></SettingRow><SettingRow title="冲突处理" description="来源结论不一致时保留分歧，不强行合并为单一答案"><select defaultValue="disclose"><option value="disclose">披露冲突</option><option value="retry">继续查证</option><option value="block">阻止回答</option></select></SettingRow></SettingsGroup>;
  if (category === '界面') return <SettingsGroup title="界面" description="调整工作区的信息密度和运行过程展示。"><SettingRow title="过程展示" description="在对话中显示工具调用和反思摘要"><Toggle checked /></SettingRow><SettingRow title="审计面板" description="任务完成后显示证据、事件和记忆"><Toggle checked /></SettingRow><SettingRow title="信息密度" description="控制对话和面板的间距"><select defaultValue="compact"><option value="compact">紧凑</option><option value="comfortable">舒适</option></select></SettingRow></SettingsGroup>;
  if (category === '数据与隐私') return <SettingsGroup title="数据与隐私" description="控制运行数据、抓取内容和诊断信息的保存方式。"><SettingRow title="保存运行记录" description="保留对话、工具调用和验证报告"><Toggle checked /></SettingRow><SettingRow title="保存抓取正文" description="将网页正文写入本地工件存储"><Toggle /></SettingRow><SettingRow title="诊断日志" description="记录不包含正文的性能与错误信息"><Toggle checked /></SettingRow><button className="danger-button" type="button">清除本地运行数据</button></SettingsGroup>;
  return <SettingsGroup title="模型与推理" description="决定 Agent 使用什么模型，以及如何规划、推理和反思。"><SettingRow title="默认模型" description="新对话默认使用的基础模型"><select value={selectedModel} onChange={(event) => onModelChange(event.target.value)}><option>Astra Pro</option><option>Astra Flash</option><option>GPT-5</option></select></SettingRow><SettingRow title="推理强度" description="控制模型在速度、Token 消耗和复杂任务质量之间的取舍"><select defaultValue="balanced"><option value="fast">快速</option><option value="balanced">均衡</option><option value="deep">深入</option></select></SettingRow><SettingRow title="规划策略" description="决定 Agent 在行动前生成完整计划，还是根据观察动态推进"><select defaultValue="adaptive"><option value="adaptive">自适应</option><option value="plan-first">先规划后执行</option><option value="direct">直接行动</option></select></SettingRow><SettingRow title="反思循环" description="分析工具结果和失败原因，必要时修订下一步策略"><Toggle checked={reflectionEnabled} onChange={onReflectionChange} /></SettingRow><SettingRow title="反思触发方式" description="选择每轮检查，或仅在失败和证据不足时反思"><select defaultValue="adaptive"><option value="adaptive">按需触发</option><option value="always">每轮触发</option><option value="failure">仅失败时</option></select></SettingRow></SettingsGroup>;
}

function SettingsGroup({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="settings-group"><header><h2>{title}</h2><p>{description}</p></header>{children}</section>;
}

function SettingRow({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <div className="setting-row"><div><strong>{title}</strong><span>{description}</span></div>{children}</div>;
}

function Toggle({ checked = false, onChange }: { checked?: boolean; onChange?: (checked: boolean) => void }) {
  const [localChecked, setLocalChecked] = useState(checked);
  const value = onChange ? checked : localChecked;
  return <button className={`toggle ${value ? 'on' : ''}`} type="button" role="switch" aria-checked={value} onClick={() => onChange ? onChange(!value) : setLocalChecked(!value)}><span /></button>;
}

function ModelMenu({ selectedModel, onModelChange, reflectionEnabled, onReflectionChange }: { selectedModel: string; onModelChange: (model: string) => void; reflectionEnabled: boolean; onReflectionChange: (enabled: boolean) => void }) {
  return <div className="floating-menu model-menu"><div className="menu-heading">选择模型</div>{[['Astra Pro', '复杂研究与多步任务'], ['Astra Flash', '快速问答与轻量搜索'], ['GPT-5', '通用推理模型']].map(([model, detail]) => <button className={selectedModel === model ? 'selected' : ''} type="button" key={model} onClick={() => onModelChange(model)}><div><strong>{model}</strong><small>{detail}</small></div><span>{selectedModel === model ? '✓' : ''}</span></button>)}<div className="menu-divider" /><div className="menu-toggle"><div><strong>反思模式</strong><small>执行后自检并调整策略</small></div><Toggle checked={reflectionEnabled} onChange={onReflectionChange} /></div></div>;
}

function UsageModal({ run, onClose }: { run: RunView | null; onClose: () => void }) {
  const calls = run?.tool_calls.length ?? 0;
  const turns = run?.turns?.length ?? 0;
  const estimatedTokens = run ? Math.max(640, turns * 760 + calls * 420 + (run.result?.findings.length ?? 0) * 180) : 0;
  const succeeded = run?.tool_calls.filter((call) => call.status === 'succeeded').length ?? 0;
  const successRate = calls ? Math.round((succeeded / calls) * 100) : 0;
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="usage-modal" role="dialog" aria-modal="true" aria-label="用量统计" onMouseDown={(event) => event.stopPropagation()}><header><div><span>当前对话</span><h2>用量统计</h2></div><button className="close-button" type="button" aria-label="关闭用量统计" onClick={onClose}>×</button></header><div className="usage-primary"><div><span>模型调用</span><strong>{turns}</strong><small>次决策 / 生成</small></div><div><span>Token 用量</span><strong>{estimatedTokens.toLocaleString()}</strong><small>前端估算</small></div></div><div className="usage-grid"><div><span>工具调用</span><strong>{calls}</strong></div><div><span>成功率</span><strong>{successRate}%</strong></div><div><span>证据来源</span><strong>{run?.result?.sources.length ?? 0}</strong></div><div><span>Agent 轮次</span><strong>{turns}</strong></div><div><span>Memory 写入</span><strong>{run?.memories?.length ?? 0}</strong></div><div><span>验证警告</span><strong>{run?.verification_report?.caveat_count ?? 0}</strong></div></div><p className="usage-note">精确输入、输出和缓存 Token 将在模型网关接入后由后端返回。</p></section></div>;
}

function MessageBubble({ message, run }: { message: ChatMessage; run: RunView | null }) {
  const role = message.role === 'user' ? 'user' : message.role === 'tool' ? 'tool' : 'assistant';
  const turnIndex = Number(message.metadata.turn_index ?? 0);
  const turn = run?.turns?.find((item) => item.turn_index === turnIndex);

  return (
    <article className={`bubble ${role}`}>
      <span className="bubble-label">{labelForRole(message.role)}</span>
      <p>{message.content}</p>
      {turn?.selected_tool && <ToolEvent turn={turn} toolCalls={run?.tool_calls ?? []} />}
      {turn?.reflection && (
        <div className="reflection-card">
          <strong>反思</strong>
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
  return (
    <details className="audit-drawer">
      <summary>审计详情</summary>
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
          )) : <p className="empty">暂无 Memory 写入。</p>}
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
