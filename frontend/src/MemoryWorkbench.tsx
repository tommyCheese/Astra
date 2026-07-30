import { useEffect, useMemo, useState, type ReactNode } from 'react';

import {
  getConsolidationJob,
  getEvolutionCandidate,
  getMemory,
  listConsolidationJobs,
  listEvolutionCandidates,
  listMemories,
  publishConsolidationJob,
  revokeMemory,
  rollbackConsolidationJob,
} from './deepMemoryApi';
import type {
  ConsolidationActionRequest,
  ConsolidationJob,
  ConsolidationJobListResult,
  EvolutionCandidate,
  EvolutionCandidateListResult,
  JsonObject,
  MemoryDetail,
  MemoryListQuery,
  MemoryListResult,
  MemoryRecord,
  MemoryRevocationRequest,
  RecallScoreComponents,
} from './deepMemoryTypes';
import { useI18n } from './i18n';

type WorkbenchTab = 'memories' | 'consolidation' | 'evolution';
type ConfirmAction =
  | { kind: 'revoke'; target: MemoryDetail }
  | { kind: 'publish'; target: ConsolidationJob }
  | { kind: 'rollback'; target: ConsolidationJob };

export type DeepMemoryClient = {
  listMemories: (query?: MemoryListQuery, signal?: AbortSignal) => Promise<MemoryListResult>;
  getMemory: (memoryId: string, signal?: AbortSignal) => Promise<MemoryDetail>;
  revokeMemory: (memoryId: string, request: MemoryRevocationRequest) => Promise<MemoryDetail>;
  listConsolidationJobs: (signal?: AbortSignal) => Promise<ConsolidationJobListResult>;
  getConsolidationJob: (jobId: string, signal?: AbortSignal) => Promise<ConsolidationJob>;
  publishConsolidationJob: (jobId: string, request: ConsolidationActionRequest) => Promise<ConsolidationJob>;
  rollbackConsolidationJob: (jobId: string, request: ConsolidationActionRequest) => Promise<ConsolidationJob>;
  listEvolutionCandidates: (signal?: AbortSignal) => Promise<EvolutionCandidateListResult>;
  getEvolutionCandidate: (candidateId: string, signal?: AbortSignal) => Promise<EvolutionCandidate>;
};

const defaultClient: DeepMemoryClient = {
  listMemories,
  getMemory,
  revokeMemory,
  listConsolidationJobs,
  getConsolidationJob,
  publishConsolidationJob,
  rollbackConsolidationJob,
  listEvolutionCandidates,
  getEvolutionCandidate,
};

const lifecycleLabels: Record<string, string> = {
  candidate: '候选',
  active: '生效中',
  superseded: '已被替代',
  revoked: '已撤销',
  expired: '已过期',
  quarantined: '隔离中',
};

const kindLabels: Record<string, string> = {
  semantic_fact: '语义事实',
  user_preference: '用户偏好',
  episodic_experience: '经历',
  procedure: '流程',
  failure_pattern: '失败模式',
  evaluation_feedback: '评估反馈',
};

const candidateStatusLabels: Record<string, string> = {
  draft: '草稿',
  evaluating: '评估中',
  rejected: '已拒绝',
  approved: '已批准',
  shadow: '影子',
  canary: '灰度',
  promoted: '已晋升',
  rolled_back: '已回滚',
};

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。';
}

function shortId(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length > 18 ? `${value.slice(0, 9)}…${value.slice(-6)}` : value;
}

function safeDate(value: string | null | undefined, language: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language);
}

function boundedJson(value: JsonObject): string {
  try {
    const text = JSON.stringify(value, null, 2);
    return text.length > 6000 ? `${text.slice(0, 6000)}\n…` : text;
  } catch {
    return '{}';
  }
}

function summaryAsDetail(memory: MemoryRecord): MemoryDetail {
  return { ...memory, sources: [], recall_events: [], audit_events: [], history: [] };
}

function StatusBadge({ status, labels = lifecycleLabels }: { status: string; labels?: Record<string, string> }) {
  const { t } = useI18n();
  return <span className={`memory-status status-${status}`}>{t(labels[status] ?? status)}</span>;
}

function MetricBar({ label, value }: { label: string; value: number }) {
  const normalized = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  return <div className="memory-score-row">
    <span>{label}</span>
    <div aria-hidden="true"><i style={{ width: `${normalized * 100}%` }} /></div>
    <strong>{value.toFixed(3)}</strong>
  </div>;
}

