import { ReactNode, useEffect, useState } from 'react';
import { CloseButton } from './CloseButton';
import { useI18n } from './i18n';

type DocumentationTopic = 'memory' | 'answer-modes' | 'token-performance' | 'runtime-settings' | 'about';

const topics: Array<{ id: DocumentationTopic; label: string; description: string }> = [
  { id: 'memory', label: '记忆', description: '生产、召回、范围与整理' },
  { id: 'answer-modes', label: '快速模式与可信模式', description: '定义、差异与选择建议' },
  { id: 'token-performance', label: 'Token 消耗与性能', description: '配对基准、指标与结果解读' },
  { id: 'runtime-settings', label: '模型与运行设置', description: '思考、计划、反思、批准与上下文' },
  { id: 'about', label: '关于 Astra', description: '创建动机、使命与版权信息' },
];

const memorySections = [
  ['memory-background', '为什么需要记忆'],
  ['memory-boundaries', '四个容易混淆的概念'],
  ['memory-lifecycle', '记忆如何产生并生效'],
  ['memory-scope', '作用范围'],
  ['memory-recall', '如何检索与召回'],
  ['memory-autodream', 'AutoDream 如何整理'],
  ['memory-faq', '常见问题'],
] as const;

const answerModeSections = [
  ['answer-mode-definitions', '两种模式的定义'],
  ['answer-mode-shared', '共享的运行时基础'],
  ['answer-mode-lifecycle', '执行流程'],
  ['answer-mode-comparison', '完整差异对比'],
  ['answer-mode-subagents', 'Subagent 的行为差异'],
  ['answer-mode-choose', '如何选择'],
  ['answer-mode-faq', '常见问题'],
] as const;

const runtimeSettingsSections = [
  ['runtime-settings-overview', '这些设置分别控制什么'],
  ['runtime-settings-model-thinking', '模型思考'],
  ['runtime-settings-plan-execution', '计划执行'],
  ['runtime-settings-reasoning', '推理资源与工具调用上限'],
  ['runtime-settings-reflection', '反思循环与触发方式'],
  ['runtime-settings-approvals', '请求批准与自动批准'],
  ['runtime-settings-context', '上下文容量如何计算'],
  ['runtime-settings-effective-scope', '设置何时生效'],
] as const;

const tokenPerformanceSections = [
  ['token-performance-source', 'Token 消耗来自哪里'],
  ['token-performance-modes', '两种模式为何不同'],
  ['token-performance-benchmark', '配对基准如何设计'],
  ['token-performance-run', '如何运行基准'],
  ['token-performance-results', '如何阅读结果'],
  ['token-performance-boundaries', '结果适用边界'],
] as const;

const aboutSections = [
  ['about-origin', '为什么创建 Astra'],
  ['about-mission', '我们的使命'],
  ['about-principles', '核心原则'],
  ['about-boundary', 'Astra 是什么'],
  ['about-copyright', '版权与许可证'],
  ['about-accuracy', '信息与版本'],
] as const;

const topicBySection = new Map<string, DocumentationTopic>([
  ...memorySections.map(([id]) => [id, 'memory'] as const),
  ...answerModeSections.map(([id]) => [id, 'answer-modes'] as const),
  ...tokenPerformanceSections.map(([id]) => [id, 'token-performance'] as const),
  ...runtimeSettingsSections.map(([id]) => [id, 'runtime-settings'] as const),
  ...aboutSections.map(([id]) => [id, 'about'] as const),
]);

