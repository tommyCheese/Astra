import '@testing-library/jest-dom/vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../src/i18n';
import { ModelThinkingContent } from '../src/ModelThinkingContent';

describe('ModelThinkingContent', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders provider-visible text with whitespace and truncation state', () => {
    const { container } = render(<I18nProvider><ModelThinkingContent item={{
      id: 'model-thinking-1', kind: 'model_thinking', title: '模型思考', status: 'running',
      detail: '第一行\n  第二行', provider: 'qwen', operation: 'synthesis', contentLevel: 'reasoning', truncated: true,
    }} /></I18nProvider>);

    expect(screen.getByText('qwen · synthesis')).toBeInTheDocument();
    expect(container.querySelector('pre')).toHaveTextContent('第一行 第二行');
    expect(container.querySelector('pre')?.textContent).toBe('第一行\n  第二行');
    expect(screen.getByText('内容超过保存上限，以下记录已被截断。')).toBeInTheDocument();
    expect(screen.getByText('运行中')).toBeInTheDocument();
    expect(container.querySelector('details')).toHaveAttribute('open');
  });

  it('follows the latest delta inside the expanded content and stops after collapse', () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
    const initial = {
      id: 'model-thinking-live', kind: 'model_thinking' as const, title: '模型思考', status: 'running' as const,
      detail: '第一段', provider: 'deepseek', operation: 'decision', contentLevel: 'reasoning' as const,
    };
    const { container, rerender } = render(<I18nProvider><ModelThinkingContent item={initial} /></I18nProvider>);
    const content = container.querySelector('pre') as HTMLPreElement;
    Object.defineProperty(content, 'scrollHeight', { configurable: true, value: 640 });

    rerender(<I18nProvider><ModelThinkingContent item={{ ...initial, detail: '第一段\n第二段' }} /></I18nProvider>);
    act(() => frames.splice(0).forEach((callback) => callback(16)));
    expect(content.scrollTop).toBe(640);
    expect(content).toHaveAttribute('data-follow-latest', 'true');

    const details = container.querySelector('details') as HTMLDetailsElement;
    details.open = false;
    fireEvent(details, new Event('toggle'));
    expect(content).toHaveAttribute('data-follow-latest', 'false');
    const scheduledAfterCollapse = frames.length;
    rerender(<I18nProvider><ModelThinkingContent item={{ ...initial, detail: '第一段\n第二段\n第三段' }} /></I18nProvider>);
    expect(frames).toHaveLength(scheduledAfterCollapse);
  });

  it('paces a burst across animation frames and reveals completion immediately', () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
    const initial = {
      id: 'model-thinking-paced', kind: 'model_thinking' as const, title: '模型思考', status: 'running' as const,
      detail: '起点', provider: 'qwen', operation: 'synthesis', contentLevel: 'reasoning' as const,
    };
    const burst = `${initial.detail}${'新增思考'.repeat(80)}`;
    const { container, rerender } = render(<I18nProvider><ModelThinkingContent item={initial} /></I18nProvider>);

    rerender(<I18nProvider><ModelThinkingContent item={{ ...initial, detail: burst }} /></I18nProvider>);
    expect(container.querySelector('pre')?.textContent).not.toBe(burst);
    act(() => frames.shift()?.(16));
    const firstFrame = container.querySelector('pre')?.textContent ?? '';
    expect(firstFrame.length).toBeGreaterThan(initial.detail.length);
    expect(firstFrame.length).toBeLessThan(burst.length);

    rerender(<I18nProvider><ModelThinkingContent item={{ ...initial, detail: burst, status: 'completed' }} /></I18nProvider>);
    expect(container.querySelector('pre')?.textContent).toBe(burst);
  });

  it('explains when the provider exposes no displayable thinking', () => {
    render(<I18nProvider><ModelThinkingContent item={{
      id: 'model-thinking-2', kind: 'model_thinking', title: '模型思考不可见', status: 'completed',
      provider: 'openai', operation: 'synthesis', contentLevel: 'unavailable', unavailableReason: 'provider_did_not_return_visible_thinking',
    }} /></I18nProvider>);

    expect(screen.getByText('该模型未公开可展示的思考内容。')).toBeInTheDocument();
  });
});
