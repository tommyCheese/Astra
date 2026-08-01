import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../src/i18n';
import { MemoryWorkbench, type DeepMemoryClient } from '../src/MemoryWorkbench';
import type {
  ConsolidationJob,
  EvolutionCandidate,
  MemoryDetail,
} from '../src/deepMemoryTypes';

const unsafeMemoryContent = '<img src=x onerror="globalThis.__memoryInjected=true"> durable preference';

const memory: MemoryDetail = {
  id: 'memory-1',
  run_id: 'run-1',
  memory_key: 'preference.editor.theme',
  namespace_type: 'user',
  namespace_id: 'user-local',
  scope: 'user',
  kind: 'user_preference',
  status: 'active',
  version: 2,
  state_version: 4,
  content: unsafeMemoryContent,
  structured_data: { theme: '<script>dark</script>' },
  provenance: { source: 'turn-1' },
  confidence: 0.94,
  importance: 0.82,
  utility_score: 0.31,
  access_count: 3,
  observed_at: '2026-07-29T08:00:00Z',
  valid_from: '2026-07-29T08:00:00Z',
  valid_to: null,
  supersedes_id: 'memory-0',
  consolidation_generation: 0,
  created_at: '2026-07-29T08:00:00Z',
  updated_at: '2026-07-29T09:00:00Z',
  expires_at: null,
  last_accessed_at: '2026-07-29T09:30:00Z',
  revoked_at: null,
  revoke_reason: null,
  sources: [{
    id: 'source-1',
    source_kind: 'turn',
    source_ref: 'turn-1',
    source_hash: 'sha256:source',
    run_id: 'run-1',
    turn_id: 'turn-1',
    tool_call_id: null,
    artifact_id: null,
    accessible: true,
    created_at: '2026-07-29T08:00:00Z',
    revoked_at: null,
    source_data: {},
  }],
  recall_events: [{
    event_id: 'recall-1',
    run_id: 'run-2',
    turn_id: 'turn-2',
    query_fingerprint: 'sha256:query',
    policy_version: 'memory-retrieval-v1',
    selected: true,
    exclusion_reason: null,
    scores: {
      total: 0.86,
      lexical: 0.72,
      confidence: 0.94,
      importance: 0.82,
      utility: 0.31,
    },
    feedback: { outcome: 'helpful' },
    created_at: '2026-07-29T09:30:00Z',
  }],
  audit_events: [{
    id: 'audit-1',
    event_type: 'memory.activated',
    actor: 'memory-validator',
    reason: 'validated provenance',
    payload: {},
    created_at: '2026-07-29T08:01:00Z',
  }],
  history: [{
    id: 'memory-0',
    run_id: 'run-0',
    memory_key: 'preference.editor.theme',
    namespace_type: 'user',
    namespace_id: 'user-local',
    scope: 'user',
    kind: 'user_preference',
    status: 'superseded',
    version: 1,
    state_version: 2,
    content: '<script>old preference</script>',
    structured_data: {},
    provenance: {},
    confidence: 0.8,
    importance: 0.7,
    utility_score: 0,
    access_count: 0,
    supersedes_id: null,
    consolidation_generation: 0,
  }],
};

const publishedJob: ConsolidationJob = {
  id: 'job-1',
  namespace_type: 'user',
  namespace_id: 'user-local',
  status: 'published',
  state_version: 6,
  generation: 3,
  idempotency_key: 'user-local:g3',
  input_hash: 'sha256:input',
  input_manifest: { memories: [{ id: 'memory-1', version: 2 }] },
  proposal: {
    operations: [{
      operation: 'replacement',
      memory_key: 'preference.editor.theme',
      content: '<img src=x onerror="globalThis.__jobInjected=true">',
      source_memory_ids: ['memory-1'],
    }],
  },
  proposal_operations: [{
    operation: 'replacement',
    memory_id: null,
    memory_key: 'preference.editor.theme',
    content: '<img src=x onerror="globalThis.__jobInjected=true">',
    source_memory_ids: ['memory-1'],
    details: {},
  }],
  validation: { passed: true, issues: [], warnings: [] },
  profile_snapshot: { version: 'profile-v2', documents: ['hash-only'] },
  model_usage: { calls: 1 },
  publish_result: { activated: ['memory-2'], superseded: ['memory-1'] },
  error: null,
  lease_owner: null,
  lease_expires_at: null,
  rollback_of_id: null,
  created_at: '2026-07-29T10:00:00Z',
  started_at: '2026-07-29T10:01:00Z',
  completed_at: '2026-07-29T10:02:00Z',
  published_at: '2026-07-29T10:03:00Z',
};

