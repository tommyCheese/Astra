import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ActivityView, InterruptView } from '../src/agui/components';
import type { ProjectedActivity } from '../src/agui/store';

function activity(activityType: string, error?: string): ProjectedActivity {
  return {
    messageId: 'activity-1',
    activityType,
    content: { schemaVersion: 1, revision: 1, fallbackText: '安全降级摘要' },
    schemaVersion: 1,
    revision: 1,
    error,
  };
}

describe('AG-UI React projections', () => {
  it('uses the specialized registry and an accessible generic fallback', () => {
    const { rerender } = render(<ActivityView activity={activity('astra.plan')} />);
    expect(screen.getByRole('region', { name: '执行计划' })).toHaveTextContent('安全降级摘要');

    rerender(<ActivityView activity={activity('vendor.unknown')} />);
    expect(screen.getByRole('region', { name: 'Agent 活动' })).toHaveTextContent('安全降级摘要');

    rerender(<ActivityView activity={activity('astra.plan', 'revision gap')} />);
    expect(screen.getByRole('status')).toHaveTextContent('revision gap');
  });

  it('renders tool approval decisions without inventing allow-similar', () => {
    const resolve = vi.fn();
    render(<InterruptView interrupt={{
      id: 'interrupt-1',
      reason: 'tool_call',
      message: 'Run command?',
      responseSchema: {
        type: 'object',
        properties: { decision: { type: 'string', enum: ['approve_once', 'reject'] } },
      },
    }} onResolve={resolve} />);
    expect(screen.queryByRole('option', { name: 'allow_similar' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('审批决定'), { target: { value: 'approve_once' } });
    fireEvent.click(screen.getByRole('button', { name: '继续' }));
    expect(resolve).toHaveBeenCalledWith({
      interruptId: 'interrupt-1',
      status: 'resolved',
      payload: { decision: 'approve_once' },
    });
  });

  it('supports keyboard-friendly generic input and cancellation', () => {
    const resolve = vi.fn();
    const view = render(<InterruptView interrupt={{ id: 'interrupt-2', reason: 'input_required' }} onResolve={resolve} />);
    fireEvent.change(view.getByLabelText('响应内容'), { target: { value: 'more context' } });
    fireEvent.submit(view.container.querySelector('form')!);
    expect(resolve).toHaveBeenCalledWith({ interruptId: 'interrupt-2', status: 'resolved', payload: 'more context' });
    const cancel = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '取消');
    fireEvent.click(cancel!);
    expect(resolve).toHaveBeenLastCalledWith({ interruptId: 'interrupt-2', status: 'cancelled' });
  });
});
