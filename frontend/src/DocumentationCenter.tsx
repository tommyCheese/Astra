import { ReactNode, useState } from 'react';
import { CloseButton } from './CloseButton';
import { useI18n } from './i18n';

type DocumentationTopic = 'memory';

const topics: Array<{ id: DocumentationTopic; label: string; description: string }> = [
  { id: 'memory', label: '记忆', description: '生产、召回、范围与整理' },
];

const sections = [
  ['memory-background', '为什么需要记忆'],
  ['memory-boundaries', '四个容易混淆的概念'],
  ['memory-lifecycle', '记忆如何产生并生效'],
  ['memory-scope', '作用范围'],
  ['memory-recall', '如何检索与召回'],
  ['memory-autodream', 'AutoDream 如何整理'],
  ['memory-faq', '常见问题'],
] as const;

export function DocumentationCenter({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [topic, setTopic] = useState<DocumentationTopic>('memory');

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
        {topic === 'memory' && <MemoryArticle />}
      </div>
    </div>
  </section>;
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

    <nav className="documentation-toc" aria-label={t('本页目录')}>
      <span>{t('本页目录')}</span>
      <div>{sections.map(([id, label]) => <a href={`#${id}`} key={id}>{t(label)}</a>)}</div>
    </nav>

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
