import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import TrustedExecutionGraph from '../src/TrustedExecutionGraph';
import { complexDagRunFixture } from '../src/dev/complexDagFixture';
import { I18nProvider } from '../src/i18n';

describe('TrustedExecutionGraph complex DAG', () => {
  it('renders every branch, merge and transitively blocked node', async () => {
    const { container } = render(
      <I18nProvider><TrustedExecutionGraph run={complexDagRunFixture} title="复杂多路可信执行图谱" /></I18nProvider>,
    );

    expect(screen.getByRole('region', { name: '复杂多路可信执行图谱' })).toBeInTheDocument();
    await waitFor(() => {
      expect(container.querySelectorAll('.react-flow__node')).toHaveLength(16);
    });
    expect(container.querySelectorAll('.trusted-plan-node.status-blocked')).toHaveLength(6);
    expect(container.querySelectorAll('.trusted-plan-node.status-failed')).toHaveLength(1);
    expect(container.querySelectorAll('.plan-edge.status-blocked').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('.plan-edge.status-running').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('.trusted-plan-node.status-running')).toHaveLength(3);
    expect(container.querySelector('.trusted-plan-node.status-running')).toHaveAttribute('data-node-status', 'running');
    expect(container.querySelector('.trusted-plan-node.status-running')).toHaveAttribute('aria-current', 'step');
    expect(container.querySelector('.trusted-plan-node.status-completed')).toHaveAttribute('data-node-status', 'completed');
    expect(container.querySelector('.trusted-plan-node.status-completed')).not.toHaveAttribute('aria-current');
    expect(screen.getByText(/2 个活动节点 · 并行 2\/3/)).toBeInTheDocument();
    expect(screen.getByText('等待资源释放')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '定位活动节点 (3)' })).toBeInTheDocument();
    expect(screen.queryByText('结构化节点列表')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '缩小图谱' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放大图谱' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '定位中心' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '展开图谱' })).not.toBeInTheDocument();
    expect(container.querySelector('.react-flow__minimap')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '放大图谱' }));
    fireEvent.click(screen.getByRole('button', { name: '缩小图谱' }));
    fireEvent.click(screen.getByRole('button', { name: '定位中心' }));

    const finalNode = screen.getByRole('button', {
      name: '节点 16：双重验证汇合并形成最终交付，受阻，依赖 fact_check、feasibility_check',
    });
    expect(finalNode).toBeVisible();
    fireEvent.keyDown(finalNode, { key: 'Enter' });
    expect(screen.getByRole('complementary', {
      name: '双重验证汇合并形成最终交付 节点详情',
    })).toHaveTextContent('受阻');
  });
});
