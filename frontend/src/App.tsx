import { FormEvent, useEffect, useMemo, useState } from 'react';
import { createRun, getRun } from './api';
import type { AgentTurnView, ChatMessage, RunEvent, RunView, ToolCallView } from './types';

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked']);

export function App() {
  const [goal, setGoal] = useState('帮我总结 Astra 当前 Web Agent 能验证哪些证据');
  const [run, setRun] = useState<RunView | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError('请输入任务目标');
      return;
    }
    setError(null);
    setLoading(true);
    setEvents([]);
    try {
      const created = await createRun(trimmedGoal);
      const current = await getRun(created.run_id);
      setRun(current);
      setGoal('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建 run 失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!run || terminalStatuses.has(run.status)) {
      return;
    }
    const source = new EventSource(`/api/runs/${run.id}/events`);
    const eventTypes = [
      'run.created',
      'run.status_changed',
      'step.created',
      'step.updated',
      'tool_call.started',
      'tool_call.completed',
      'artifact.created',
      'agent_turn.created',
      'agent_turn.updated',
      'memory.read',
      'memory.write',
      'memory.write_rejected',
      'reflection.created',
      'verification.created',
    ];
    for (const type of eventTypes) {
      source.addEventListener(type, (message) => {
        const event = JSON.parse((message as MessageEvent).data) as RunEvent;
        setEvents((items) => mergeEvents(items, [event]));
      });
    }
    const refresh = window.setInterval(async () => {
      const next = await getRun(run.id);
      setRun(next);
      setEvents((items) => mergeEvents(items, next.events));
      if (terminalStatuses.has(next.status)) {
        source.close();
        window.clearInterval(refresh);
      }
    }, 700);
    return () => {
      source.close();
      window.clearInterval(refresh);
    };
  }, [run?.id, run?.status]);

  const visibleEvents = useMemo(() => mergeEvents(events, run?.events ?? []), [events, run]);
  const messages = useMemo(() => buildConversation(run), [run]);

  return (
    <main className="shell chat-shell">
      <section className="chat-topbar">
        <div>
          <h1>Astra</h1>
          <p>Web Agent</p>
        </div>
        <span className={`status status-${run?.status ?? 'idle'}`}>{statusLabel(run?.status)}</span>
      </section>

      <section className="chat-surface">
        <div className="conversation">
          {!messages.length && (
            <div className="welcome">
              <h2>今天想研究什么？</h2>
              <p>我会使用 Web 搜索和自适应抓取，边行动边留下可审计证据。</p>
            </div>
          )}
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} run={run} />
          ))}
          {run && !terminalStatuses.has(run.status) && (
            <div className="bubble assistant">
              <span className="bubble-label">Astra</span>
              <p>{activeState(run)}</p>
            </div>
          )}
        </div>

        <form className="chat-composer" onSubmit={submit}>
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="输入任务 / 继续追问..."
          />
          <button type="submit" disabled={loading}>{loading ? '...' : '↑'}</button>
        </form>
        {error && <div className="notice error">{error}</div>}
      </section>

      {run && <AuditDrawer run={run} events={visibleEvents} />}
    </main>
  );
}

function MessageBubble({ message, run }: { message: ChatMessage; run: RunView | null }) {
  const role = message.role === 'user' ? 'user' : message.role === 'tool' ? 'tool' : 'assistant';
  const turnIndex = Number(message.metadata.turn_index ?? 0);
  const turn = run?.turns?.find((item) => item.turn_index === turnIndex);

  return (
    <article className={`bubble ${role}`}>
      <span className="bubble-label">{labelForRole(message.role)}</span>
      <p>{message.content}</p>
      {turn?.selected_tool && <ToolEvent turn={turn} toolCalls={run?.tool_calls ?? []} />}
      {turn?.reflection && (
        <div className="reflection-card">
          <strong>反思</strong>
          <span>{String(turn.reflection.summary ?? message.content)}</span>
        </div>
      )}
      {message.role === 'assistant' && run?.result && <FinalAnswer run={run} />}
    </article>
  );
}

function ToolEvent({ turn, toolCalls }: { turn: AgentTurnView; toolCalls: ToolCallView[] }) {
  const call = toolCalls.find((item) => item.id === turn.tool_call_id);
  const output = call?.output ?? {};
  const url = typeof output.url === 'string' ? output.url : undefined;
  const warnings = Array.isArray(output.warnings) ? output.warnings : [];

  return (
    <div className="tool-event">
      <div>
        <strong>{turn.selected_tool}</strong>
        <span>{call?.status ?? turn.status}{toolCallDetail(output)}</span>
      </div>
      {url && <a href={url} target="_blank" rel="noreferrer">{url}</a>}
      {warnings.map((warning, index) => (
        <p key={index}>{String(warning)}</p>
      ))}
    </div>
  );
}

