import { afterEach, describe, expect, it, vi } from 'vitest';

import { updateRuntimeMemorySettings, type MemoryRuntimeSettings } from '../src/api';

const settings: MemoryRuntimeSettings = {
  write_enabled: true,
  cross_session_mode: 'shadow',
  retrieval_max_items: 5,
  retrieval_max_tokens: 1800,
  retrieval_min_confidence: 0.3,
  retrieval_min_score: 0.1,
  autodream_enabled: false,
  autodream_scan_seconds: 3600,
  autodream_min_candidates: 3,
};

describe('Runtime Memory settings API', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('updates the complete typed settings document', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(settings), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(updateRuntimeMemorySettings(settings)).resolves.toEqual(settings);
    expect(fetchMock).toHaveBeenCalledWith('/api/runtime/memory-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  });

  it('surfaces a rejected update without changing the caller document', async () => {
    const original = structuredClone(settings);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { message: '记忆运行设置 retrieval_min_score 超出允许范围' },
    }), {
      status: 422,
      headers: { 'Content-Type': 'application/json' },
    })));

    await expect(updateRuntimeMemorySettings(settings)).rejects.toThrow();
    expect(settings).toEqual(original);
  });
});
