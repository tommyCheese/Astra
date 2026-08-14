import { useEffect, useMemo, useState, type FormEvent } from 'react';
import type { ResumeEntry, RunAgentInput } from '@ag-ui/core';
import { createConversation, listConversations } from '../api';
import type { ConversationSummary } from '../types';
import { ActivityView, InterruptView, type InterruptResolution } from './components';
import { AgUiHttpTransport, type AstraAgentTransport } from './transport';
import { useAgUiConversation } from './useAgUiConversation';

function publicId(prefix: string): string {
  return `${prefix}-${typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`}`;
}

function runInput(
  threadId: string,
  runId: string,
  content: string,
  state: Record<string, unknown>,
  parentRunId?: string | null,
): RunAgentInput {
  return {
    threadId,
    runId,
    ...(parentRunId ? { parentRunId } : {}),
    state,
    messages: content ? [{ id: publicId('user'), role: 'user', content }] : [],
    tools: [],
    context: [],
    forwardedProps: {
      astra: {
        profileVersion: 'astra-ag-ui-v1',
        answerMode: 'standard',
        planExecution: 'auto',
        subagentMode: 'auto',
      },
    },
  };
}

export function AgUiChatSurface({
  thread,
  transport,
}: {
  thread: ConversationSummary;
  transport: AstraAgentTransport;
}) {
  const { state, start, resume, close, cancel } = useAgUiConversation(transport);
  const [draft, setDraft] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<{ id: string; role: string; content: string }>>([]);
  const busy = state.connection === 'streaming';

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || busy || state.pendingInterrupts.length) return;
    setActionError(null);
    setDraft('');
    try {
      setHistory((current) => [
        ...current,
        ...state.messageOrder.map((id) => state.messages[id]).filter((message) => message.content),
        { id: publicId('user'), role: 'user', content },
      ]);
      start(runInput(thread.id, publicId('run'), content, state.sharedState));
    } catch (error) {
      setDraft(content);
      setActionError(error instanceof Error ? error.message : String(error));
    }
  };

  const resolveInterrupt = (resolution: InterruptResolution) => {
    const response: ResumeEntry = resolution;
    setActionError(null);
    try {
      resume(
        runInput(thread.id, publicId('run'), '', state.sharedState, state.runId),
        [response],
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  };

  const cancelRun = () => {
    setActionError(null);
    void cancel().catch((error: unknown) => {
      setActionError(error instanceof Error ? error.message : String(error));
    });
  };

  return (
    <main className="ag-ui-chat" aria-label="Astra AG-UI 对话">
      <header className="ag-ui-chat__header">
        <div><strong>Astra</strong><span>{thread.title}</span></div>
        <div className="ag-ui-chat__status" role="status">
          {state.connection === 'streaming' ? '正在生成' : state.connection === 'reconnecting' ? '连接已断开，可重新发送' : 'AG-UI'}
        </div>
      </header>

      <section className="ag-ui-chat__timeline" aria-live="polite" aria-label="对话内容">
        {!state.messageOrder.length && !state.activityOrder.length && (
          <div className="ag-ui-chat__empty"><h1>有什么可以帮你？</h1><p>回答、计划、工具和 Subagent 状态会实时显示。</p></div>
        )}
        {state.reasoningOrder.map((id) => (
          <details className="ag-ui-reasoning" key={id} open={!state.reasoning[id].complete}>
            <summary>思考摘要</summary><p>{state.reasoning[id].content}</p>
          </details>
        ))}
        {[...history, ...state.messageOrder.map((id) => state.messages[id])].map((message) => (
          <article className={`ag-ui-message ag-ui-message--${message.role}`} key={message.id}>
            <span className="ag-ui-message__role">{message.role === 'user' ? '你' : 'Astra'}</span>
            <p>{message.content}</p>
          </article>
        ))}
        {state.activityOrder.map((id) => <ActivityView activity={state.activities[id]} key={id} />)}
        {state.toolOrder.map((id) => (
          <details className="ag-ui-tool" key={id}>
            <summary>{state.tools[id].name || '工具'} · {state.tools[id].complete ? '已完成' : '执行中'}</summary>
            {state.tools[id].result && <pre>{state.tools[id].result}</pre>}
          </details>
        ))}
        {state.pendingInterrupts.map((interrupt) => (
          <InterruptView interrupt={interrupt} key={interrupt.id} onResolve={resolveInterrupt} />
        ))}
        {(state.error || actionError) && <p className="ag-ui-chat__error" role="alert">{actionError ?? state.error?.message}</p>}
      </section>

      <form className="ag-ui-composer" aria-label="消息编辑器" onSubmit={submit}>
        <label htmlFor="ag-ui-message-input">发送消息</label>
        <textarea
          id="ag-ui-message-input"
          value={draft}
          disabled={Boolean(state.pendingInterrupts.length)}
          placeholder={state.pendingInterrupts.length ? '请先处理上方待响应事项' : '给 Astra 发送消息'}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className="ag-ui-composer__actions">
          {busy && <button type="button" onClick={cancelRun}>停止运行</button>}
          {busy && <button type="button" onClick={close}>仅断开连接</button>}
          <button type="submit" disabled={!draft.trim() || busy || Boolean(state.pendingInterrupts.length)}>发送</button>
        </div>
      </form>
    </main>
  );
}

export function AgUiChatPage() {
  const [thread, setThread] = useState<ConversationSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void listConversations(1)
      .then((threads) => threads[0] ?? createConversation('AG-UI 新对话'))
      .then((value) => { if (active) setThread(value); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { active = false; };
  }, []);

  const transport = useMemo(() => thread ? new AgUiHttpTransport(
    '/api/ag-ui',
    (runId) => `/api/ag-ui/runs/${encodeURIComponent(runId)}/cancel?threadId=${encodeURIComponent(thread.id)}`,
  ) : null, [thread]);

  if (error) return <main className="ag-ui-chat"><p role="alert">无法初始化 AG-UI 对话：{error}</p></main>;
  if (!thread || !transport) return <main className="ag-ui-chat"><p role="status">正在载入对话…</p></main>;
  return <AgUiChatSurface thread={thread} transport={transport} />;
}
