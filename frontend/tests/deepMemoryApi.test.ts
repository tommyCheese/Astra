import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  attachEvolutionEvaluation,
  DEEP_MEMORY_API_PATHS,
  DeepMemoryApiError,
  getEvolutionCandidate,
  listConsolidationJobs,
  listEvolutionCandidates,
  listMemories,
  reviewEvolutionCandidate,
  revokeMemory,
  rollbackConsolidationJob,
  triggerConsolidation,
} from '../src/deepMemoryApi';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const rawMemory = {
  id: 'memory-1',
  run_id: 'run-1',
  memory_key: 'fact.one',
  namespace_type: 'run',
  namespace_id: 'run-1',
  scope: 'run',
  kind: 'semantic_fact',
  status: 'active',
  version: 1,
  state_version: 2,
  content: '<script>inert</script>',
  structured_data: {},
  provenance: { run_id: 'run-1' },
  confidence: 0.9,
  importance: 0.8,
  utility_score: 0.1,
  access_count: 1,
  consolidation_generation: 0,
};

const rawJob = {
  id: 'job-1',
  namespace_type: 'session',
  namespace_id: 'session-1',
  status: 'conflict',
  state_version: 3,
  generation: 2,
  input_manifest: {},
  proposal: { operations: [{ operation: 'add', memory_key: 'fact.two', source_memory_ids: ['memory-1'] }] },
  validation: { passed: false, issues: [{ code: 'STALE_INPUT', message: 'stale', severity: 'error' }] },
  profile_snapshot: {},
  model_usage: {},
  publish_result: {},
  error: { code: 'STALE_INPUT' },
  lease_owner: 'worker-1',
  lease_expires_at: '2026-07-30T00:00:00Z',
};

const rawCandidate = {
  id: 'candidate-1',
  namespace_type: 'workspace',
  namespace_id: 'workspace-1',
  candidate: {
    schema_version: 1,
    candidate_key: 'procedure.research.v1',
    revision: 1,
    candidate_type: 'procedure',
    target: 'procedure',
    title: 'Research safely',
    content: '<img src=x onerror=alert(1)>',
    source_refs: [{ source_type: 'run', source_id: 'run-1', digest: 'sha256:run' }],
    required_tools: ['catalog_search'],
    environment_constraints: [{ key: 'runtime.mode', value: 'local' }],
    parameter_changes: [],
    supersedes_id: null,
  },
  candidate_digest: 'sha256:candidate',
  status: 'approved',
  state_version: 3,
  current_evaluation_id: 'evaluation-1',
  current_evaluation_verdict: 'passed',
  created_by: 'author',
  reviewed_by: 'reviewer',
  review_reason: 'passed',
  executable: false,
  production_promotion_enabled: false,
  created_at: '2026-07-30T00:00:00Z',
  updated_at: '2026-07-30T01:00:00Z',
};

