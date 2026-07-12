import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FloatingAd } from './FloatingAd';

describe('FloatingAd', () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
      },
    });
  });
  afterEach(cleanup);

  it('can be permanently dismissed by the user', async () => {
    const props = { id: 'campaign', imageSrc: '/poster.png', imageAlt: '活动海报' };
    const { unmount } = render(<FloatingAd {...props} />);

    expect(screen.getByRole('complementary', { name: '推广内容' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '关闭广告' }));
    expect(screen.queryByRole('complementary', { name: '推广内容' })).not.toBeInTheDocument();

    unmount();
    render(<FloatingAd {...props} />);
    expect(screen.queryByRole('complementary', { name: '推广内容' })).not.toBeInTheDocument();
  });
});