function topicFromHash(): DocumentationTopic | null {
  if (typeof window === 'undefined') return null;
  return topicBySection.get(window.location.hash.replace(/^#/, '')) ?? null;
}

export function DocumentationCenter({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [topic, setTopic] = useState<DocumentationTopic>(() => topicFromHash() ?? 'memory');
  const sections = topic === 'memory'
    ? memorySections
    : topic === 'answer-modes'
      ? answerModeSections
      : topic === 'token-performance'
        ? tokenPerformanceSections
        : topic === 'runtime-settings'
          ? runtimeSettingsSections
          : aboutSections;

  useEffect(() => {
    const syncTopicToHash = () => {
      const linkedTopic = topicFromHash();
      if (linkedTopic) setTopic(linkedTopic);
    };
    syncTopicToHash();
    window.addEventListener('hashchange', syncTopicToHash);
    return () => window.removeEventListener('hashchange', syncTopicToHash);
  }, []);

  useEffect(() => {
    const anchor = window.location.hash.replace(/^#/, '');
    if (!anchor || topicBySection.get(anchor) !== topic) return;
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById(anchor);
      if (typeof target?.scrollIntoView === 'function') target.scrollIntoView({ block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [topic]);

  return <section className="documentation-center" aria-labelledby="documentation-center-title">
    <header className="documentation-header">
      <div>
        <span>{t('帮助与指南')}</span>
        <h1 id="documentation-center-title">{t('Astra 文档中心')}</h1>
        <p>{t('了解 Astra 的核心概念、边界与实际生效方式。')}</p>
      </div>
      <CloseButton label={t('关闭帮助文档')} onClick={onClose} />
    </header>

    <div className="documentation-layout">
      <aside className="documentation-sidebar">
        <nav aria-label={t('文档主题')}>
          <span className="documentation-nav-label">{t('核心概念')}</span>
          {topics.map((item) => <button
            className={topic === item.id ? 'active' : ''}
            type="button"
            aria-current={topic === item.id ? 'page' : undefined}
            key={item.id}
            onClick={() => setTopic(item.id)}
          >
            <span className="documentation-topic-mark" aria-hidden="true">✦</span>
            <span><strong>{t(item.label)}</strong><small>{t(item.description)}</small></span>
          </button>)}
        </nav>
        <div className="documentation-sidebar-note">
          <strong>{t('文档随 Astra 一起更新')}</strong>
          <p>{t('这里描述的是当前版本的产品行为，不是通用模型能力。')}</p>
        </div>
      </aside>

      <div className="documentation-content">
        <div className="documentation-content-frame">
          <DocumentationToc sections={sections} />
          {topic === 'memory' && <MemoryArticle />}
          {topic === 'answer-modes' && <AnswerModesArticle />}
          {topic === 'token-performance' && <TokenPerformanceArticle />}
          {topic === 'runtime-settings' && <RuntimeSettingsArticle />}
          {topic === 'about' && <AboutArticle />}
        </div>
      </div>
    </div>
  </section>;
}

function DocumentationToc({ sections }: { sections: ReadonlyArray<readonly [string, string]> }) {
  const { t } = useI18n();
  return <nav className="documentation-page-toc" aria-label={t('本页目录')}>
    <span>{t('本页目录')}</span>
    <div>{sections.map(([id, label]) => <a href={`#${id}`} key={id}>{t(label)}</a>)}</div>
  </nav>;
}

function MemoryArticle() {
  const { t } = useI18n();
  return <article className="documentation-article" aria-labelledby="memory-document-title">
    <div className="documentation-hero">
      <span className="documentation-kicker">{t('核心概念')}</span>
      <h2 id="memory-document-title">{t('记忆')}</h2>
      <p>{t('记忆让 Astra 在上下文窗口之外保留值得复用的信息，但“已保存”不等于“每次回答都会使用”。一条记忆只有通过范围匹配、检索筛选和上下文注入后，才会影响某次回答。')}</p>
      <div className="documentation-summary-grid">
        <div><span>01</span><strong>{t('有来源地保存')}</strong><small>{t('从任务结果中提取可复用事实、偏好与经验')}</small></div>
        <div><span>02</span><strong>{t('按范围召回')}</strong><small>{t('只在用户、Session、Task 或 Run 边界匹配时参与')}</small></div>
        <div><span>03</span><strong>{t('受预算约束')}</strong><small>{t('相关度、置信度和上下文预算共同决定是否注入')}</small></div>
      </div>
    </div>

    <DocumentSection id="memory-background" eyebrow="Background" title="为什么需要记忆">
      <p>{t('模型的单次上下文是有限的。新任务开始后，之前对话中的偏好、项目约定和已验证事实不会天然出现；反复让用户重新说明既低效，也容易产生不一致。')}</p>
      <div className="documentation-problem-grid">
        <div><strong>{t('减少重复说明')}</strong><p>{t('保留稳定偏好、称呼、格式约定和长期目标。')}</p></div>
        <div><strong>{t('延续任务经验')}</strong><p>{t('让后续任务复用已验证的项目事实、决策与失败教训。')}</p></div>
        <div><strong>{t('控制而非无限记住')}</strong><p>{t('通过来源、范围、置信度、有效期和审计记录约束记忆。')}</p></div>
      </div>
      <aside className="documentation-callout"><strong>{t('核心原则')}</strong><p>{t('记忆是可治理的辅助上下文，不是系统指令，也不能覆盖权限、安全策略或用户当前明确要求。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="memory-boundaries" eyebrow="Boundaries" title="四个容易混淆的概念">
      <div className="documentation-boundary-list">
        <Boundary term="MEMORY.md" title="记忆治理规则" description="告诉 Agent 应该提取什么、避免保存什么以及如何组织候选；它本身不是已保存记忆，也不会单独开启写入或召回。" />
        <Boundary term={t('运行时设置')} title="能力开关与预算" description="控制是否保存新记忆、是否启用持久记忆召回，以及条数、Token 和分数阈值。设置只影响之后创建的任务。" />
        <Boundary term={t('已保存的记忆')} title="结构化记录" description="包含内容、来源、范围、置信度、状态和版本关系。只有 active 且满足约束的记录才有资格参与召回。" />
        <Boundary term={t('活动与 AutoDream')} title="证据与整理" description="活动记录解释记忆何时被创建、召回或替代；AutoDream 在后台合并重复记忆。两者都不是另一套独立记忆。" />
      </div>
    </DocumentSection>

    <DocumentSection id="memory-lifecycle" eyebrow="Lifecycle" title="记忆如何产生并生效">
      <ol className="documentation-timeline">
        <TimelineStep number="1" title="产生候选" description="任务形成可复用结果后，记忆提取器依据 MEMORY.md 从有来源的内容中生成结构化候选。关闭“保存新记忆”时不会进入写入流程。" />
        <TimelineStep number="2" title="校验并等待确认" description="Astra 校验来源、命名空间、内容和置信度，并用稳定键去重或建立候选版本；通过校验后仍保持 candidate。" />
        <TimelineStep number="3" title="人工确认激活" description="本机操作员在待确认列表检查内容、范围、置信度和来源，填写原因后手动激活；拒绝的候选不会参与召回。" />
        <TimelineStep number="4" title="新请求触发检索" description="后续请求到来时，Astra 按当前目标、用户、Session、Task 和 Run 范围寻找符合条件的 active 记忆。" />
        <TimelineStep number="5" title="筛选后注入" description="active 记忆还要通过相关度、置信度、有效期、来源访问和上下文预算筛选；仅在持久记忆召回开启时才会加入模型上下文。" />
      </ol>
      <aside className="documentation-callout emphasis"><strong>{t('什么时候真正生效？')}</strong><p>{t('保存成功只产生待确认候选。人工激活后也只是未来“可能被使用”；只有某次请求检索命中，并且召回模式为开启、所有门槛和预算都通过时，它才会进入该次回答的上下文。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="memory-scope" eyebrow="Scope" title="作用范围">
      <p>{t('范围决定一条记忆可以在哪些请求中成为候选。范围越小，隔离越强；范围不会因为内容相似而自动扩大。')}</p>
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('范围')}</th><th>{t('匹配边界')}</th><th>{t('适合保存')}</th></tr></thead>
        <tbody>
          <tr><td><code>run</code></td><td>{t('仅当前一次运行')}</td><td>{t('本次执行的临时线索和中间决策')}</td></tr>
          <tr><td><code>task</code></td><td>{t('同一任务／对话的后续运行')}</td><td>{t('当前任务目标、局部约束和追问上下文')}</td></tr>
          <tr><td><code>session</code></td><td>{t('同一浏览器会话中的不同任务')}</td><td>{t('本次使用期间需要跨对话延续的事实和偏好')}</td></tr>
          <tr><td><code>user</code></td><td>{t('同一用户创建的任务')}</td><td>{t('稳定个人偏好，例如语言、格式和沟通习惯')}</td></tr>
        </tbody>
      </table></div>
      <aside className="documentation-callout neutral"><strong>{t('Task Workspace 不是记忆作用域')}</strong><p>{t('当前 Task Workspace 与 Task 一对一，只保存该任务的文件、变更和检查点。它不会让多个 Task 共享记忆；跨 Task 的临时共享由 session 作用域承担。')}</p></aside>
      <p className="documentation-footnote">{t('持久记忆召回关闭时，task、session 和 user 范围的记忆不会注入当前请求；当前 run 内的上下文仍正常工作。')}</p>
    </DocumentSection>

    <DocumentSection id="memory-recall" eyebrow="Recall" title="如何检索与召回">
      <div className="documentation-mode-grid">
        <div><span>{t('关闭')}</span><strong>off</strong><p>{t('不执行持久记忆召回，也不会向回答注入 Task、Session 或用户记忆。')}</p></div>
        <div className="active"><span>{t('开启')}</span><strong>on</strong><p>{t('检索、筛选并把最终选中的记忆作为低权限辅助数据注入上下文。')}</p></div>
      </div>
      <h3>{t('一次召回会依次经过')}</h3>
      <ol className="documentation-checklist">
        <li>{t('身份与命名空间隔离：先确认用户、Session、Task 和 Run 范围。')}</li>
        <li>{t('生命周期过滤：只考虑 active、未过期且来源仍可访问的记录。')}</li>
        <li>{t('相关度排序：根据当前目标的词项匹配，并结合置信度、重要性、新近度和历史效用评分。')}</li>
        <li>{t('阈值与预算：应用最低置信度、最低相关度、最多条数以及 Token／字符预算。')}</li>
        <li>{t('安全注入：记忆以不受信任的辅助数据进入上下文，不能获得系统指令权限。')}</li>
      </ol>
      <aside className="documentation-callout neutral"><strong>{t('当前检索边界')}</strong><p>{t('当前版本使用词项相关度而不是向量语义检索。表达完全不同但含义相近的内容可能无法命中，召回审计会保留筛选与排除原因。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="memory-autodream" eyebrow="AutoDream" title="AutoDream 如何整理">
      <p>{t('AutoDream 是记忆库的后台维护流程。它只在同一命名空间内寻找重复、可合并或冲突的 active 记忆，生成可审计的整理提案，并在校验通过后发布替代版本。')}</p>
      <div className="documentation-autodream-flow" aria-label={t('AutoDream 整理流程')}>
        <span>{t('原始记忆')}</span><i aria-hidden="true">→</i><span>{t('整理与校验')}</span><i aria-hidden="true">→</i><span>{t('新 active 版本')}</span>
      </div>
      <aside className="documentation-callout"><strong>{t('来源记忆不会被硬删除')}</strong><p>{t('成功发布后，参与合并的原记忆会标记为 superseded，由新版本接替召回；来源和版本关系仍保留用于审计。若整理结果回滚，新版本会被撤销，原记忆可以恢复。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="memory-faq" eyebrow="FAQ" title="常见问题">
      <div className="documentation-faq">
        <details open><summary>{t('记忆审计只对对应的 run 生效吗？')}</summary><p>{t('审计事件记录的是某次 run 中发生的生产、检索或注入决策，所以事件归属于该 run；被记录或召回的记忆本身仍按自己的 task、session 或 user 范围存在。')}</p></details>
        <details><summary>{t('生产晋升关闭是什么意思？')}</summary><p>{t('表示后台整理产出的候选不会自动晋升为正式 active 记忆。候选和评估仍可被记录，但不会在未经批准的情况下改变生产召回结果。')}</p></details>
        <details><summary>{t('修改设置会立刻改变正在运行的任务吗？')}</summary><p>{t('不会。记忆运行时设置在创建任务时固化，修改会应用于之后新建的任务；已有任务继续使用创建时的配置。')}</p></details>
      </div>
    </DocumentSection>
  </article>;
}

function AnswerModesArticle() {
  const { t } = useI18n();
  return <article className="documentation-article" aria-labelledby="answer-modes-document-title">
    <div className="documentation-hero">
      <span className="documentation-kicker">Answer modes</span>
      <h2 id="answer-modes-document-title">{t('快速模式与可信模式')}</h2>
      <p>{t('Astra 只提供快速模式和可信模式两种产品模式。快速模式由独立 fast-v1 运行时驱动，可信模式由 trusted-v1 执行计划和验证；两者只共享模型传输、工具与平台安全边界。')}</p>
      <div className="documentation-summary-grid">
        <div><span>01</span><strong>{t('快速模式')}</strong><small>{t('模型直接选择回答、工具、提问或停止，不执行可信校验')}</small></div>
        <div><span>02</span><strong>{t('可信模式')}</strong><small>{t('先建立任务契约和 Plan DAG，再执行、验证和收敛')}</small></div>
        <div><span>03</span><strong>{t('共享安全边界')}</strong><small>{t('权限、审批、工具路由、Sandbox、Artifact 和取消保持一致')}</small></div>
      </div>
    </div>

    <DocumentSection id="answer-mode-definitions" eyebrow="Definitions" title="两种模式的定义">
      <div className="documentation-mode-grid">
        <div className="active"><span>standard · fast-v1</span><strong>{t('快速模式')}</strong><p>{t('面向日常问答、检索、总结和低风险工具任务。独立 Fast Agent Loop 相信模型选择下一动作，不创建 TaskContract、Plan、Reflection、VerificationReport 或 CompletionDecision。')}</p></div>
        <div><span>trusted</span><strong>{t('可信模式')}</strong><p>{t('面向多阶段、高风险、需要审计或明确交付标准的任务。Run 先创建完整 TaskContract 和版本化 Plan DAG，再按依赖执行节点、验证结果并通过 Completion Gate。')}</p></div>
      </div>
      <aside className="documentation-callout"><strong>{t('可信不等于绝对正确')}</strong><p>{t('可信模式提高计划透明度、验证覆盖和失败可见性，但不能保证模型结论绝对正确。重要决策仍应检查来源、产物和验证状态。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="answer-mode-shared" eyebrow="Shared runtime" title="共享的运行时基础">
      <p>{t('两种模式拥有不同的 Agent runtime、状态快照、事件和终结逻辑。它们共享模型传输、ToolRouter、权限与审批、Workspace、Artifact、Sandbox、取消以及历史会话。')}</p>
      <ol className="documentation-checklist">
        <li>{t('工具调用都必须经过同一输入校验、权限门、效果分析和执行后端。')}</li>
        <li>{t('文件和产物都使用相同的 Workspace、Artifact 与安全交付边界。')}</li>
        <li>{t('快速模式首版不开放 Subagent 与记忆写入；显式 Subagent 工作流会创建可信运行。')}</li>
        <li>{t('切换回答模式不会自动更换模型，也不会绕过执行审批或部署安全上限。')}</li>
      </ol>
    </DocumentSection>

    <DocumentSection id="answer-mode-lifecycle" eyebrow="Lifecycle" title="执行流程">
      <h3>{t('快速模式')}</h3>
      <ol className="documentation-timeline">
        <TimelineStep number="1" title="直接理解请求" description="根 Agent 使用当前对话目标和可用能力进入快速决策，不等待规范计划生成。" />
        <TimelineStep number="2" title="模型选择行动" description="模型直接选择 answer、call_tool、ask_user 或 stop；工具结果作为轻量观察返回下一轮。" />
        <TimelineStep number="3" title="直接终结" description="运行清洗 Artifact 引用并持久化快速回答，不创建 VerificationReport 或 CompletionDecision。" />
      </ol>
      <h3>{t('可信模式')}</h3>
      <ol className="documentation-timeline">
        <TimelineStep number="1" title="建立任务契约" description="提取交付物、约束、风险和可验证的成功标准。" />
        <TimelineStep number="2" title="生成规范计划" description="创建完整且经过校验的版本化 Plan DAG；根据设置等待确认或自动执行。" />
        <TimelineStep number="3" title="按依赖执行与验证" description="节点经过调度、工具选择、观察评估、失败恢复或重新规划。" />
        <TimelineStep number="4" title="通过完整完成门槛" description="检查 Plan、成功标准、验证、审批、预算、Subagent 和 Join 后才允许成功完成。" />
      </ol>
    </DocumentSection>

    <DocumentSection id="answer-mode-comparison" eyebrow="Comparison" title="完整差异对比">
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('维度')}</th><th>{t('快速模式')}</th><th>{t('可信模式')}</th></tr></thead>
        <tbody>
          <tr><td>{t('启动方式')}</td><td>{t('直接进入快速 Agent Loop')}</td><td>{t('先建立 TaskContract 和 Plan')}</td></tr>
          <tr><td>{t('规范计划')}</td><td>{t('不创建 Plan DAG')}</td><td>{t('创建、持久化并版本化 Plan DAG')}</td></tr>
          <tr><td>{t('计划控制')}</td><td>{t('无需确认')}</td><td>{t('支持确认后执行或自动执行')}</td></tr>
          <tr><td>{t('推理策略')}</td><td>{t('独立最小部署策略，不读取可信推理强度')}</td><td>{t('可配置推理强度、工具预算和反思')}</td></tr>
          <tr><td>{t('验证等级')}</td><td>{t('不执行可信验证')}</td><td>{t('严格验证和成功标准覆盖')}</td></tr>
          <tr><td>{t('完成条件')}</td><td>{t('模型回答后直接清洗并持久化')}</td><td>{t('Plan、验证、审批、预算和完整 Completion Gate')}</td></tr>
          <tr><td>{t('失败处理')}</td><td>{t('在快速循环内重试、替代或阻塞')}</td><td>{t('节点失败、反思、重规划和版本 lineage')}</td></tr>
          <tr><td>{t('过程界面')}</td><td>{t('模型、工具、审批与错误的轻量时间线')}</td><td>{t('可信执行图谱、节点检查和 Agent 树')}</td></tr>
          <tr><td>{t('典型成本')}</td><td>{t('延迟和用量通常较低')}</td><td>{t('规划与验证会增加延迟和用量')}</td></tr>
          <tr><td>{t('适用任务')}</td><td>{t('日常问答、检索、总结和低风险操作')}</td><td>{t('复杂交付、高风险操作、长流程和严格审计')}</td></tr>
        </tbody>
      </table></div>
    </DocumentSection>

    <DocumentSection id="answer-mode-subagents" eyebrow="Subagents" title="Subagent 的行为差异">
      <p>{t('fast-v1 首版不装配 Subagent。需要显式并发委派时，/subagent 会创建 trusted-v1 Run，再由受治理 Supervisor 负责 child、Join、取消和恢复。')}</p>
      <div className="documentation-problem-grid">
        <div><strong>{t('快速模式')}</strong><p>{t('工具目录过滤 swarm 和其他可信专属能力；模型不能通过输出重新授予这些能力。')}</p></div>
        <div><strong>{t('可信 Subagent')}</strong><p>{t('根 Agent 在 TaskContract 和 Plan DAG 约束下调用 swarm，委派目标可以关联当前计划节点、成功标准和严格完成门槛。')}</p></div>
        <div><strong>{t('共同边界')}</strong><p>{t('child 都使用独立 ContextManifest，不共享完整聊天、隐藏推理或可变主 Agent 状态；结果通过结构化 SubagentResult 和 Join 返回。')}</p></div>
      </div>
      <aside className="documentation-callout neutral"><strong>{t('运行时不会中途切换')}</strong><p>{t('每个 Run 在创建时冻结 runtime kind 和版本；修改偏好只影响新 Run，等待审批或重启恢复仍使用原运行时。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="answer-mode-choose" eyebrow="Choosing a mode" title="如何选择">
      <div className="documentation-boundary-list">
        <Boundary term={t('选择快速模式')} title="结果容易检查" description="问题范围清晰、失败影响低，希望更快获得答案，并且你能够直接判断结果是否可用。" />
        <Boundary term={t('选择可信模式')} title="并发委派" description="需要 Subagent、并行比较或独立子任务时，使用可信模式或显式 /subagent。" />
        <Boundary term={t('选择可信模式')} title="交付物和步骤复杂" description="任务包含多个依赖步骤、文件产物、明确成功标准，或失败后需要重规划。" />
        <Boundary term={t('选择可信模式')} title="风险或审计要求高" description="结果将用于重要决策、受控操作或需要解释执行路径、证据和验证状态。" />
      </div>
      <aside className="documentation-callout emphasis"><strong>{t('一个实用判断')}</strong><p>{t('如果你只关心“尽快得到可检查的答案”，优先快速模式；如果你还关心“系统按什么计划完成、如何证明完成以及失败在哪里”，选择可信模式。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="answer-mode-faq" eyebrow="FAQ" title="常见问题">
      <div className="documentation-faq">
        <details open><summary>{t('可信模式一定使用 Subagent 吗？')}</summary><p>{t('不一定。可信模式一定生成规范计划，但只有任务适合独立并发且策略允许时才创建 Subagent；显式 /subagent 命令除外。')}</p></details>
        <details><summary>{t('快速模式可以使用工具和文件吗？')}</summary><p>{t('可以。快速模式共享相同的工具、审批、Workspace 和 Artifact 管线，只是跳过可信计划和严格完成验证。')}</p></details>
        <details><summary>{t('切换模式会修改当前正在运行的任务吗？')}</summary><p>{t('不会。回答模式在 Run 创建时固化；开关影响之后创建的 Run，当前运行继续使用原模式。')}</p></details>
        <details><summary>{t('模型思考深度等于回答模式吗？')}</summary><p>{t('不等于。思考深度是模型级设置，回答模式决定规划、编排和验证生命周期；调整思考深度不会自动启用或关闭可信模式。')}</p></details>
      </div>
    </DocumentSection>
  </article>;
}

function TokenPerformanceArticle() {
  const { t } = useI18n();
  return <article className="documentation-article" aria-labelledby="token-performance-document-title">
    <div className="documentation-hero">
      <span className="documentation-kicker">Performance</span>
      <h2 id="token-performance-document-title">{t('Token 消耗与性能')}</h2>
      <p>{t('Astra 的可信模式会用额外的模型调用换取显式计划、严格验证和更完整的完成判断。实际增幅取决于任务，不能用一个固定百分比代表所有场景；配对基准可以在相同模型和相同任务下给出可复核结果。')}</p>
      <div className="documentation-summary-grid">
        <div><span>01</span><strong>{t('校验不等于模型调用')}</strong><small>{t('确定性权限、Schema 和完成校验本身不消耗模型 Token')}</small></div>
        <div><span>02</span><strong>{t('可信模式有固定开销')}</strong><small>{t('Task Contract 和 Plan DAG 通常会增加模型调用')}</small></div>
        <div><span>03</span><strong>{t('用配对结果比较')}</strong><small>{t('相同 Case、模型、配置和时间窗口才具有可比性')}</small></div>
      </div>
    </div>

    <DocumentSection id="token-performance-source" eyebrow="Accounting" title="Token 消耗来自哪里">
      <p>{t('权限判断、工具输入 Schema 校验、Artifact 与 Evidence 引用检查、Completion Gate 等主要由 Astra 本地确定性代码执行。它们会产生少量本地计算时延，但不会直接产生供应商模型 Token。')}</p>
      <div className="documentation-boundary-list">
        <Boundary term="0 model calls" title="确定性治理" description="权限、预算、引用、计划结构和完成状态检查在运行时执行，不需要额外模型调用。" />
        <Boundary term="model calls" title="模型驱动阶段" description="回答决策、Task Contract、Plan、反思、重规划、综合和可选记忆提取会产生 Token。" />
        <Boundary term="provider usage" title="权威统计来源" description="性能基准读取每个 Run 的供应商 usage；缺失上报时不会把未知 Token 当作零。" />
      </div>
    </DocumentSection>

    <DocumentSection id="token-performance-modes" eyebrow="Modes" title="两种模式为何不同">
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('阶段')}</th><th>{t('快速模式')}</th><th>{t('可信模式')}</th></tr></thead>
        <tbody>
          <tr><td>{t('任务启动')}</td><td>{t('直接进入 Agent Loop')}</td><td>{t('先生成 Task Contract 和 Plan DAG')}</td></tr>
          <tr><td>{t('结果检查')}</td><td>{t('基础完成检查')}</td><td>{t('成功标准、计划状态和完整 Completion Gate')}</td></tr>
          <tr><td>{t('失败恢复')}</td><td>{t('快速循环内收敛')}</td><td>{t('可按预算反思、重规划并再次验证')}</td></tr>
          <tr><td>{t('典型表现')}</td><td>{t('小任务通常更快且 Token 更少')}</td><td>{t('复杂任务可获得更强过程控制，但 Token 和时延通常更高')}</td></tr>
        </tbody>
      </table></div>
      <aside className="documentation-callout"><strong>{t('不存在通用固定倍数')}</strong><p>{t('短答案中，契约和计划的固定成本占比可能很高；复杂任务中，可信计划可能减少无效行动，也可能因为反思和重规划增加消耗。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="token-performance-benchmark" eyebrow="Paired benchmark" title="配对基准如何设计">
      <p>{t('内置基准选择三个不依赖 Web、文件或外部服务的友好 Case。每个 Case 都由快速模式和可信模式各执行一次，并在下一轮反转顺序，以降低供应商负载随时间变化造成的偏差。')}</p>
      <div className="documentation-problem-grid">
        <div><strong>short_explanation</strong><p>{t('三句话解释递归并给出极短伪代码，用于观察小任务的固定治理开销。')}</p></div>
        <div><strong>structured_comparison</strong><p>{t('用有界表格比较列表与元组，用于观察结构化交付约束的成本。')}</p></div>
        <div><strong>bounded_checklist</strong><p>{t('生成恰好五项的短清单，用于观察明确成功条件的规划和验证成本。')}</p></div>
      </div>
      <ol className="documentation-checklist">
        <li>{t('两种模式使用相同模型、Case、工具开关和运行配置。')}</li>
        <li>{t('所有计量 Run 串行执行，可信模式自动执行计划，不计入人工确认等待。')}</li>
        <li>{t('默认每个 Case 重复三次，并在计量前为两种模式各预热一次。')}</li>
      </ol>
    </DocumentSection>

    <DocumentSection id="token-performance-run" eyebrow="Run" title="如何运行基准">
      <p>{t('先启动后端并配置需要评估的真实模型。内置 mock provider 不会上报真实 Token，不能用来形成成本结论。')}</p>
      <pre className="documentation-command"><code>cd backend{`\n`}python -m benchmarks.mode_performance --runs-per-case 3 --warmup 1</code></pre>
      <p>{t('如果模型价格较高，可以先运行单 Case 冒烟测试：')}</p>
      <pre className="documentation-command"><code>python -m benchmarks.mode_performance --case short_explanation --runs-per-case 1 --warmup 0</code></pre>
      <aside className="documentation-callout neutral"><strong>{t('默认保护')}</strong><p>{t('任一模型调用缺失 Token usage 时基准会失败；完成统计后会清理创建的对话。使用 --keep-runs 可以保留记录。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="token-performance-results" eyebrow="Results" title="如何阅读结果">
      <div className="documentation-boundary-list">
        <Boundary term="trusted_token_ratio" title="可信模式 Token 倍数" description="可信模式总 Token 除以快速模式总 Token；1.35 表示使用 1.35 倍 Token。" />
        <Boundary term="trusted_token_overhead_percent" title="可信模式 Token 增幅" description="相对快速模式增加的百分比；35 表示增加 35%。" />
        <Boundary term="complete_ms" title="端到端完成时延" description="从提交请求到 answer.completed，并同时报告 mean、p50 和 p95。" />
        <Boundary term="minimum_usage_coverage" title="Token 上报覆盖率" description="只有值为 1.0 时，所有模型调用才都有供应商 usage。" />
      </div>
      <p>{t('结果还分别提供输入、缓存输入、输出、推理 Token 和模型调用数，并按单个 Case 展示，避免总体平均值掩盖任务差异。')}</p>
    </DocumentSection>

    <DocumentSection id="token-performance-boundaries" eyebrow="Interpretation" title="结果适用边界">
      <ol className="documentation-checklist">
        <li>{t('不要把不同任务的历史 Run 平均值直接相除；必须比较同一批配对 Case。')}</li>
        <li>{t('记录模型供应商、模型名、采集时间，以及 Memory、Subagent、模型思考和缓存是否开启。')}</li>
        <li>{t('Token 比例描述本次配置和样本，不代表其他模型、长任务或工具密集任务。')}</li>
        <li>{t('确定性校验的本地耗时属于运行时性能，不应误报为模型 Token。')}</li>
      </ol>
      <aside className="documentation-callout emphasis"><strong>{t('推荐报告')}</strong><p>{t('同时报告每种模式的 Token mean/p50/p95、模型调用数、完成时延、总体增幅和 usage coverage，不只给出一个倍数。')}</p></aside>
    </DocumentSection>
  </article>;
}

function RuntimeSettingsArticle() {
  const { t } = useI18n();
  return <article className="documentation-article" aria-labelledby="runtime-settings-document-title">
    <div className="documentation-hero">
      <span className="documentation-kicker">Runtime settings</span>
      <h2 id="runtime-settings-document-title">{t('模型与运行设置')}</h2>
      <p>{t('聊天区的模型浮窗同时包含模型本身的思考参数和可信模式的运行策略。它们作用于不同层级：模型思考控制一次模型调用如何推理；计划、工具预算与反思控制 Astra 如何组织整个 Run；批准方式控制工具影响是否需要人工确认。')}</p>
      <div className="documentation-summary-grid">
        <div><span>01</span><strong>{t('模型调用层')}</strong><small>{t('模型思考开关与深度，由所选模型能力决定')}</small></div>
        <div><span>02</span><strong>{t('Run 策略层')}</strong><small>{t('计划执行、推理资源、工具上限和反思触发')}</small></div>
        <div><span>03</span><strong>{t('安全与容量层')}</strong><small>{t('工具批准边界和本轮可用上下文容量')}</small></div>
      </div>
    </div>

    <DocumentSection id="runtime-settings-overview" eyebrow="Boundaries" title="这些设置分别控制什么">
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('设置')}</th><th>{t('直接控制')}</th><th>{t('不直接控制')}</th></tr></thead>
        <tbody>
          <tr><td>{t('模型思考')}</td><td>{t('当前模型调用的思考开关和深度')}</td><td>{t('是否生成计划、工具次数和批准规则')}</td></tr>
          <tr><td>{t('计划执行')}</td><td>{t('可信计划生成后先等待确认还是立即开始')}</td><td>{t('后续工具是否免于批准')}</td></tr>
          <tr><td>{t('推理强度')}</td><td>{t('Run 的工具预算档位和可用反思深度')}</td><td>{t('模型供应商提供的思考深度')}</td></tr>
          <tr><td>{t('反思循环')}</td><td>{t('何时额外检查结果并修订下一步')}</td><td>{t('基础安全检查和完成检查')}</td></tr>
          <tr><td>{t('批准方式')}</td><td>{t('可批准工具影响是否弹出人工确认')}</td><td>{t('平台禁止项、沙箱和权限边界')}</td></tr>
          <tr><td>{t('上下文容量')}</td><td>{t('本轮输入、预留回复和剩余输入的估算')}</td><td>{t('账户额度、计费 Token 和未来轮次用量')}</td></tr>
        </tbody>
      </table></div>
    </DocumentSection>

    <DocumentSection id="runtime-settings-model-thinking" eyebrow="Model call" title="模型思考">
      <p>{t('模型思考是所选模型公开的调用参数。Astra 先读取该模型是否支持开关、允许哪些深度以及默认值，再只显示可用选项。')}</p>
      <p>{t('开启后，Astra 会在过程面板保存并展示供应商明确返回的思考正文或摘要；部分供应商只提供摘要或 Token 用量，未公开的隐藏思维链无法展示。')}</p>
      <div className="documentation-boundary-list">
        <Boundary term={t('开启／关闭')} title="是否请求扩展思考" description="关闭时使用模型允许的非扩展思考路径；若供应商规定始终开启，界面会显示开启但不允许关闭。" />
        <Boundary term={t('最低／低／中／高／极高')} title="模型思考深度" description="深度只在供应商声明支持时出现；档位含义和实际 Token 消耗由具体模型决定，不等同于 Astra 的快速、均衡、深入。" />
        <Boundary term={t('不可调整')} title="能力不可用" description="模型不支持可配置参数，或能力读取暂时失败时，Astra 保留当前安全默认值并禁用调整；读取失败可以重试。" />
      </div>
      <aside className="documentation-callout neutral"><strong>{t('与推理强度的区别')}</strong><p>{t('模型思考深度作用于单次模型调用；Astra 推理强度作用于整个 Run 的工具调用预算和反思资源。两者可以独立设置。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="runtime-settings-plan-execution" eyebrow="Trusted plan" title="计划执行">
      <div className="documentation-mode-grid">
        <div className="active"><span>{t('确认后执行')}</span><strong>{t('先核对计划版本')}</strong><p>{t('先展示完整计划，由你确认这个版本后开始执行。确认只启动当前计划版本，不批准计划中可能出现的后续工具影响。')}</p></div>
        <div><span>{t('直接执行')}</span><strong>{t('计划生成后立即开始')}</strong><p>{t('完整计划通过结构校验后立即开始执行，不等待计划确认；后续工具仍分别经过权限、效果分析和批准规则。')}</p></div>
      </div>
      <aside className="documentation-callout"><strong>{t('计划确认不是工具批准')}</strong><p>{t('计划确认决定“是否开始执行这个计划版本”；工具批准决定“某个具体操作是否可以产生对应影响”。开启直接执行不会自动启用自动批准。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="runtime-settings-reasoning" eyebrow="Run budget" title="推理资源与工具调用上限">
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('档位')}</th><th>{t('工具调用范围')}</th><th>{t('行为')}</th></tr></thead>
        <tbody>
          <tr><td>{t('快速')}</td><td>0–5</td><td>{t('简单任务更快；启用反思时提供轻量反思能力。')}</td></tr>
          <tr><td>{t('均衡')}</td><td>6–15</td><td>{t('兼顾速度与检查深度；启用反思时提供基本反思能力。默认上限为 8 次。')}</td></tr>
          <tr><td>{t('深入')}</td><td>{t('不设独立工具次数上限')}</td><td>{t('为复杂任务提供更充分的执行与反思空间，但仍受 Agent 轮次、安全策略、总预算和系统限制。')}</td></tr>
        </tbody>
      </table></div>
      <ol className="documentation-checklist">
        <li>{t('工具调用上限统计一次 Run 发起的外部工具调用；失败、超时和重试也会计入。')}</li>
        <li>{t('把上限设为 0 表示该 Run 不应发起外部工具调用，但模型回答和确定性安全检查仍可继续。')}</li>
        <li>{t('切换快速或均衡档位时，超出新范围的旧值会重置到该档位默认值。')}</li>
        <li>{t('深入档位没有单独的工具次数滑块，不代表无限 Token、无限时间或绕过权限。')}</li>
      </ol>
    </DocumentSection>

    <DocumentSection id="runtime-settings-reflection" eyebrow="Reflection" title="反思循环与触发方式">
      <p>{t('反思允许 Agent 在预算内检查观察结果、失败或完成状态，并修订下一步策略。它是可选的额外推理，不替代确定性的权限检查、工具结果校验和完成门槛。')}</p>
      <div className="documentation-boundary-list">
        <Boundary term={t('关闭')} title="不调用额外反思" description="安全检查、工具结果校验和完成检查仍保留。" />
        <Boundary term={t('失败时')} title="只对明确失败反思" description="仅在工具、模型输出或完成检查失败时触发，额外开销最低。" />
        <Boundary term={t('按需')} title="对风险信号自适应反思" description="在失败、低置信度、证据冲突或无进展时触发，是默认的平衡选择。" />
        <Boundary term={t('每轮')} title="每轮结束都反思" description="检查最频繁，更审慎但延迟和模型用量通常更高。" />
      </div>
    </DocumentSection>

    <DocumentSection id="runtime-settings-approvals" eyebrow="Safety" title="请求批准与自动批准">
      <div className="documentation-mode-grid">
        <div className="active"><span>{t('请求批准')}</span><strong>{t('按具体影响确认')}</strong><p>{t('无副作用行为可以自动执行；写文件、删除、外部修改、凭据使用等操作按运行时识别出的影响和授权范围决定是否询问。')}</p></div>
        <div><span>{t('自动批准')}</span><strong>{t('跳过可批准行为的交互确认')}</strong><p>{t('适合你信任当前任务和运行环境的情况。它只跳过原本允许用户批准的确认，不会把禁止操作变成允许。')}</p></div>
      </div>
      <ol className="documentation-checklist">
        <li>{t('平台禁止项、权限边界、预算、沙箱、凭据范围和工具可用性始终生效。')}</li>
        <li>{t('计划确认与工具批准相互独立：确认计划不会批准工具，自动批准也不会替你确认计划版本。')}</li>
        <li>{t('请求批准模式中的持续授权只在显示的 Run 或 Task 范围内有效，并可在任务安全中心撤销。')}</li>
        <li>{t('无法可靠识别影响的操作会按更保守的规则处理。')}</li>
      </ol>
    </DocumentSection>

    <DocumentSection id="runtime-settings-context" eyebrow="Context" title="上下文容量如何计算">
      <p>{t('上下文看板是发送前估算，不是供应商账单。它把模型窗口拆成可用输入和回复预留，再把可用输入中的当前占用按来源展示。')}</p>
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('项目')}</th><th>{t('计入内容')}</th><th>{t('明确不计入')}</th></tr></thead>
        <tbody>
          <tr><td>{t('系统指令与工具定义预留')}</td><td>{t('系统指令、Agent 执行与安全约束、可用工具接口的固定额度')}</td><td>{t('对话、草稿和回复预留')}</td></tr>
          <tr><td>{t('较早轮次的压缩摘要')}</td><td>{t('整理后生成的一段较早用户目标和最终回答摘要')}</td><td>{t('已折叠原始轮次的重复占用')}</td></tr>
          <tr><td>{t('未折叠轮次')}</td><td>{t('每个可见运行的用户目标与最终摘要')}</td><td>{t('工具日志、思考过程和中间事件')}</td></tr>
          <tr><td>{t('当前草稿')}</td><td>{t('输入框文字和一条消息的固定开销')}</td><td>{t('尚未接入的附件内容')}</td></tr>
          <tr><td>{t('已选 Skill')}</td><td>{t('名称和说明的界面估算，从系统预留中拆出展示')}</td><td>{t('完整 SKILL.md 大小或额外叠加用量')}</td></tr>
          <tr><td>{t('回复预留')}</td><td>{t('Astra 回复预留上限与模型单次最大输出上限的较小值')}</td><td>{t('已使用输入')}</td></tr>
        </tbody>
      </table></div>
      <aside className="documentation-callout emphasis"><strong>{t('计算公式')}</strong><p>{t('可用输入 = 模型窗口 − 回复预留；已使用输入 = 系统预留 + 压缩摘要 + 未折叠轮次 + 当前草稿；剩余输入 = 可用输入 − 已使用输入。')}</p></aside>
      <p className="documentation-footnote">{t('估算器把中文、日文、韩文字符按 1 Token 计算，其他字符按每 3.2 个字符约 1 Token 计算，每条消息另加 6 Token。供应商实际分词和计费结果可能不同。')}</p>
    </DocumentSection>

    <DocumentSection id="runtime-settings-effective-scope" eyebrow="Lifecycle" title="设置何时生效">
      <ol className="documentation-timeline">
        <TimelineStep number="1" title="选择模型" description="模型选择和模型思考参数用于之后发起的模型调用；正在执行的调用不会被中途改写。" />
        <TimelineStep number="2" title="调整可信策略" description="计划执行、推理强度、工具上限和反思设置用于之后创建或继续的 Run；已冻结的计划版本和已完成事件不会被追溯修改。" />
        <TimelineStep number="3" title="选择批准方式" description="批准方式应用于之后到达权限门的工具操作；已经等待用户决定的批准请求仍需按当前卡片处理。" />
        <TimelineStep number="4" title="查看上下文" description="看板随当前对话、草稿、所选 Skill 和模型能力更新，显示的是下一次发送前的估算。" />
      </ol>
    </DocumentSection>
  </article>;
}

function AboutArticle() {
  const { t } = useI18n();
  return <article className="documentation-article" aria-labelledby="about-document-title">
    <div className="documentation-hero">
      <span className="documentation-kicker">About Astra</span>
      <h2 id="about-document-title">{t('关于 Astra')}</h2>
      <p>{t('Astra 是一个 AI 原生的通用 Agent 平台。它希望把前沿模型的理解与推理能力，连接到可持续执行、真实工具操作、结果验证和长期记忆。')}</p>
      <div className="documentation-summary-grid">
        <div><span>01</span><strong>{t('从回答走向完成')}</strong><small>{t('不仅提供建议，也能在受控边界内执行真实任务')}</small></div>
        <div><span>02</span><strong>{t('通用而非单一场景')}</strong><small>{t('围绕用户目标组织模型、工具、工作空间与知识')}</small></div>
        <div><span>03</span><strong>{t('可治理地长期演进')}</strong><small>{t('让计划、权限、证据、记忆和改进保持可见与可控')}</small></div>
      </div>
    </div>

    <DocumentSection id="about-origin" eyebrow="Motivation" title="为什么创建 Astra">
      <p>{t('大模型已经能够理解复杂请求、生成内容和调用工具，但普通聊天通常停留在一次回复：上下文会消失，计划难以持续，真实操作缺少统一权限边界，结果也不一定经过验证。用户仍需自己在模型、文件、工具和外部系统之间搬运信息并检查完成情况。')}</p>
      <div className="documentation-problem-grid">
        <div><strong>{t('任务不能止于建议')}</strong><p>{t('真实目标往往需要多步骤执行、工具操作、文件交付和失败恢复。')}</p></div>
        <div><strong>{t('能力需要统一治理')}</strong><p>{t('模型能力、工具权限、审批、预算和运行环境必须由确定性系统共同约束。')}</p></div>
        <div><strong>{t('经验需要可靠延续')}</strong><p>{t('任务、证据、产物和长期记忆应当可恢复、可审计，并在明确范围内复用。')}</p></div>
      </div>
      <aside className="documentation-callout"><strong>{t('创建 Astra 的出发点')}</strong><p>{t('在前沿模型之上构建一层通用任务操作系统，让 AI 从“给出下一步建议”走向“在用户控制下可靠地完成目标”。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="about-mission" eyebrow="Mission" title="我们的使命">
      <p>{t('Astra 的使命是让个人和团队能够安全地把目标交给 Agent：系统理解目标与环境，形成适当计划，调用真实能力完成工作，验证交付结果，并在不削弱隐私、权限和用户控制的前提下持续积累经验。')}</p>
      <ol className="documentation-timeline">
        <TimelineStep number="1" title="理解目标与环境" description="理解用户意图、当前对话、工作空间、知识来源和可用系统。" />
        <TimelineStep number="2" title="组织可执行工作" description="根据任务复杂度选择快速循环或可信计划，并在需要时协调工具与 Subagent。" />
        <TimelineStep number="3" title="完成并验证交付" description="产生真实操作、文件和证据，检查成功标准并诚实呈现失败与不确定性。" />
        <TimelineStep number="4" title="在治理中持续学习" description="通过有来源的记忆、审计和离线评估积累经验，而不是未经允许改变生产行为。" />
      </ol>
    </DocumentSection>

    <DocumentSection id="about-principles" eyebrow="Principles" title="核心原则">
      <div className="documentation-boundary-list">
        <Boundary term="General" title="通用目标优先" description="Astra 不局限于代码或单一工具，而是围绕用户目标组合合适的模型、能力和工作流。" />
        <Boundary term="Governed" title="能力必须受治理" description="模型不能自行扩大权限；工具、审批、预算、凭据、工作空间和网络访问由运行时控制。" />
        <Boundary term="Durable" title="执行必须可恢复" description="Run、计划、行动、Subagent、产物和等待状态以持久化记录为准，不依赖一次进程生命周期。" />
        <Boundary term="Verifiable" title="结果需要可检查" description="重要结论应连接到来源、Evidence、Artifact 和验证状态，失败与警告不能伪装成成功。" />
        <Boundary term="Human control" title="用户保持最终控制" description="高影响操作需要审批，记忆与改进需要治理，最终决定和责任不被 Agent 取代。" />
      </div>
    </DocumentSection>

    <DocumentSection id="about-boundary" eyebrow="Identity" title="Astra 是什么">
      <div className="documentation-mode-grid">
        <div className="active"><span>{t('Astra 是')}</span><strong>{t('通用 Agent 运行平台')}</strong><p>{t('它把模型推理、任务状态、工具、权限、工作空间、证据、记忆和多 Agent 协作组织在一个可恢复运行时中。')}</p></div>
        <div><span>{t('Astra 不是')}</span><strong>{t('只会对话的模型外壳')}</strong><p>{t('它不把提示词约定当作安全边界，也不把单次模型输出当作已完成且已验证的真实任务。')}</p></div>
      </div>
      <aside className="documentation-callout neutral"><strong>{t('关于“我们”')}</strong><p>{t('这里的“我们”指 Astra 项目及其贡献者。项目文档不推断或声明仓库元数据中不存在的组织、团队或个人身份。')}</p></aside>
    </DocumentSection>

    <DocumentSection id="about-copyright" eyebrow="Copyright" title="版权与许可证">
      <p>{t('Astra 源代码与文档的版权归各自权利人和贡献者所有。当前仓库没有声明单一、排他的版权主体，因此本页不推断个人、组织或版权年份。')}</p>
      <div className="documentation-table-wrap"><table>
        <thead><tr><th>{t('项目')}</th><th>{t('说明')}</th></tr></thead>
        <tbody>
          <tr><td>{t('开源许可证')}</td><td><strong>Apache License 2.0</strong></td></tr>
          <tr><td>{t('允许')}</td><td>{t('在遵守许可证条件的前提下使用、复制、修改和分发源码或衍生作品。')}</td></tr>
          <tr><td>{t('需要')}</td><td>{t('随分发保留许可证和适用的版权、专利、商标与归属声明，并对修改过的文件作出显著说明。')}</td></tr>
          <tr><td>{t('免责声明')}</td><td>{t('软件按“原样”提供，不附带明示或默示保证；适用责任限制以许可证原文为准。')}</td></tr>
          <tr><td>{t('名称与标识')}</td><td>{t('Apache License 2.0 不自动授予使用项目名称、商标或产品标识进行背书的权利。')}</td></tr>
        </tbody>
      </table></div>
      <aside className="documentation-callout emphasis"><strong>{t('许可证摘要不是法律文本')}</strong><p>{t('本节用于帮助理解，不替代完整许可证。使用、修改或分发 Astra 前，请阅读 Apache License 2.0 原文。')}</p><a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">{t('阅读 Apache License 2.0 完整原文')}</a></aside>
    </DocumentSection>

    <DocumentSection id="about-accuracy" eyebrow="Versioning" title="信息与版本">
      <p>{t('帮助文档描述当前安装版本的产品行为。Astra 持续演进时，功能、界面和运行边界可能变化；应用内实际行为、发行说明、仓库 VERSION 文件和完整 LICENSE 是对应信息的权威来源。')}</p>
      <aside className="documentation-callout"><strong>{t('文档也属于产品')}</strong><p>{t('如果实现与帮助说明不一致，应将其视为需要修复的产品问题：安全边界以运行时强制规则为准，随后同步更新文档和测试。')}</p></aside>
    </DocumentSection>
  </article>;
}

function DocumentSection({ id, eyebrow, title, children }: { id: string; eyebrow: string; title: string; children: ReactNode }) {
  const { t } = useI18n();
  return <section className="documentation-section" id={id} aria-labelledby={`${id}-title`}>
    <span className="documentation-section-eyebrow">{eyebrow}</span>
    <h2 id={`${id}-title`}>{t(title)}</h2>
    {children}
  </section>;
}

function Boundary({ term, title, description }: { term: string; title: string; description: string }) {
  const { t } = useI18n();
  return <div><code>{term}</code><span><strong>{t(title)}</strong><p>{t(description)}</p></span></div>;
}

function TimelineStep({ number, title, description }: { number: string; title: string; description: string }) {
  const { t } = useI18n();
  return <li><span>{number}</span><div><strong>{t(title)}</strong><p>{t(description)}</p></div></li>;
}
