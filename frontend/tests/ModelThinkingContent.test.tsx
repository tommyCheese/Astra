import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../src/i18n';
import { ModelThinkingContent } from '../src/ModelThinkingContent';

describe('ModelThinkingContent', () => {
  it('renders provider-visible text with whitespace and truncation state', () => {
    const { container } = render(<I18nProvider><ModelThinkingContent item={{
      id: 'model-thinking-1', kind: 'model_thinking', title: '模型思考', status: 'running',
      detail: '第一行\n  第二行', provider: 'qwen', operation: 'synthesis', contentLevel: 'reasoning', truncated: true,
    }} /></I18nProvider>);

    expect(screen.getByText('qwen · synthesis')).toBeInTheDocument();
    expect(container.querySelector('pre')).toHaveTextContent('第一行 第二行');
    expect(container.querySelector('pre')?.textContent).toBe('第一行\n  第二行');
    expect(screen.getByText('内容超过保存上限，以下记录已被截断。')).toBeInTheDocument();
    expect(container.querySelector('details')).toHaveAttribute('open');
  });

  it('explains when the provider exposes no displayable thinking', () => {
    render(<I18nProvider><ModelThinkingContent item={{
      id: 'model-thinking-2', kind: 'model_thinking', title: '模型思考不可见', status: 'completed',
      provider: 'openai', operation: 'synthesis', contentLevel: 'unavailable', unavailableReason: 'provider_did_not_return_visible_thinking',
    }} /></I18nProvider>);

    expect(screen.getByText('该模型未公开可展示的思考内容。')).toBeInTheDocument();
  });
});