function ScoreBreakdown({ scores }: { scores: RecallScoreComponents }) {
  const { t } = useI18n();
  const components = [
    ['lexical', '词法'],
    ['tags', '标签'],
    ['kind', '类型'],
    ['recency', '时效'],
    ['confidence', '置信度'],
    ['importance', '重要性'],
    ['utility', '效用'],
    ['semantic', '语义'],
  ] as const;
  return <div className="memory-score-breakdown" aria-label={t('召回评分')}>
    <MetricBar label={t('总分')} value={scores.total} />
    {components.map(([key, label]) => scores[key] === undefined
      ? null
      : <MetricBar key={key} label={t(label)} value={scores[key] as number} />)}
  </div>;
}

export function MemoryWorkbench({ client = defaultClient }: { client?: DeepMemoryClient }) {
  const { language, t } = useI18n();
  const [tab, setTab] = useState<WorkbenchTab>('memories');
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [selectedMemory, setSelectedMemory] = useState<MemoryDetail | null>(null);
  const [jobs, setJobs] = useState<ConsolidationJob[]>([]);
  const [selectedJob, setSelectedJob] = useState<ConsolidationJob | null>(null);
  const [candidates, setCandidates] = useState<EvolutionCandidate[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState<EvolutionCandidate | null>(null);
  const [loaded, setLoaded] = useState<Record<WorkbenchTab, boolean>>({
    memories: false,
    consolidation: false,
    evolution: false,
  });
  const [loading, setLoading] = useState<Record<WorkbenchTab, boolean>>({
    memories: false,
    consolidation: false,
    evolution: false,
  });
  const [errors, setErrors] = useState<Partial<Record<WorkbenchTab, string>>>({});
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [kindFilter, setKindFilter] = useState('');
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [reason, setReason] = useState('');
  const [actionPending, setActionPending] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (loaded[tab]) return;
    const controller = new AbortController();
    setLoading((current) => ({ ...current, [tab]: true }));
    setErrors((current) => ({ ...current, [tab]: undefined }));

    const load = async () => {
      try {
        if (tab === 'memories') {
          const result = await client.listMemories({ include_history: true, limit: 200 }, controller.signal);
          if (controller.signal.aborted) return;
          setMemories(result.items);
          if (result.items[0]) {
            const fallback = summaryAsDetail(result.items[0]);
            setSelectedMemory(fallback);
            try {
              setSelectedMemory(await client.getMemory(result.items[0].id, controller.signal));
            } catch (error) {
              if (!isAbort(error)) setErrors((current) => ({ ...current, memories: errorMessage(error) }));
            }
          }
        } else if (tab === 'consolidation') {
          const result = await client.listConsolidationJobs(controller.signal);
          if (controller.signal.aborted) return;
          setJobs(result.items);
          if (result.items[0]) {
            setSelectedJob(result.items[0]);
            try {
              setSelectedJob(await client.getConsolidationJob(result.items[0].id, controller.signal));
            } catch (error) {
              if (!isAbort(error)) setErrors((current) => ({ ...current, consolidation: errorMessage(error) }));
            }
          }
        } else {
          const result = await client.listEvolutionCandidates(controller.signal);
          if (controller.signal.aborted) return;
          setCandidates(result.items);
          if (result.items[0]) {
            setSelectedCandidate(result.items[0]);
            try {
              setSelectedCandidate(await client.getEvolutionCandidate(result.items[0].id, controller.signal));
            } catch (error) {
              if (!isAbort(error)) setErrors((current) => ({ ...current, evolution: errorMessage(error) }));
            }
          }
        }
        if (!controller.signal.aborted) setLoaded((current) => ({ ...current, [tab]: true }));
      } catch (error) {
        if (!isAbort(error)) setErrors((current) => ({ ...current, [tab]: errorMessage(error) }));
      } finally {
        if (!controller.signal.aborted) setLoading((current) => ({ ...current, [tab]: false }));
      }
    };

    void load();
    return () => controller.abort();
  }, [client, loaded, tab]);

  const visibleMemories = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return memories.filter((memory) => {
      const queryMatch = !normalizedQuery || [
        memory.content,
        memory.memory_key,
        memory.namespace_id,
        memory.kind,
      ].some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
      return queryMatch
        && (!statusFilter || memory.status === statusFilter)
        && (!kindFilter || memory.kind === kindFilter);
    });
  }, [kindFilter, memories, query, statusFilter]);

  async function inspectMemory(memory: MemoryRecord) {
    setSelectedMemory(summaryAsDetail(memory));
    setErrors((current) => ({ ...current, memories: undefined }));
    try {
      setSelectedMemory(await client.getMemory(memory.id));
    } catch (error) {
      setErrors((current) => ({ ...current, memories: errorMessage(error) }));
    }
  }

  async function inspectJob(job: ConsolidationJob) {
    setSelectedJob(job);
    setErrors((current) => ({ ...current, consolidation: undefined }));
    try {
      setSelectedJob(await client.getConsolidationJob(job.id));
    } catch (error) {
      setErrors((current) => ({ ...current, consolidation: errorMessage(error) }));
    }
  }

  async function inspectCandidate(candidate: EvolutionCandidate) {
    setSelectedCandidate(candidate);
    setErrors((current) => ({ ...current, evolution: undefined }));
    try {
      setSelectedCandidate(await client.getEvolutionCandidate(candidate.id));
    } catch (error) {
      setErrors((current) => ({ ...current, evolution: errorMessage(error) }));
    }
  }

  function openAction(action: ConfirmAction) {
    setReason('');
    setNotice('');
    setConfirmAction(action);
  }

  async function submitAction() {
    if (!confirmAction || reason.trim().length < 3) return;
    setActionPending(true);
    setNotice('');
    try {
      if (confirmAction.kind === 'revoke') {
        const updated = await client.revokeMemory(confirmAction.target.id, {
          expected_state_version: confirmAction.target.state_version,
          reason: reason.trim(),
          actor: 'local-operator',
        });
        setSelectedMemory(updated);
        setMemories((items) => items.map((item) => item.id === updated.id ? updated : item));
        setNotice(t('记忆已撤销；历史召回记录仍保留用于审计。'));
      } else {
        const request = {
          expected_state_version: confirmAction.target.state_version,
          reason: reason.trim(),
          actor: 'local-operator',
        };
        const updated = confirmAction.kind === 'publish'
          ? await client.publishConsolidationJob(confirmAction.target.id, request)
          : await client.rollbackConsolidationJob(confirmAction.target.id, request);
        setSelectedJob(updated);
        setJobs((items) => items.map((item) => item.id === updated.id ? updated : item));
        setNotice(confirmAction.kind === 'publish'
          ? t('代次已原子发布。')
          : t('代次已回滚；输入与提案清单保持可审计。'));
      }
      setConfirmAction(null);
      setReason('');
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setActionPending(false);
    }
  }

  return <section className="memory-workbench" aria-labelledby="memory-workbench-title">
    <header className="memory-workbench-header">
      <div>
        <span>Deep Memory</span>
        <h2 id="memory-workbench-title">{t('记忆与自进化')}</h2>
        <p>{t('检查跨对话记忆、AutoDream 代次与受治理候选；这些记录不授予任何工具或权限。')}</p>
      </div>
      <span className="memory-governance-lock">{t('生产晋升关闭')}</span>
    </header>

    <div className="memory-tabs" role="tablist" aria-label={t('深度记忆视图')}>
      {([
        ['memories', '记忆审计'],
        ['consolidation', 'AutoDream'],
        ['evolution', '自进化候选'],
      ] as const).map(([id, label]) => <button
        key={id}
        type="button"
        role="tab"
        aria-selected={tab === id}
        className={tab === id ? 'active' : ''}
        onClick={() => setTab(id)}
      >{t(label)}</button>)}
    </div>

    {notice && <p className="memory-notice" role="status">{notice}</p>}
    {errors[tab] && <p className="memory-error" role="alert">{errors[tab]}</p>}

    {tab === 'memories' && <div className="memory-panel">
      <div className="memory-list-pane">
        <div className="memory-filters">
          <label>
            <span className="sr-only">{t('搜索记忆')}</span>
            <input value={query} onChange={(event) => setQuery(event.currentTarget.value)} placeholder={t('搜索内容、键或命名空间')} />
          </label>
          <select aria-label={t('生命周期筛选')} value={statusFilter} onChange={(event) => setStatusFilter(event.currentTarget.value)}>
            <option value="">{t('全部生命周期')}</option>
            {Object.entries(lifecycleLabels).map(([value, label]) => <option value={value} key={value}>{t(label)}</option>)}
          </select>
          <select aria-label={t('记忆类型筛选')} value={kindFilter} onChange={(event) => setKindFilter(event.currentTarget.value)}>
            <option value="">{t('全部类型')}</option>
            {Object.entries(kindLabels).map(([value, label]) => <option value={value} key={value}>{t(label)}</option>)}
          </select>
        </div>
        <div className="memory-list-summary">{t('共 {count} 条').replace('{count}', String(visibleMemories.length))}</div>
        {loading.memories ? <div className="memory-empty">{t('正在读取记忆…')}</div> : visibleMemories.length ? <div className="memory-record-list">
          {visibleMemories.map((memory) => <button
            type="button"
            className={selectedMemory?.id === memory.id ? 'active' : ''}
            aria-pressed={selectedMemory?.id === memory.id}
            key={memory.id}
            onClick={() => void inspectMemory(memory)}
          >
            <span><StatusBadge status={memory.status} /><small>v{memory.version}</small></span>
            <strong>{memory.content || t('无内容摘要')}</strong>
            <small>{t(kindLabels[memory.kind] ?? memory.kind)} · {memory.namespace_type}:{shortId(memory.namespace_id)}</small>
          </button>)}
        </div> : <div className="memory-empty">{t('没有匹配的记忆')}</div>}
      </div>

      <div className="memory-detail-pane">
        {selectedMemory ? <MemoryInspector
          memory={selectedMemory}
          language={language}
          onRevoke={() => openAction({ kind: 'revoke', target: selectedMemory })}
        /> : <div className="memory-empty">{t('选择一条记忆查看审计详情')}</div>}
      </div>
    </div>}

    {tab === 'consolidation' && <div className="memory-panel">
      <div className="memory-list-pane">
        <div className="memory-list-heading"><strong>{t('合并代次')}</strong><span>{jobs.length}</span></div>
        {loading.consolidation ? <div className="memory-empty">{t('正在读取 AutoDream 作业…')}</div> : jobs.length ? <div className="memory-record-list">
          {jobs.map((job) => <button
            type="button"
            className={selectedJob?.id === job.id ? 'active' : ''}
            aria-pressed={selectedJob?.id === job.id}
            key={job.id}
            onClick={() => void inspectJob(job)}
          >
            <span><StatusBadge status={job.status} labels={{ proposed: '待复核', published: '已发布', rolled_back: '已回滚', validation_failed: '校验失败', queued: '排队中', running: '运行中', failed: '失败', interrupted: '已中断' }} /><small>G{job.generation}</small></span>
            <strong>{job.namespace_type}:{shortId(job.namespace_id)}</strong>
            <small>{safeDate(job.created_at, language)}</small>
          </button>)}
        </div> : <div className="memory-empty">{t('暂无合并作业')}</div>}
      </div>
      <div className="memory-detail-pane">
        {selectedJob ? <ConsolidationInspector
          job={selectedJob}
          language={language}
          onPublish={() => openAction({ kind: 'publish', target: selectedJob })}
          onRollback={() => openAction({ kind: 'rollback', target: selectedJob })}
        /> : <div className="memory-empty">{t('选择一个作业复核冻结输入与提案')}</div>}
      </div>
    </div>}

    {tab === 'evolution' && <div className="memory-panel">
      <div className="memory-list-pane">
        <div className="memory-list-heading"><strong>{t('候选版本')}</strong><span>{candidates.length}</span></div>
        {loading.evolution ? <div className="memory-empty">{t('正在读取自进化候选…')}</div> : candidates.length ? <div className="memory-record-list">
          {candidates.map((candidate) => <button
            type="button"
            className={selectedCandidate?.id === candidate.id ? 'active' : ''}
            aria-pressed={selectedCandidate?.id === candidate.id}
            key={candidate.id}
            onClick={() => void inspectCandidate(candidate)}
          >
            <span><StatusBadge status={candidate.status} labels={candidateStatusLabels} /><small>r{candidate.revision}</small></span>
            <strong>{candidate.candidate_key || t('未命名候选')}</strong>
            <small>{candidate.candidate_type} · {candidate.target_component}</small>
          </button>)}
        </div> : <div className="memory-empty">{t('暂无自进化候选')}</div>}
      </div>
      <div className="memory-detail-pane">
        {selectedCandidate
          ? <EvolutionInspector candidate={selectedCandidate} language={language} />
          : <div className="memory-empty">{t('选择候选查看证据与离线评估')}</div>}
      </div>
    </div>}

    {confirmAction && <ActionDialog
      action={confirmAction}
      reason={reason}
      pending={actionPending}
      onReasonChange={setReason}
      onCancel={() => setConfirmAction(null)}
      onConfirm={() => void submitAction()}
    />}
  </section>;
}

