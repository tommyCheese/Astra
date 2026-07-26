import { useState } from 'react';
import { GraphPaneWindowActions } from '../GraphPaneWindowActions';
import TrustedExecutionGraph from '../TrustedExecutionGraph';
import { complexDagRunFixture } from './complexDagFixture';

export default function ComplexDagPaneVerificationPage() {
  const [expanded, setExpanded] = useState(false);
  return <main style={{ minHeight: '100vh', padding: '0 24px' }}>
    <section
      className={`chat-surface has-trusted-graph-pane ${expanded ? 'trusted-graph-pane-expanded' : ''}`}
      style={{ height: '100vh' }}
    >
      <div className="conversation">
        <p style={{ margin: 0, color: '#69788b', fontSize: 12 }}>DEVELOPMENT VISUAL FIXTURE</p>
        <h1 style={{ margin: '5px 0 20px' }}>对话与图谱半屏验收</h1>
        <article className="bubble user"><p>分析一个包含并行研究、跨分支依赖与双重验证的复杂任务。</p></article>
        <article className="bubble assistant">
          <span className="bubble-label">Astra</span>
          <p>计划正在执行。扩大右侧图谱窗格后，这段对话与完整图谱应各占可用内容区的一半。</p>
        </article>
      </div>
      <aside className={`trusted-graph-floating-pane ${expanded ? 'expanded' : ''}`} aria-label="执行图谱窗格">
        <GraphPaneWindowActions
          expanded={expanded}
          expandLabel="扩大图谱窗格"
          restoreLabel="恢复图谱窗格"
          closeLabel="收起图谱"
          onExpandedChange={setExpanded}
          onClose={() => undefined}
        />
        <TrustedExecutionGraph run={complexDagRunFixture} compact={!expanded} title="复杂多路可信执行图谱" />
      </aside>
    </section>
  </main>;
}
