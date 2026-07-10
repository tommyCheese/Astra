import { FormEvent, useEffect, useMemo, useState } from 'react';
import { createRun, getRun } from './api';
import type { RunEvent, RunView } from './types';

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'blocked']);

export function App() {
  const [goal, setGoal] = useState('查询 Astra 第一条 Web 数据查询任务切片应该验证哪些证据');
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
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      setEvents((items) => mergeEvents(items, [event]));
    };
    const eventTypes = [
      'run.created',
      'run.status_changed',
      'step.created',
      'step.updated',
      'tool_call.started',
      'tool_call.completed',
      'artifact.created',
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
    }, 800);
    return () => {
      source.close();
      window.clearInterval(refresh);
    };
  }, [run?.id, run?.status]);

  const visibleEvents = useMemo(() => mergeEvents(events, run?.events ?? []), [events, run]);

  return (
    <main className="shell">
      <section className="topbar">
        <div>
          <h1>Astra</h1>
          <p>通用任务运行台</p>
        </div>
        <span className={`status status-${run?.status ?? 'idle'}`}>{run?.status ?? 'idle'}</span>
      </section>

      <form className="composer" onSubmit={submit}>
        <textarea value={goal} onChange={(event) => setGoal(event.target.value)} />
        <button type="submit" disabled={loading}>{loading ? '运行中' : 'Run'}</button>
      </form>
      {error && <div className="notice error">{error}</div>}

      <section className="workspace">
        <Timeline run={run} events={visibleEvents} />
        <ResultPanel run={run} />
      </section>
    </main>
  );
}

function Timeline({ run, events }: { run: RunView | null; events: RunEvent[] }) {
  return (
    <section className="panel">
      <h2>Timeline</h2>
      {!run && <p className="empty">提交一个目标后，运行步骤会出现在这里。</p>}
      {run?.steps.map((step) => (
        <article className="timeline-item" key={step.id}>
          <span className={`dot dot-${step.status}`} />
          <div>
            <strong>{step.index}. {step.title}</strong>
            <p>{step.intent}</p>
            {step.evidence && <code>{JSON.stringify(step.evidence)}</code>}
          </div>
        </article>
      ))}
      <div className="events">
        {events.slice(-12).map((event) => (
          <div key={event.id} className="event-row">
            <span>{event.type}</span>
            <code>{JSON.stringify(event.payload)}</code>
          </div>
        ))}
      </div>
    </section>
  );
}

function ResultPanel({ run }: { run: RunView | null }) {
  const result = run?.result;
  return (
    <section className="panel result">
      <h2>Result</h2>
      {!result && <p className="empty">最终答案、来源和验证备注会在 run 完成后显示。</p>}
      {result && (
        <>
          <h3>{result.summary}</h3>
          <div className="block">
            <h4>发现</h4>
            {result.findings.map((finding, index) => (
              <p key={index}>{finding.text}</p>
            ))}
          </div>
          <div className="block">
            <h4>来源</h4>
            {result.sources.map((source) => (
              <a key={source.url} href={source.url} target="_blank" rel="noreferrer">
                {source.title || source.url}
              </a>
            ))}
          </div>
          {result.source_quality?.length ? (
            <div className="block">
              <h4>来源质量</h4>
              {result.source_quality.map((source) => (
                <div key={source.url} className="quality-row">
                  <div>
                    <strong>{formatScore(source.quality_score)}</strong>
                    <span>{source.extraction_strategy || 'unknown'}</span>
                  </div>
                  <a href={source.url} target="_blank" rel="noreferrer">{source.url}</a>
                  {source.warnings?.map((warning, index) => (
                    <p key={index}>{warning}</p>
                  ))}
                </div>
              ))}
            </div>
          ) : null}
          {result.failed_sources?.length ? (
            <div className="block">
              <h4>失败来源</h4>
              {result.failed_sources.map((source, index) => (
                <p key={`${source.url ?? 'failed'}-${index}`}>
                  {source.title || source.url || '未知来源'}：{source.category || 'failed'}
                </p>
              ))}
            </div>
          ) : null}
          <div className="block">
            <h4>限制与验证</h4>
            {[...result.caveats, ...result.verification_notes].map((item, index) => (
              <p key={index}>{item}</p>
            ))}
          </div>
        </>
      )}
      {run?.tool_calls.length ? (
        <div className="block">
          <h4>工具调用</h4>
          {run.tool_calls.map((call) => (
            <div key={call.id} className="tool-call">
              <span>{call.tool_name}{toolCallDetail(call.output)}</span>
              <strong>{call.status}</strong>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
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
  if (typeof output.extraction_strategy === 'string') {
    const score = typeof output.quality_score === 'number' ? ` · ${formatScore(output.quality_score)}` : '';
    return ` · ${output.extraction_strategy}${score}`;
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