describe('deepMemoryApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('uses canonical Memory paths, query parameters, and optimistic revocation payloads', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [rawMemory], total: 1, next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({
        ...rawMemory,
        status: 'revoked',
        state_version: 3,
        sources: [],
        recall_events: [],
        audit_events: [],
        history: [],
      }));
    vi.stubGlobal('fetch', fetchMock);

    const listed = await listMemories({ run_id: 'run/one', include_history: true, limit: 20 });
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/memories?run_id=run%2Fone&include_history=true&limit=20');
    expect(listed.items[0]).toMatchObject({
      id: 'memory-1',
      structured_data: {},
      content: '<script>inert</script>',
    });

    const revoked = await revokeMemory('memory/one', {
      expected_state_version: 2,
      reason: 'incorrect fact',
      actor: 'local-operator',
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/memories/memory%2Fone/revoke');
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'POST' });
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_state_version: 2,
      reason: 'incorrect fact',
      actor: 'local-operator',
    });
    expect(revoked.status).toBe('revoked');
  });

  it('normalizes consolidation audit data and calls canonical trigger and rollback routes', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ jobs: [rawJob], total: 1 }))
      .mockResolvedValueOnce(jsonResponse({ ...rawJob, status: 'queued' }))
      .mockResolvedValueOnce(jsonResponse({ ...rawJob, status: 'rolled_back', state_version: 4 }));
    vi.stubGlobal('fetch', fetchMock);

    const listed = await listConsolidationJobs({ status: 'conflict', limit: 10 });
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/memory/consolidation/jobs?status=conflict&limit=10');
    expect(listed.items[0]).toMatchObject({
      status: 'conflict',
      lease_owner: 'worker-1',
      proposal_operations: [{
        operation: 'add',
        memory_key: 'fact.two',
        source_memory_ids: ['memory-1'],
      }],
    });
    expect(listed.items[0]?.validation.issues[0]?.message).toBe('stale');

    await triggerConsolidation({
      namespace_type: 'session',
      namespace_id: 'session-1',
      idempotency_key: 'manual-one',
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(DEEP_MEMORY_API_PATHS.consolidationJobs);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      namespace_type: 'session',
      namespace_id: 'session-1',
      idempotency_key: 'manual-one',
    });

    await rollbackConsolidationJob('job/one', {
      expected_state_version: 3,
      reason: 'regression',
      actor: 'operator',
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/memory/consolidation/jobs/job%2Fone/rollback');
  });

  it('flattens immutable nested evolution snapshots without trusting promotion flags', async () => {
    const detail = {
      ...rawCandidate,
      sources: [{
        source_type: 'run',
        source_id: 'run-1',
        digest: 'sha256:run',
        accessible: true,
        created_at: '2026-07-30T00:00:00Z',
      }],
      evaluations: [{
        id: 'evaluation-1',
        version: 1,
        manifest_digest: 'sha256:evaluation',
        evaluator: 'offline-replay',
        issuer: 'astra',
        verdict: 'passed',
        manifest: {},
        created_at: '2026-07-30T00:30:00Z',
      }],
      audit_events: [{
        id: 7,
        event_type: 'candidate.approved',
        actor: 'reviewer',
        reason: 'passed',
        expected_state_version: 2,
        actual_state_version: 3,
        payload: {},
        created_at: '2026-07-30T01:00:00Z',
      }],
      rollback_metadata: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([rawCandidate]))
      .mockResolvedValueOnce(jsonResponse(detail))
      .mockResolvedValueOnce(jsonResponse(detail))
      .mockResolvedValueOnce(jsonResponse(detail));
    vi.stubGlobal('fetch', fetchMock);

    const listed = await listEvolutionCandidates({ status: 'approved', limit: 5 });
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/agent-evolution/candidates?status=approved&limit=5');
    expect(listed.production_promotion_enabled).toBe(false);
    expect(listed.items[0]).toMatchObject({
      id: 'candidate-1',
      candidate_key: 'procedure.research.v1',
      title: 'Research safely',
      target_component: 'procedure',
      content: '<img src=x onerror=alert(1)>',
      required_tools: ['catalog_search'],
      executable: false,
      production_promotion_enabled: false,
    });

    const candidate = await getEvolutionCandidate('candidate/one');
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/agent-evolution/candidates/candidate%2Fone');
    expect(candidate.sources[0]).toMatchObject({
      source_kind: 'run',
      source_ref: 'run-1',
      source_hash: 'sha256:run',
    });
    expect(candidate.audit_events[0]).toMatchObject({
      id: '7',
      expected_state_version: 2,
      actual_state_version: 3,
    });

    await reviewEvolutionCandidate('candidate-1', {
      decision: 'approve',
      expected_state_version: 2,
      actor: 'reviewer',
      reason: 'passed',
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/agent-evolution/candidates/candidate-1/approve');
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      expected_state_version: 2,
      actor: 'reviewer',
      reason: 'passed',
    });

    await attachEvolutionEvaluation('candidate-1', {
      expected_state_version: 1,
      actor: 'evaluator',
      reason: 'offline replay',
      manifest: { schema_version: 1 },
    });
    expect(fetchMock.mock.calls[3]?.[0]).toBe('/api/agent-evolution/candidates/candidate-1/evaluations');
  });

  it('surfaces FastAPI conflict detail without exposing an executable promotion path in the UI', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'input manifest changed' }, 409)));

    await expect(rollbackConsolidationJob('job-1', {
      expected_state_version: 1,
      actor: 'operator',
      reason: 'rollback',
    })).rejects.toEqual(expect.objectContaining<Partial<DeepMemoryApiError>>({
      name: 'DeepMemoryApiError',
      status: 409,
      message: 'input manifest changed',
    }));
  });
});
