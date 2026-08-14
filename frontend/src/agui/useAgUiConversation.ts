import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import type { BaseEvent, ResumeEntry, RunAgentInput } from '@ag-ui/core';
import { AgUiFrameBatcher } from './batching';
import {
  initialAgUiProjectionStore,
  markAgUiDisconnected,
  reduceAgUiEvent,
  withAgUiCapabilities,
  type AgUiProjectedEvent,
} from './store';
import type { AstraAgentStream, AstraAgentTransport } from './transport';

export function useAgUiConversation(transport: AstraAgentTransport) {
  type Action =
    | { kind: 'event'; event: AgUiProjectedEvent }
    | { kind: 'disconnected' }
    | { kind: 'capabilities'; value: Awaited<ReturnType<AstraAgentTransport['getCapabilities']>> };
  const [state, dispatch] = useReducer(
    (current: ReturnType<typeof initialAgUiProjectionStore>, action: Action) => (
      action.kind === 'event'
        ? reduceAgUiEvent(current, action.event)
        : action.kind === 'capabilities'
          ? withAgUiCapabilities(current, action.value)
          : markAgUiDisconnected(current)
    ),
    undefined,
    initialAgUiProjectionStore,
  );
  const stream = useRef<AstraAgentStream | null>(null);
  const batcher = useMemo(() => new AgUiFrameBatcher((events) => {
    for (const event of events) dispatch({ kind: 'event', event });
  }), []);

  const callbacks = useMemo(() => ({
    onEvent: (event: BaseEvent) => batcher.push(event as AgUiProjectedEvent),
    onError: (error: Error) => {
      batcher.flush();
      dispatch({ kind: 'event', event: { type: 'RUN_ERROR', message: error.message } as AgUiProjectedEvent });
    },
    onComplete: () => batcher.flush(),
  }), [batcher]);

  const start = useCallback((input: RunAgentInput) => {
    if (state.pendingInterrupts.length) throw new Error('请先处理当前待响应事项。');
    stream.current?.close();
    stream.current = transport.start(input, callbacks);
  }, [callbacks, state.pendingInterrupts.length, transport]);

  const resume = useCallback((input: RunAgentInput, responses: ResumeEntry[]) => {
    stream.current?.close();
    stream.current = transport.resume({ ...input, resume: responses }, callbacks);
  }, [callbacks, transport]);

  const close = useCallback(() => {
    stream.current?.close();
    stream.current = null;
    batcher.flush();
    dispatch({ kind: 'disconnected' });
  }, [batcher]);

  const cancel = useCallback(async () => {
    if (!state.runId) return;
    await transport.cancel(state.runId);
  }, [state.runId, transport]);

  const disconnected = useCallback(() => {
    batcher.flush();
    dispatch({ kind: 'disconnected' });
  }, [batcher]);

  useEffect(() => () => {
    stream.current?.close();
    batcher.close();
  }, [batcher]);

  useEffect(() => {
    let active = true;
    void transport.getCapabilities().then((value) => {
      if (active) dispatch({ kind: 'capabilities', value });
    }).catch(() => undefined);
    return () => { active = false; };
  }, [transport]);

  return { state, start, resume, close, cancel, disconnected };
}
