import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getSharedConversation } from '../src/api';
import { SharedConversationPage } from '../src/SharedConversationPage';

vi.mock('../src/api', () => ({
  getSharedConversation: vi.fn(async () => ({
    title: '公开标题',
    messages: [
      { role: 'user', content: '公开问题', items: [] },
      { role: 'process', content: '', items: [{ kind: 'reasoning', title: '思考', detail: '正在分析公开问题', status: 'completed' }, { kind: 'tool', title: 'web_search', detail: '', status: 'completed' }] },
      { role: 'assistant', content: '**公开回答**', items: [] },
    ],
    shared_at: '2026-07-14T00:00:00Z',
    updated_at: '2026-07-14T00:00:00Z',
  })),
}));

describe('SharedConversationPage', () => {
  beforeEach(() => {
    vi.mocked(getSharedConversation).mockResolvedValue({
      title: '公开标题',
      messages: [
        { role: 'user', content: '公开问题', items: [] },
        { role: 'process', content: '', items: [{ kind: 'reasoning', title: '思考', detail: '正在分析公开问题', status: 'completed' }, { kind: 'tool', title: 'web_search', detail: '', status: 'completed' }] },
        { role: 'assistant', content: '**公开回答**', items: [] },
      ],
      shared_at: '2026-07-14T00:00:00Z',
      updated_at: '2026-07-14T00:00:00Z',
    });
  });

  it('renders a standalone read-only conversation without composer controls', async () => {
    render(<SharedConversationPage token="token" />);

    expect(await screen.findByRole('heading', { name: '公开标题' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Astra' })).toHaveAttribute('src', '/astra.svg');
    expect(screen.getByText('公开问题')).toBeInTheDocument();
    expect(screen.getByText('公开回答').tagName).toBe('STRONG');
    expect(screen.getByText('思考过程')).toBeInTheDocument();
    expect(screen.getByText('2 个步骤')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByText('历史对话')).not.toBeInTheDocument();
  });

  it('ignores a stale response after the share token changes', async () => {
    let resolveFirst!: (value: Awaited<ReturnType<typeof getSharedConversation>>) => void;
    vi.mocked(getSharedConversation)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({
        title: '新分享', messages: [], shared_at: '2026-07-15T00:00:00Z', updated_at: '2026-07-15T00:00:00Z',
      });

    const { rerender } = render(<SharedConversationPage token="old" />);
    rerender(<SharedConversationPage token="new" />);
    expect(await screen.findByRole('heading', { name: '新分享' })).toBeInTheDocument();

    resolveFirst({
      title: '旧分享', messages: [], shared_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z',
    });
    await waitFor(() => expect(screen.queryByRole('heading', { name: '旧分享' })).not.toBeInTheDocument());
    expect(getSharedConversation).toHaveBeenLastCalledWith('new', expect.any(AbortSignal));
  });
});