function MemoryInspector({ memory, language, onRevoke }: {
  memory: MemoryDetail;
  language: string;
  onRevoke: () => void;
}) {
  const { t } = useI18n();
  const canRevoke = ['candidate', 'active', 'quarantined'].includes(memory.status);
  return <article className="memory-inspector" aria-label={t('记忆详情')}>
    <header>
      <div><StatusBadge status={memory.status} /><span>{t(kindLabels[memory.kind] ?? memory.kind)}</span></div>
      <button className="memory-danger-button" type="button" disabled={!canRevoke} onClick={onRevoke}>{t('撤销记忆')}</button>
    </header>
    <p className="memory-content" data-testid="memory-safe-content">{memory.content || t('无内容摘要')}</p>
    <dl className="memory-metadata">
      <div><dt>{t('命名空间')}</dt><dd>{memory.namespace_type}:{memory.namespace_id || '—'}</dd></div>
      <div><dt>{t('稳定键')}</dt><dd>{memory.memory_key || '—'}</dd></div>
      <div><dt>{t('版本')}</dt><dd>v{memory.version} · state {memory.state_version}</dd></div>
      <div><dt>{t('置信度')}</dt><dd>{memory.confidence.toFixed(3)}</dd></div>
      <div><dt>{t('重要性')}</dt><dd>{memory.importance.toFixed(3)}</dd></div>
      <div><dt>{t('效用')}</dt><dd>{memory.utility_score.toFixed(3)}</dd></div>
      <div><dt>{t('有效时间')}</dt><dd>{safeDate(memory.valid_from, language)} → {safeDate(memory.valid_to, language)}</dd></div>
      <div><dt>{t('到期时间')}</dt><dd>{safeDate(memory.expires_at, language)}</dd></div>
      <div><dt>{t('替代关系')}</dt><dd>{memory.supersedes_id ? `← ${memory.supersedes_id}` : '—'}</dd></div>
    </dl>

    <InspectorSection title={t('来源证据')} count={memory.sources.length}>
      {memory.sources.length ? <div className="memory-source-list">
        {memory.sources.map((source) => <div key={source.id}>
          <span className={source.accessible ? 'source-ok' : 'source-blocked'}>{source.accessible ? t('可访问') : t('已失效')}</span>
          <strong>{source.source_kind}</strong>
          <code>{source.source_ref || '—'}</code>
        </div>)}
      </div> : <p className="memory-section-empty">{t('详情响应未包含来源记录')}</p>}
    </InspectorSection>

    <InspectorSection title={t('召回审计')} count={memory.recall_events.length}>
      {memory.recall_events.length ? memory.recall_events.map((event) => <div className="memory-recall-card" key={event.event_id}>
        <header>
          <div><strong>{event.shadow ? t('影子召回') : event.selected ? t('已注入上下文') : t('未选中')}</strong><small>{safeDate(event.created_at, language)}</small></div>
          <code>{shortId(event.query_fingerprint)}</code>
        </header>
        <ScoreBreakdown scores={event.scores} />
        {event.exclusion_reason && <p>{t('排除原因')}：{event.exclusion_reason}</p>}
      </div>) : <p className="memory-section-empty">{t('尚无召回评分')}</p>}
    </InspectorSection>

    <InspectorSection title={t('生命周期审计')} count={memory.audit_events.length}>
      {memory.audit_events.length ? <ol className="memory-audit-list">
        {memory.audit_events.map((event) => <li key={event.id}>
          <span>{event.event_type}</span>
          <strong>{event.reason || t('未提供原因')}</strong>
          <time>{safeDate(event.created_at, language)}</time>
        </li>)}
      </ol> : <p className="memory-section-empty">{t('尚无生命周期事件')}</p>}
    </InspectorSection>

    <InspectorSection title={t('历史版本')} count={memory.history.length}>
      {memory.history.length ? <div className="memory-version-list">
        {memory.history.map((version) => <div key={version.id}><StatusBadge status={version.status} /><strong>v{version.version}</strong><span>{version.content}</span></div>)}
      </div> : <p className="memory-section-empty">{t('当前记录没有更早版本')}</p>}
    </InspectorSection>

    <details className="memory-json-details">
      <summary>{t('结构化数据与溯源元数据')}</summary>
      <div><strong>structured_data</strong><pre>{boundedJson(memory.structured_data)}</pre></div>
      <div><strong>provenance</strong><pre>{boundedJson(memory.provenance)}</pre></div>
    </details>
  </article>;
}

