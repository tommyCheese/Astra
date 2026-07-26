import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import TrustedExecutionGraph from '../src/TrustedExecutionGraph';
import { complexDagRunFixture } from '../src/dev/complexDagFixture';

describe('TrustedExecutionGraph complex DAG', () => {
  it('renders every branch, merge and transitively blocked node', async () => {
    const { container } = render(
      <TrustedExecutionGraph run={complexDagRunFixture} title="复杂多路可信执行图谱" />,
    );

    expect(screen.getByRole('region', { name: '复杂多路可信执行图谱' })).toBeInTheDocument();
    await waitFor(() => {
      expect(container.querySelectorAll('.react-flow__node')).toHaveLength(16);
    });
    expect(container.querySelectorAll('.status-blocked')).toHaveLength(6);
    expect(container.querySelectorAll('.status-failed')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '缩小图谱' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放大图谱' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '适应视图' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '放大图谱' }));
    fireEvent.click(screen.getByRole('button', { name: '缩小图谱' }));
    fireEvent.click(screen.getByRole('button', { name: '适应视图' }));

    const finalNode = screen.getByRole('article', { name: '双重验证汇合并形成最终交付，受阻' });
    expect(finalNode).toBeVisible();
    fireEvent.click(finalNode);
    expect(screen.getByRole('complementary', {
      name: '双重验证汇合并形成最终交付 节点详情',
    })).toHaveTextContent('受阻');
  });
});
