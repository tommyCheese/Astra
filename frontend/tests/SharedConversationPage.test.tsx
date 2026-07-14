import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
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
});