function FinalAnswer({ run }: { run: RunView }) {
  const result = run.result;
  if (!result) {
    return null;
  }
  const report = run.verification_report ?? result.verification_report;
  return (
    <div className="answer-block">
      {result.findings.map((finding, index) => (
        <p key={index}>{finding.text}</p>
      ))}
      {result.sources.length ? (
        <div className="source-grid">
          {result.sources.map((source) => {
            const quality = result.source_quality?.find((item) => item.url === source.url);
            return (
              <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="source-card">
                <strong>{source.title || source.url}</strong>
                {quality && (
                  <span>{formatScore(quality.quality_score)} · {quality.extraction_strategy || 'unknown'}</span>
                )}
              </a>
            );
          })}
        </div>
      ) : null}
      {[...result.caveats, ...result.verification_notes, ...(report?.notes ?? [])].map((item, index) => (
        <p key={`note-${index}`} className="note">{item}</p>
      ))}
    </div>
  );
}

function AuditDrawer({ run, events }: { run: RunView; events: RunEvent[] }) {
  return (
    <details className="audit-drawer">
      <summary>审计详情</summary>
      <div className="audit-grid">
        <section>
          <h3>Turns</h3>
          {run.turns?.map((turn) => (
            <div className="audit-row" key={turn.id}>
              <strong>{turn.turn_index}. {turn.decision_type}</strong>
              <span>{turn.reasoning_summary}</span>
            </div>
          ))}
        </section>
        <section>
          <h3>Memory</h3>
          {run.memories?.length ? run.memories.map((memory) => (
            <div className="audit-row" key={memory.id}>
              <strong>{memory.scope}/{memory.kind} · {Math.round(memory.confidence * 100)}%</strong>
              <span>{memory.content}</span>
            </div>
          )) : <p className="empty">暂无 Memory 写入。</p>}
        </section>
        <section>
          <h3>Timeline</h3>
          {run.steps.map((step) => (
            <div className="audit-row" key={step.id}>
              <strong>{step.index}. {step.title}</strong>
              <span>{step.status}</span>
            </div>
          ))}
        </section>
        <section>
          <h3>Events</h3>
          {events.slice(-10).map((event) => (
            <div className="audit-row" key={event.id}>
              <strong>{event.type}</strong>
              <code>{JSON.stringify(event.payload)}</code>
            </div>
          ))}
        </section>
      </div>
    </details>
  );
}

function buildConversation(run: RunView | null): ChatMessage[] {
  if (!run) {
    return [];
  }
  if (run.chat_messages?.length) {
    return run.chat_messages;
  }
  const messages: ChatMessage[] = [
    {
      id: `${run.id}-user`,
      role: 'user',
      content: run.summary || '提交了一个任务',
      status: 'completed',
      metadata: {},
    },
  ];
  for (const call of run.tool_calls) {
    messages.push({
      id: call.id,
      role: 'tool',
      content: call.tool_name,
      status: call.status,
      metadata: { selected_tool: call.tool_name, output: call.output },
    });
  }
  if (run.result) {
    messages.push({
      id: `${run.id}-answer`,
      role: 'assistant',
      content: run.result.summary,
      status: run.status,
      metadata: {},
    });
  }
  return messages;
}

function activeState(run: RunView) {
  const latest = [...(run.turns ?? [])].sort((a, b) => b.turn_index - a.turn_index)[0];
  if (latest?.selected_tool === 'web_search') return '正在搜索候选来源...';
  if (latest?.selected_tool === 'web_fetch') return '正在阅读和验证来源...';
  if (latest?.decision_type === 'reflect') return '正在反思并调整策略...';
  if (run.status === 'verifying') return '正在验证证据...';
  return '正在处理...';
}

function statusLabel(status?: string) {
  return status ?? 'idle';
}

function labelForRole(role: string) {
  if (role === 'user') return '你';
  if (role === 'tool') return '工具';
  if (role === 'reflection') return '反思';
  return 'Astra';
}

function formatScore(score?: number | null) {
  if (typeof score !== 'number') {
    return 'n/a';
  }
  return `${Math.round(score * 100)}%`;
}

function toolCallDetail(output?: Record<string, unknown> | null) {
  if (!output) {
    return '';
  }
  if (typeof output.candidate_count === 'number') {
    return ` · ${output.candidate_count} candidates`;
  }
  if (typeof output.quality_score === 'number' || typeof output.extraction_strategy === 'string') {
    const strategy = typeof output.extraction_strategy === 'string' ? output.extraction_strategy : 'read';
    const score = typeof output.quality_score === 'number' ? ` · ${formatScore(output.quality_score)}` : '';
    return ` · ${strategy}${score}`;
  }
  return '';
}

function mergeEvents(left: RunEvent[], right: RunEvent[]) {
  const map = new Map<number, RunEvent>();
  for (const event of [...left, ...right]) {
    map.set(event.id, event);
  }
  return [...map.values()].sort((a, b) => a.id - b.id);
}
