import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRun, takeCreatedRunStream, type RunStreamEvent } from '../src/api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('createRun streaming fast path', () => {
  it('creates and receives answer events on one HTTP connection', async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(
          'data: {"type":"stream.ready","payload":{"run_id":"run-1","task_id":"task-1","status":"created","answer_mode":"standard"}}\n\n',
        ));
        controller.enqueue(encoder.encode(
          'id: 2\ndata: {"id":2,"type":"answer.delta","payload":{"delta":"首"}}\n\n',
        ));
        controller.close();
      },
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const created = await createRun('测试快速流', undefined, 'standard');
    const stream = takeCreatedRunStream(created.run_id);
    const events: RunStreamEvent[] = [];
    stream?.subscribe((event) => events.push(event));
    await Promise.resolve();

    expect(created).toEqual({
      run_id: 'run-1',
      task_id: 'task-1',
      status: 'created',
      answer_mode: 'standard',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/runs/stream');
    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(requestBody.session_id).toEqual(expect.any(String));
    expect(requestBody.session_id.length).toBeGreaterThan(0);
    expect(events.map((event) => event.type)).toEqual([
      'stream.ready',
      'answer.delta',
    ]);
  });
});
