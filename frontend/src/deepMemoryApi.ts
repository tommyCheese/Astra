import type {
  ConsolidationActionRequest,
  ConsolidationJob,
  ConsolidationJobListQuery,
  ConsolidationJobListResult,
  ConsolidationProposalOperation,
  ConsolidationTriggerRequest,
  ConsolidationValidation,
  EvolutionCandidate,
  EvolutionCandidateCreateRequest,
  EvolutionCandidateListQuery,
  EvolutionCandidateListResult,
  EvolutionEvaluationRequest,
  EvolutionPromotionRequest,
  EvolutionRollbackRequest,
  EvolutionReviewRequest,
  EvolutionAuditEvent,
  EvolutionEvaluation,
  EvolutionSource,
  JsonObject,
  MemoryAuditEvent,
  MemoryDetail,
  MemoryListQuery,
  MemoryListResult,
  MemoryRecallAudit,
  MemoryRecord,
  MemoryRevocationRequest,
  MemorySource,
  RecallScoreComponents,
} from './deepMemoryTypes';

export const DEEP_MEMORY_API_PATHS = Object.freeze({
  memories: '/api/memories',
  memory: (memoryId: string) => `/api/memories/${encodeURIComponent(memoryId)}`,
  consolidationJobs: '/api/memory/consolidation/jobs',
  consolidationJob: (jobId: string) => `/api/memory/consolidation/jobs/${encodeURIComponent(jobId)}`,
  evolutionCandidates: '/api/agent-evolution/candidates',
  evolutionCandidate: (candidateId: string) => `/api/agent-evolution/candidates/${encodeURIComponent(candidateId)}`,
});

export class DeepMemoryApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'DEEP_MEMORY_REQUEST_FAILED',
    public readonly details: JsonObject = {},
  ) {
    super(message);
    this.name = 'DeepMemoryApiError';
  }
}

function objectValue(value: unknown): JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function optionalString(value: unknown): string | null {
  return typeof value === 'string' && value.length ? value : null;
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function envelopeRecord(body: unknown, keys: string[]): JsonObject {
  const root = objectValue(body);
  for (const key of keys) {
    const direct = objectValue(root[key]);
    if (Object.keys(direct).length) return direct;
  }
  const data = objectValue(root.data);
  for (const key of keys) {
    const nested = objectValue(data[key]);
    if (Object.keys(nested).length) return nested;
  }
  return Object.keys(data).length ? data : root;
}

function envelopeItems(body: unknown, keys: string[]): { items: unknown[]; envelope: JsonObject } {
  if (Array.isArray(body)) return { items: body, envelope: {} };
  const root = objectValue(body);
  const data = root.data;
  const candidates: unknown[] = [
    root.items,
    ...keys.map((key) => root[key]),
    data,
  ];
  const dataObject = objectValue(data);
  candidates.push(dataObject.items, ...keys.map((key) => dataObject[key]));
  const items = candidates.find(Array.isArray);
  return { items: arrayValue(items), envelope: Object.keys(dataObject).length ? { ...root, ...dataObject } : root };
}

async function apiJson(input: RequestInfo | URL, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(input, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init.headers,
    },
  });
  if (response.ok) {
    if (response.status === 204) return {};
    return response.json();
  }
  let body: JsonObject = {};
  try {
    body = objectValue(await response.json());
  } catch {
    // The status and conservative fallback below remain safe to show.
  }
  const error = objectValue(body.error);
  throw new DeepMemoryApiError(
    stringValue(
      error.message,
      stringValue(body.message, stringValue(body.detail, '深度记忆服务暂时不可用。')),
    ),
    response.status,
    stringValue(error.code, stringValue(body.code, 'DEEP_MEMORY_REQUEST_FAILED')),
    objectValue(error.details ?? body.details),
  );
}

function normalizeSource(value: unknown): MemorySource {
  const item = objectValue(value);
  return {
    id: stringValue(item.id, `${stringValue(item.source_kind, 'source')}:${stringValue(item.source_ref)}`),
    source_kind: stringValue(item.source_kind, 'unknown'),
    source_ref: stringValue(item.source_ref),
    source_hash: optionalString(item.source_hash),
    run_id: optionalString(item.run_id),
    turn_id: optionalString(item.turn_id),
    tool_call_id: optionalString(item.tool_call_id),
    artifact_id: optionalString(item.artifact_id),
    accessible: booleanValue(item.accessible, true),
    created_at: optionalString(item.created_at),
    revoked_at: optionalString(item.revoked_at),
    source_data: objectValue(item.source_data),
  };
}

