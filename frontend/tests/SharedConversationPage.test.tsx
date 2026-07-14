import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SharedConversationPage } from '../src/SharedConversationPage';

vi.mock('../src/api', () => ({
  getSharedConversation: vi.fn(async () => ({
    title: '公开标题',
    messages: [{ role: 'user', content: '公开问题' }, { role: 'assistant', content: '**公开回答**' }],
    shared_at: '2026-07-14T00:00:00Z',
    updated_at: '2026-07-14T00:00:00Z',
  })),
}));

describe('SharedConversationPage', () => {
  it('renders a standalone read-only conversation without composer controls', async () => {
    render(<SharedConversationPage token="token" />);

    expect(await screen.findByRole('heading', { name: '公开标题' })).toBeInTheDocument();
    expect(screen.getByText('公开问题')).toBeInTheDocument();
    expect(screen.getByText('公开回答').tagName).toBe('STRONG');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByText('历史对话')).not.toBeInTheDocument();
  });
});