const evolutionCandidate: EvolutionCandidate = {
  id: 'candidate-1',
  candidate_key: 'procedure.research.safe-v1',
  revision: 1,
  supersedes_id: null,
  candidate_type: 'procedure',
  target_component: 'procedure',
  title: 'Safe research procedure',
  namespace_type: 'workspace',
  namespace_id: 'workspace-local',
  status: 'approved',
  state_version: 3,
  content: '<script>globalThis.__candidateInjected=true</script>Use only currently enabled tools.',
  content_digest: 'sha256:candidate',
  environment_constraints: [{ key: 'runtime.mode', value: 'local' }],
  required_tools: ['web_search'],
  parameter_changes: [],
  current_evaluation_id: 'evaluation-1',
  current_evaluation_verdict: 'passed',
  created_by: 'local-operator',
  reviewed_by: 'reviewer',
  review_reason: 'offline replay passed',
  sources: [{
    id: 'run:run-1',
    source_kind: 'run',
    source_ref: 'run-1',
    source_hash: 'sha256:run',
    run_id: 'run-1',
    memory_id: null,
    accessible: true,
    created_at: '2026-07-29T11:00:00Z',
    revoked_at: null,
  }],
  evaluations: [{
    id: 'evaluation-1',
    version: 1,
    manifest_digest: 'sha256:evaluation',
    evaluator: 'offline-replay',
    issuer: 'astra',
    verdict: 'passed',
    manifest: { held_out_cases: 8 },
    created_at: '2026-07-29T11:30:00Z',
  }],
  audit_events: [{
    id: 'audit-2',
    event_type: 'candidate.approved',
    actor: 'reviewer',
    reason: 'offline replay passed',
    expected_state_version: 2,
    actual_state_version: 3,
    payload: {},
    created_at: '2026-07-29T11:31:00Z',
  }],
  rollback_metadata: null,
  executable: false,
  production_promotion_enabled: false,
  created_at: '2026-07-29T11:00:00Z',
  updated_at: '2026-07-29T11:31:00Z',
};

function clientFixture(): DeepMemoryClient {
  return {
    listMemories: vi.fn(async () => ({ items: [memory], total: 1, next_cursor: null })),
    getMemory: vi.fn(async () => memory),
    revokeMemory: vi.fn(async () => ({
      ...memory,
      status: 'revoked',
      state_version: 5,
      revoked_at: '2026-07-29T12:00:00Z',
      revoke_reason: '用户要求撤销',
    })),
    listConsolidationJobs: vi.fn(async () => ({ items: [publishedJob], total: 1, next_cursor: null })),
    getConsolidationJob: vi.fn(async () => publishedJob),
    publishConsolidationJob: vi.fn(async () => publishedJob),
    rollbackConsolidationJob: vi.fn(async () => ({
      ...publishedJob,
      status: 'rolled_back',
      state_version: 7,
    })),
    listEvolutionCandidates: vi.fn(async () => ({
      items: [evolutionCandidate],
      total: 1,
      next_cursor: null,
      production_promotion_enabled: false as const,
    })),
    getEvolutionCandidate: vi.fn(async () => evolutionCandidate),
  };
}

function renderWorkbench(client: DeepMemoryClient) {
  return render(<I18nProvider><MemoryWorkbench client={client} /></I18nProvider>);
}