function normalizeScore(value: unknown): RecallScoreComponents {
  const score = objectValue(value);
  const component = (key: string) => (
    typeof score[key] === 'number' && Number.isFinite(score[key]) ? score[key] as number : undefined
  );
  return {
    total: numberValue(score.total ?? score.score),
    lexical: component('lexical'),
    tags: component('tags'),
    kind: component('kind'),
    recency: component('recency'),
    confidence: component('confidence'),
    importance: component('importance'),
    utility: component('utility'),
    semantic: component('semantic'),
  };
}

function normalizeRecall(value: unknown): MemoryRecallAudit {
  const item = objectValue(value);
  return {
    event_id: stringValue(item.event_id, stringValue(item.id)),
    run_id: optionalString(item.run_id),
    turn_id: optionalString(item.turn_id),
    query_fingerprint: optionalString(item.query_fingerprint ?? item.query_hash),
    policy_version: optionalString(item.policy_version),
    shadow: booleanValue(item.shadow),
    selected: booleanValue(item.selected, true),
    exclusion_reason: optionalString(item.exclusion_reason),
    scores: normalizeScore(item.scores ?? item.score_components ?? item.score),
    feedback: objectValue(item.feedback),
    created_at: optionalString(item.created_at),
  };
}

function normalizeAudit(value: unknown): MemoryAuditEvent {
  const item = objectValue(value);
  return {
    id: typeof item.id === 'number' ? String(item.id) : stringValue(item.id),
    event_type: stringValue(item.event_type, 'unknown'),
    actor: optionalString(item.actor),
    reason: optionalString(item.reason),
    payload: objectValue(item.payload),
    created_at: optionalString(item.created_at),
  };
}

export function normalizeMemory(value: unknown): MemoryRecord {
  const item = objectValue(value);
  return {
    id: stringValue(item.id),
    run_id: optionalString(item.run_id),
    workspace_id: optionalString(item.workspace_id),
    created_by: optionalString(item.created_by),
    memory_key: stringValue(item.memory_key),
    namespace_type: stringValue(item.namespace_type, 'run'),
    namespace_id: stringValue(item.namespace_id),
    scope: stringValue(item.scope, stringValue(item.namespace_type, 'run')),
    kind: stringValue(item.kind, 'semantic_fact'),
    status: stringValue(item.status, 'candidate'),
    version: numberValue(item.version, 1),
    state_version: numberValue(item.state_version, 1),
    content: stringValue(item.content),
    structured_data: objectValue(item.structured_data),
    provenance: objectValue(item.provenance),
    confidence: numberValue(item.confidence, 0.5),
    importance: numberValue(item.importance, 0.5),
    utility_score: numberValue(item.utility_score),
    access_count: numberValue(item.access_count),
    observed_at: optionalString(item.observed_at),
    valid_from: optionalString(item.valid_from),
    valid_to: optionalString(item.valid_to),
    supersedes_id: optionalString(item.supersedes_id),
    consolidation_generation: numberValue(item.consolidation_generation),
    created_at: optionalString(item.created_at),
    updated_at: optionalString(item.updated_at),
    expires_at: optionalString(item.expires_at),
    last_accessed_at: optionalString(item.last_accessed_at),
    revoked_at: optionalString(item.revoked_at),
    revoke_reason: optionalString(item.revoke_reason),
  };
}

export function normalizeMemoryDetail(value: unknown): MemoryDetail {
  const item = envelopeRecord(value, ['memory']);
  const base = normalizeMemory(item);
  return {
    ...base,
    sources: arrayValue(item.sources).map(normalizeSource),
    recall_events: arrayValue(item.recall_events ?? item.recalls).map(normalizeRecall),
    audit_events: arrayValue(item.audit_events ?? item.events).map(normalizeAudit),
    history: arrayValue(item.history ?? item.versions).map(normalizeMemory),
  };
}