function ConsolidationInspector({ job, language, onPublish, onRollback }: {
  job: ConsolidationJob;
  language: string;
  onPublish: () => void;
  onRollback: () => void;
}) {
  const { t } = useI18n();
  const canPublish = job.status === 'proposed' && job.validation.passed;
  const canRollback = job.status === 'published';
  return <article className="memory-inspector consolidation-inspector" aria-label={t('AutoDream 作业详情')}>
    <header>
      <div><StatusBadge status={job.status} labels={{ proposed: '待复核', published: '已发布', rolled_back: '已回滚', validation_failed: '校验失败', queued: '排队中', running: '运行中', failed: '失败', interrupted: '已中断' }} /><span>Generation {job.generation}</span></div>
      <div className="memory-detail-actions">
        <button type="button" disabled={!canPublish} onClick={onPublish}>{t('发布已验证代次')}</button>
        <button className="memory-danger-button" type="button" disabled={!canRollback} onClick={onRollback}>{t('回滚此代次')}</button>
      </div>
    </header>
    <dl className="memory-metadata">
      <div><dt>{t('命名空间')}</dt><dd>{job.namespace_type}:{job.namespace_id}</dd></div>
      <div><dt>{t('输入哈希')}</dt><dd>{job.input_hash || '—'}</dd></div>
      <div><dt>{t('状态版本')}</dt><dd>{job.state_version}</dd></div>
      <div><dt>{t('创建时间')}</dt><dd>{safeDate(job.created_at, language)}</dd></div>
      <div><dt>{t('发布时间')}</dt><dd>{safeDate(job.published_at, language)}</dd></div>
      <div><dt>{t('回滚来源')}</dt><dd>{job.rollback_of_id || '—'}</dd></div>
    </dl>

    <InspectorSection title={t('冻结输入清单')} count={Object.keys(job.input_manifest).length}>
      <pre className="memory-json-block">{boundedJson(job.input_manifest)}</pre>
    </InspectorSection>

    <InspectorSection title={t('提案操作')} count={job.proposal_operations.length}>
      {job.proposal_operations.length ? <div className="consolidation-operation-list">
        {job.proposal_operations.map((operation, index) => <div key={`${operation.operation}-${index}`}>
          <strong>{operation.operation}</strong>
          <span>{operation.memory_key || operation.memory_id || t('新记录')}</span>
          {operation.content && <p>{operation.content}</p>}
          <small>{t('{count} 个来源').replace('{count}', String(operation.source_memory_ids.length))}</small>
        </div>)}
      </div> : <p className="memory-section-empty">{t('提案未包含可发布操作')}</p>}
    </InspectorSection>

    <InspectorSection title={t('发布校验')} count={job.validation.issues.length}>
      <div className={`consolidation-validation ${job.validation.passed ? 'passed' : 'failed'}`}>
        <strong>{job.validation.passed ? t('校验通过') : t('校验未通过')}</strong>
        {job.validation.issues.map((issue) => <p key={`${issue.code}-${issue.message}`}><code>{issue.code}</code>{issue.message}</p>)}
        {job.validation.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      </div>
    </InspectorSection>
  </article>;
}

function EvolutionInspector({ candidate, language }: { candidate: EvolutionCandidate; language: string }) {
  const { t } = useI18n();
  return <article className="memory-inspector evolution-inspector" aria-label={t('自进化候选详情')}>
    <header>
      <div><StatusBadge status={candidate.status} labels={candidateStatusLabels} /><span>{candidate.candidate_type}</span></div>
      <button
        className="promotion-disabled-button"
        type="button"
        disabled
        aria-describedby="promotion-disabled-reason"
      >{t('生产晋升（未开放）')}</button>
    </header>
    <p id="promotion-disabled-reason" className="memory-safety-note">{t('初始版本只允许离线评估与人工复核；候选不会修改 Skills、提示词、权限或运行策略。')}</p>
    <dl className="memory-metadata">
      <div><dt>{t('候选键')}</dt><dd>{candidate.candidate_key || '—'}</dd></div>
      <div><dt>{t('修订')}</dt><dd>r{candidate.revision} · state {candidate.state_version}</dd></div>
      <div><dt>{t('目标组件')}</dt><dd>{candidate.target_component || '—'}</dd></div>
      <div><dt>{t('命名空间')}</dt><dd>{candidate.namespace_type}:{candidate.namespace_id}</dd></div>
      <div><dt>{t('内容摘要')}</dt><dd>{shortId(candidate.content_digest)}</dd></div>
      <div><dt>{t('更新时间')}</dt><dd>{safeDate(candidate.updated_at, language)}</dd></div>
    </dl>

    <InspectorSection title={t('候选内容')} count={Object.keys(candidate.content).length}>
      <pre className="memory-json-block" data-testid="candidate-safe-content">{boundedJson(candidate.content)}</pre>
    </InspectorSection>
    <InspectorSection title={t('支撑来源')} count={candidate.sources.length}>
      {candidate.sources.length ? <div className="memory-source-list">
        {candidate.sources.map((source) => <div key={source.id}>
          <span className={source.accessible ? 'source-ok' : 'source-blocked'}>{source.accessible ? t('可访问') : t('已失效')}</span>
          <strong>{source.source_kind}</strong>
          <code>{source.source_ref}</code>
        </div>)}
      </div> : <p className="memory-section-empty">{t('详情响应未包含来源记录')}</p>}
    </InspectorSection>
    <InspectorSection title={t('离线评估')} count={candidate.evaluations.length}>
      {candidate.evaluations.length ? <div className="evolution-evaluation-list">
        {candidate.evaluations.map((evaluation) => <div key={evaluation.id}>
          <span className={`evaluation-verdict verdict-${evaluation.verdict}`}>{evaluation.verdict}</span>
          <strong>v{evaluation.version} · {evaluation.evaluator}</strong>
          <code>{shortId(evaluation.manifest_digest)}</code>
          <time>{safeDate(evaluation.created_at, language)}</time>
        </div>)}
      </div> : <p className="memory-section-empty">{t('尚未附加可比离线评估')}</p>}
    </InspectorSection>
  </article>;
}

function InspectorSection({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return <section className="memory-inspector-section">
    <header><h3>{title}</h3><span>{count}</span></header>
    {children}
  </section>;
}

function ActionDialog({ action, reason, pending, onReasonChange, onCancel, onConfirm }: {
  action: ConfirmAction;
  reason: string;
  pending: boolean;
  onReasonChange: (reason: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  const title = action.kind === 'revoke' ? t('撤销这条记忆？')
    : action.kind === 'publish' ? t('发布这个合并代次？')
      : t('回滚这个合并代次？');
  const actionLabel = action.kind === 'revoke' ? t('确认撤销')
    : action.kind === 'publish' ? t('确认发布')
      : t('确认回滚');
  return <div className="memory-dialog-backdrop">
    <section className="memory-action-dialog" role="alertdialog" aria-modal="true" aria-labelledby="memory-action-title">
      <h2 id="memory-action-title">{title}</h2>
      <p>{action.kind === 'revoke'
        ? t('撤销会立即排除后续召回，但不会删除历史版本或既有运行审计。')
        : t('操作使用当前状态版本进行冲突检查，并保留输入、提案与来源清单。')}</p>
      <label>
        <span>{t('操作原因')}</span>
        <textarea
          autoFocus
          rows={3}
          value={reason}
          onChange={(event) => onReasonChange(event.currentTarget.value)}
          placeholder={t('至少输入 3 个字符，原因会写入审计记录')}
        />
      </label>
      <div>
        <button type="button" disabled={pending} onClick={onCancel}>{t('取消')}</button>
        <button className={action.kind === 'publish' ? 'primary-button' : 'memory-danger-button'} type="button" disabled={pending || reason.trim().length < 3} onClick={onConfirm}>{pending ? t('处理中…') : actionLabel}</button>
      </div>
    </section>
  </div>;
}