describe('MemoryWorkbench', () => {
  beforeEach(() => {
    const values = new Map<string, string>([['astra.language', 'zh-CN']]);
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('inspects lifecycle, provenance, recall scores, and historical versions as inert text', async () => {
    const client = clientFixture();
    const view = renderWorkbench(client);

    expect(await screen.findByLabelText('记忆详情')).toBeInTheDocument();
    expect(screen.getByTestId('memory-safe-content')).toHaveTextContent(unsafeMemoryContent);
    expect(screen.getByText('turn-1')).toBeInTheDocument();
    expect(screen.getByLabelText('召回评分')).toBeInTheDocument();
    expect(screen.getByText('validated provenance')).toBeInTheDocument();
    expect(screen.getByText('<script>old preference</script>')).toBeInTheDocument();
    expect(view.container.querySelector('img')).toBeNull();
    expect(view.container.querySelector('script')).toBeNull();
    expect(client.listMemories).toHaveBeenCalledWith(
      { include_history: true, limit: 200 },
      expect.any(AbortSignal),
    );
  });

  it('uses progressive disclosure for the stored Memory view', async () => {
    const client = clientFixture();
    render(<I18nProvider><MemoryWorkbench client={client} visibleTabs={['memories']} showHeader={false} /></I18nProvider>);

    expect(await screen.findByLabelText('记忆详情')).toBeInTheDocument();
    expect(screen.getByText('turn-1')).toBeInTheDocument();
    const auditDetails = screen.getByTestId('memory-audit-details');
    expect(auditDetails).not.toHaveAttribute('open');
    expect(client.listMemories).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('审计详情'));
    expect(auditDetails).toHaveAttribute('open');
    expect(screen.getByText('召回审计')).toBeInTheDocument();
    expect(screen.getByText('生命周期审计')).toBeInTheDocument();
    expect(screen.getByText('结构化数据与溯源元数据')).toBeInTheDocument();
    expect(client.listMemories).toHaveBeenCalledTimes(1);
  });

  it('revokes with the visible state version and keeps the audit history', async () => {
    const client = clientFixture();
    renderWorkbench(client);
    await screen.findByLabelText('记忆详情');

    fireEvent.click(screen.getByRole('button', { name: '撤销记忆' }));
    const dialog = screen.getByRole('alertdialog', { name: '撤销这条记忆？' });
    fireEvent.change(
      screen.getByPlaceholderText('至少输入 3 个字符，原因会写入审计记录'),
      { target: { value: '用户要求撤销' } },
    );
    fireEvent.click(screen.getByRole('button', { name: '确认撤销' }));

    await waitFor(() => expect(client.revokeMemory).toHaveBeenCalledWith('memory-1', {
      expected_state_version: 4,
      reason: '用户要求撤销',
      actor: 'local-operator',
    }));
    expect(dialog).not.toBeInTheDocument();
    expect(await screen.findByText('记忆已撤销；历史召回记录仍保留用于审计。')).toBeInTheDocument();
    expect(screen.getAllByText('已撤销').length).toBeGreaterThan(0);
    expect(screen.getByText('validated provenance')).toBeInTheDocument();
  });

  it('reviews a published consolidation generation and performs audited rollback', async () => {
    const client = clientFixture();
    const view = renderWorkbench(client);
    fireEvent.click(screen.getByRole('tab', { name: '整理与合并' }));

    expect(await screen.findByLabelText('AutoDream 作业详情')).toBeInTheDocument();
    expect(screen.getByText('preference.editor.theme')).toBeInTheDocument();
    expect(screen.getByText('校验通过')).toBeInTheDocument();
    expect(screen.getByText('<img src=x onerror="globalThis.__jobInjected=true">')).toBeInTheDocument();
    expect(view.container.querySelector('img')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '回滚此代次' }));
    fireEvent.change(
      screen.getByPlaceholderText('至少输入 3 个字符，原因会写入审计记录'),
      { target: { value: '离线指标回归' } },
    );
    fireEvent.click(screen.getByRole('button', { name: '确认回滚' }));

    await waitFor(() => expect(client.rollbackConsolidationJob).toHaveBeenCalledWith('job-1', {
      expected_state_version: 6,
      reason: '离线指标回归',
      actor: 'local-operator',
    }));
    expect(await screen.findByText('代次已回滚；输入与提案清单保持可审计。')).toBeInTheDocument();
    expect(client.listConsolidationJobs).toHaveBeenCalledWith(
      { limit: 100 },
      expect.any(AbortSignal),
    );
  });

  it('renders evolution evidence safely and keeps production promotion visibly disabled', async () => {
    const client = clientFixture();
    const view = renderWorkbench(client);
    fireEvent.click(screen.getByRole('tab', { name: 'Agent 改进' }));

    expect(await screen.findByLabelText('自进化候选详情')).toBeInTheDocument();
    expect(screen.getByTestId('candidate-safe-content')).toHaveTextContent(
      '<script>globalThis.__candidateInjected=true</script>Use only currently enabled tools.',
    );
    expect(view.container.querySelector('script')).toBeNull();
    expect(screen.getByText('offline replay passed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生产晋升（未开放）' })).toBeDisabled();
    expect(screen.getByText(/候选不会修改 Skills、提示词、权限或运行策略/)).toBeInTheDocument();
    expect(client.listEvolutionCandidates).toHaveBeenCalledWith(
      { limit: 100 },
      expect.any(AbortSignal),
    );
  });
});