function normalizeOperation(value: unknown): ConsolidationProposalOperation {
  const item = objectValue(value);
  return {
    operation: stringValue(item.operation, stringValue(item.action, stringValue(item.type, 'unknown'))),
    memory_id: optionalString(item.memory_id),
    memory_key: optionalString(item.memory_key),
    content: optionalString(item.content),
    source_memory_ids: arrayValue(item.source_memory_ids ?? item.sources).map((source) => stringValue(source)).filter(Boolean),
    details: objectValue(item.details),
  };
}

function proposalOperations(proposal: JsonObject): ConsolidationProposalOperation[] {
  const direct = arrayValue(proposal.operations);
  if (direct.length) return direct.map(normalizeOperation);
  return ['additions', 'replacements', 'supersessions', 'links'].flatMap((key) =>
    arrayValue(proposal[key]).map((operation) => normalizeOperation({
      ...objectValue(operation),
      operation: objectValue(operation).operation ?? key.replace(/s$/, ''),
    })),
  );
}

function normalizeValidation(value: unknown): ConsolidationValidation {
  const item = objectValue(value);
  return {
    passed: booleanValue(item.passed ?? item.valid),
    issues: arrayValue(item.issues).map((issue) => {
      const detail = objectValue(issue);
      return {
        code: stringValue(detail.code, 'VALIDATION_ISSUE'),
        message: stringValue(detail.message, stringValue(detail.detail)),
        severity: stringValue(detail.severity, 'error'),
      };
    }),
    warnings: arrayValue(item.warnings).map((warning) => stringValue(warning)).filter(Boolean),
  };
}

export function normalizeConsolidationJob(value: unknown): ConsolidationJob {
  const item = envelopeRecord(value, ['job', 'consolidation_job']);
  const proposal = objectValue(item.proposal);
  return {
    id: stringValue(item.id),
    namespace_type: stringValue(item.namespace_type, 'run'),
    namespace_id: stringValue(item.namespace_id),
    status: stringValue(item.status, 'queued'),
    state_version: numberValue(item.state_version, 1),
    generation: numberValue(item.generation, 1),
    idempotency_key: optionalString(item.idempotency_key),
    input_hash: optionalString(item.input_hash),
    input_manifest: objectValue(item.input_manifest),
    proposal,
    proposal_operations: proposalOperations(proposal),
    validation: normalizeValidation(item.validation),
    profile_snapshot: objectValue(item.profile_snapshot),
    model_usage: objectValue(item.model_usage),
    publish_result: objectValue(item.publish_result),
    error: item.error ? objectValue(item.error) : null,
    lease_owner: optionalString(item.lease_owner),
    lease_expires_at: optionalString(item.lease_expires_at),
    rollback_of_id: optionalString(item.rollback_of_id),
    created_at: optionalString(item.created_at),
    started_at: optionalString(item.started_at),
    completed_at: optionalString(item.completed_at),
    published_at: optionalString(item.published_at),
  };
}

function normalizeEvolutionSource(value: unknown): EvolutionSource {
  const item = objectValue(value);
  return {
    id: stringValue(item.id, `${stringValue(item.source_kind ?? item.source_type)}:${stringValue(item.source_ref ?? item.source_id)}`),
    source_kind: stringValue(item.source_kind, stringValue(item.source_type, 'unknown')),
    source_ref: stringValue(item.source_ref, stringValue(item.source_id)),
    source_hash: optionalString(item.source_hash ?? item.digest),
    run_id: optionalString(item.run_id),
    memory_id: optionalString(item.memory_id),
    accessible: booleanValue(item.accessible, true),
    created_at: optionalString(item.created_at),
    revoked_at: optionalString(item.revoked_at),
  };
}

function normalizeEvaluation(value: unknown): EvolutionEvaluation {
  const item = objectValue(value);
  return {
    id: stringValue(item.id),
    version: numberValue(item.version, 1),
    manifest_digest: stringValue(item.manifest_digest),
    evaluator: stringValue(item.evaluator),
    issuer: stringValue(item.issuer),
    verdict: stringValue(item.verdict, 'unknown'),
    manifest: objectValue(item.manifest),
    created_at: optionalString(item.created_at),
  };
}

