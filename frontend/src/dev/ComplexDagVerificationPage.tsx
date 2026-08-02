import TrustedExecutionGraph from '../TrustedExecutionGraph';
import { I18nProvider } from '../i18n';
import { complexDagRunFixture } from './complexDagFixture';

export default function ComplexDagVerificationPage() {
  return <I18nProvider><ComplexDagVerificationContent /></I18nProvider>;
}

function ComplexDagVerificationContent() {
  return <main style={{ minHeight: '100vh', padding: 24 }}>
    <header style={{ maxWidth: 1160, margin: '0 auto 16px' }}>
      <p style={{ margin: 0, color: '#69788b', fontSize: 12 }}>DEVELOPMENT VISUAL FIXTURE</p>
      <h1 style={{ margin: '4px 0' }}>复杂多路 DAG 验收</h1>
      <p style={{ margin: 0 }}>
        16 个节点 · 22 条边 · 多级扇出/汇合 · 跨分支依赖 · 失败阻塞传播
      </p>
    </header>
    <div style={{ maxWidth: 1160, margin: '0 auto' }}>
      <TrustedExecutionGraph run={complexDagRunFixture} title="复杂多路可信执行图谱" />
    </div>
  </main>;
}