function normalizeEvolutionAudit(value: unknown): EvolutionAuditEvent {
  const item = objectValue(value);
  return {
    id: typeof item.id === 'number' ? String(item.id) : stringValue(item.id),
    event_type: stringValue(item.event_type, 'unknown'),
    actor: optionalString(item.actor),
    reason: optionalString(item.reason),
    expected_state_version: typeof item.expected_state_version === 'number'
      ? item.expected_state_version
      : null,
    actual_state_version: typeof item.actual_state_version === 'number'
      ? item.actual_state_version
      : null,
    payload: objectValue(item.payload),
    created_at: optionalString(item.created_at),
  };
}

export function normalizeEvolutionCandidate(value: unknown): EvolutionCandidate {
  const root = objectValue(value);
  const data = objectValue(root.data);
  const wrapped = objectValue(root.evolution_candidate);
  const dataWrapped = objectValue(data.evolution_candidate ?? data.candidate);
  const rootCandidateEnvelope = objectValue(root.candidate);
  const item = stringValue(root.id)
    ? root
    : Object.keys(wrapped).length
      ? wrapped
      : stringValue(rootCandidateEnvelope.id)
        ? rootCandidateEnvelope
        : Object.keys(dataWrapped).length
          ? dataWrapped
          : Object.keys(data).length
            ? data
            : root;
  const nested = objectValue(item.candidate);
  const candidate = Object.keys(nested).length ? nested : item;
  const rawContent = candidate.content;
  const content = typeof rawContent === 'string' ? rawContent : objectValue(rawContent);
  const sourceRefs = arrayValue(candidate.source_refs);
  const explicitSources = arrayValue(item.sources);
  return {
    id: stringValue(item.id),
    candidate_key: stringValue(candidate.candidate_key),
    revision: numberValue(candidate.revision, 1),
    supersedes_id: optionalString(candidate.supersedes_id),
    candidate_type: stringValue(candidate.candidate_type, 'procedure'),
    target_component: stringValue(candidate.target_component, stringValue(candidate.target)),
    title: stringValue(candidate.title),
    namespace_type: stringValue(item.namespace_type, 'run'),
    namespace_id: stringValue(item.namespace_id),
    status: stringValue(item.status, 'draft'),
    state_version: numberValue(item.state_version, 1),
    content,
    content_digest: optionalString(item.content_digest ?? item.candidate_digest),
    environment_constraints: arrayValue(candidate.environment_constraints).map(objectValue),
    required_tools: arrayValue(candidate.required_tools).map((tool) => stringValue(tool)).filter(Boolean),
    parameter_changes: arrayValue(candidate.parameter_changes).map(objectValue),
    current_evaluation_id: optionalString(item.current_evaluation_id),
    current_evaluation_verdict: optionalString(item.current_evaluation_verdict),
    created_by: optionalString(item.created_by),
    reviewed_by: optionalString(item.reviewed_by),
    review_reason: optionalString(item.review_reason),
    sources: (explicitSources.length ? explicitSources : sourceRefs).map(normalizeEvolutionSource),
    evaluations: arrayValue(item.evaluations).map(normalizeEvaluation),
    audit_events: arrayValue(item.audit_events).map(normalizeEvolutionAudit),
    rollback_metadata: item.rollback_metadata ? objectValue(item.rollback_metadata) : null,
    executable: false,
    production_promotion_enabled: false,
    created_at: optionalString(item.created_at),
    updated_at: optionalString(item.updated_at),
  };
}

function appendQuery(path: string, query: Record<string, string | number | boolean | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value));
  });
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

export async function listMemories(query: MemoryListQuery = {}, signal?: AbortSignal): Promise<MemoryListResult> {
  const path = appendQuery(DEEP_MEMORY_API_PATHS.memories, query);
  const body = await apiJson(path, { signal });
  const { items, envelope } = envelopeItems(body, ['memories']);
  return {
    items: items.map(normalizeMemory),
    total: numberValue(envelope.total, items.length),
    next_cursor: optionalString(envelope.next_cursor),
  };
}

export async function getMemory(memoryId: string, signal?: AbortSignal): Promise<MemoryDetail> {
  return normalizeMemoryDetail(await apiJson(DEEP_MEMORY_API_PATHS.memory(memoryId), { signal }));
}

export async function revokeMemory(memoryId: string, request: MemoryRevocationRequest): Promise<MemoryDetail> {
  return normalizeMemoryDetail(await apiJson(`${DEEP_MEMORY_API_PATHS.memory(memoryId)}/revoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function listConsolidationJobs(
  query: ConsolidationJobListQuery = {},
  signal?: AbortSignal,
): Promise<ConsolidationJobListResult> {
  const body = await apiJson(appendQuery(DEEP_MEMORY_API_PATHS.consolidationJobs, query), { signal });
  const { items, envelope } = envelopeItems(body, ['jobs', 'consolidation_jobs']);
  return {
    items: items.map(normalizeConsolidationJob),
    total: numberValue(envelope.total, items.length),
    next_cursor: optionalString(envelope.next_cursor),
  };
}

export async function getConsolidationJob(jobId: string, signal?: AbortSignal): Promise<ConsolidationJob> {
  return normalizeConsolidationJob(await apiJson(DEEP_MEMORY_API_PATHS.consolidationJob(jobId), { signal }));
}

export async function triggerConsolidation(request: ConsolidationTriggerRequest): Promise<ConsolidationJob> {
  return normalizeConsolidationJob(await apiJson(DEEP_MEMORY_API_PATHS.consolidationJobs, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function publishConsolidationJob(jobId: string, request: ConsolidationActionRequest): Promise<ConsolidationJob> {
  return normalizeConsolidationJob(await apiJson(`${DEEP_MEMORY_API_PATHS.consolidationJob(jobId)}/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function rollbackConsolidationJob(jobId: string, request: ConsolidationActionRequest): Promise<ConsolidationJob> {
  return normalizeConsolidationJob(await apiJson(`${DEEP_MEMORY_API_PATHS.consolidationJob(jobId)}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function listEvolutionCandidates(
  query: EvolutionCandidateListQuery = {},
  signal?: AbortSignal,
): Promise<EvolutionCandidateListResult> {
  const body = await apiJson(appendQuery(DEEP_MEMORY_API_PATHS.evolutionCandidates, query), { signal });
  const { items, envelope } = envelopeItems(body, ['candidates', 'evolution_candidates']);
  return {
    items: items.map(normalizeEvolutionCandidate),
    total: numberValue(envelope.total, items.length),
    next_cursor: optionalString(envelope.next_cursor),
    // The initial rollout is fail-closed even if an unexpected server field says otherwise.
    production_promotion_enabled: false,
  };
}

export async function getEvolutionCandidate(candidateId: string, signal?: AbortSignal): Promise<EvolutionCandidate> {
  return normalizeEvolutionCandidate(await apiJson(DEEP_MEMORY_API_PATHS.evolutionCandidate(candidateId), { signal }));
}

export async function createEvolutionCandidate(
  request: EvolutionCandidateCreateRequest,
): Promise<EvolutionCandidate> {
  return normalizeEvolutionCandidate(await apiJson(DEEP_MEMORY_API_PATHS.evolutionCandidates, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function reviewEvolutionCandidate(
  candidateId: string,
  request: EvolutionReviewRequest,
): Promise<EvolutionCandidate> {
  const { decision, ...body } = request;
  return normalizeEvolutionCandidate(await apiJson(`${DEEP_MEMORY_API_PATHS.evolutionCandidate(candidateId)}/${decision}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function attachEvolutionEvaluation(
  candidateId: string,
  request: EvolutionEvaluationRequest,
): Promise<EvolutionCandidate> {
  return normalizeEvolutionCandidate(await apiJson(`${DEEP_MEMORY_API_PATHS.evolutionCandidate(candidateId)}/evaluations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function recordEvolutionRollback(
  candidateId: string,
  request: EvolutionRollbackRequest,
): Promise<EvolutionCandidate> {
  return normalizeEvolutionCandidate(await apiJson(`${DEEP_MEMORY_API_PATHS.evolutionCandidate(candidateId)}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}

export async function requestEvolutionPromotion(
  candidateId: string,
  request: EvolutionPromotionRequest,
): Promise<EvolutionCandidate> {
  return normalizeEvolutionCandidate(await apiJson(`${DEEP_MEMORY_API_PATHS.evolutionCandidate(candidateId)}/promotion`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }));
}
