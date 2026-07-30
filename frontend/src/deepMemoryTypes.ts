export type JsonObject = Record<string, unknown>;

export type MemoryNamespaceType = 'run' | 'task' | 'workspace' | 'user';
export type MemoryLifecycleStatus =
  | 'candidate'
  | 'active'
  | 'superseded'
  | 'revoked'
  | 'expired'
  | 'quarantined';
export type MemoryKind =
  | 'semantic_fact'
  | 'user_preference'
  | 'episodic_experience'
  | 'procedure'
  | 'failure_pattern'
  | 'evaluation_feedback';

export type MemorySource = {
  id: string;
  source_kind: string;
  source_ref: string;
  source_hash?: string | null;
  run_id?: string | null;
  turn_id?: string | null;
  tool_call_id?: string | null;
  artifact_id?: string | null;
  accessible: boolean;
  created_at?: string | null;
  revoked_at?: string | null;
  source_data: JsonObject;
};

export type RecallScoreComponents = {
  total: number;
  lexical?: number;
  tags?: number;
  kind?: number;
  recency?: number;
  confidence?: number;
  importance?: number;
  utility?: number;
  semantic?: number;
};

export type MemoryRecallAudit = {
  event_id: string;
  run_id?: string | null;
  turn_id?: string | null;
  query_fingerprint?: string | null;
  policy_version?: string | null;
  shadow: boolean;
  selected: boolean;
  exclusion_reason?: string | null;
  scores: RecallScoreComponents;
  feedback: JsonObject;
  created_at?: string | null;
};

export type MemoryAuditEvent = {
  id: string;
  event_type: string;
  actor?: string | null;
  reason?: string | null;
  payload: JsonObject;
  created_at?: string | null;
};

export type MemoryRecord = {
  id: string;
  run_id?: string | null;
  memory_key: string;
  namespace_type: MemoryNamespaceType | string;
  namespace_id: string;
  scope: string;
  kind: MemoryKind | string;
  status: MemoryLifecycleStatus | string;
  version: number;
  state_version: number;
  content: string;
  structured_data: JsonObject;
  provenance: JsonObject;
  confidence: number;
  importance: number;
  utility_score: number;
  access_count: number;
  observed_at?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  supersedes_id?: string | null;
  consolidation_generation: number;
  created_at?: string | null;
  updated_at?: string | null;
  expires_at?: string | null;
  last_accessed_at?: string | null;
  revoked_at?: string | null;
  revoke_reason?: string | null;
};

export type MemoryDetail = MemoryRecord & {
  sources: MemorySource[];
  recall_events: MemoryRecallAudit[];
  audit_events: MemoryAuditEvent[];
  history: MemoryRecord[];
};

export type MemoryListQuery = {
  query?: string;
  status?: MemoryLifecycleStatus | '';
  kind?: MemoryKind | '';
  namespace_type?: MemoryNamespaceType | '';
  namespace_id?: string;
  run_id?: string;
  include_history?: boolean;
  limit?: number;
  cursor?: string;
};

export type MemoryListResult = {
  items: MemoryRecord[];
  total: number;
  next_cursor?: string | null;
};

export type MemoryRevocationRequest = {
  expected_state_version: number;
  reason: string;
  actor?: string;
};

export type ConsolidationJobStatus =
  | 'queued'
  | 'running'
  | 'proposed'
  | 'validation_failed'
  | 'published'
  | 'rolled_back'
  | 'interrupted'
  | 'failed';

export type ConsolidationProposalOperation = {
  operation: string;
  memory_id?: string | null;
  memory_key?: string | null;
  content?: string | null;
  source_memory_ids: string[];
  details: JsonObject;
};

export type ConsolidationValidation = {
  passed: boolean;
  issues: Array<{
    code: string;
    message: string;
    severity: string;
  }>;
  warnings: string[];
};

export type ConsolidationJob = {
  id: string;
  namespace_type: MemoryNamespaceType | string;
  namespace_id: string;
  status: ConsolidationJobStatus | string;
  state_version: number;
  generation: number;
  idempotency_key?: string | null;
  input_hash?: string | null;
  input_manifest: JsonObject;
  proposal: JsonObject;
  proposal_operations: ConsolidationProposalOperation[];
  validation: ConsolidationValidation;
  profile_snapshot: JsonObject;
  model_usage: JsonObject;
  publish_result: JsonObject;
  error?: JsonObject | null;
  rollback_of_id?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  published_at?: string | null;
};

export type ConsolidationJobListResult = {
  items: ConsolidationJob[];
  total: number;
  next_cursor?: string | null;
};

export type ConsolidationActionRequest = {
  expected_state_version: number;
  reason: string;
  actor?: string;
};

export type EvolutionCandidateStatus =
  | 'draft'
  | 'evaluating'
  | 'rejected'
  | 'approved'
  | 'shadow'
  | 'canary'
  | 'promoted'
  | 'rolled_back';

export type EvolutionSource = {
  id: string;
  source_kind: string;
  source_ref: string;
  source_hash?: string | null;
  run_id?: string | null;
  memory_id?: string | null;
  accessible: boolean;
  created_at?: string | null;
};

export type EvolutionEvaluation = {
  id: string;
  version: number;
  manifest_digest: string;
  evaluator: string;
  issuer: string;
  verdict: string;
  manifest: JsonObject;
  created_at?: string | null;
};

export type EvolutionCandidate = {
  id: string;
  candidate_key: string;
  revision: number;
  supersedes_id?: string | null;
  candidate_type: 'procedure' | 'policy_recommendation' | string;
  target_component: string;
  namespace_type: MemoryNamespaceType | string;
  namespace_id: string;
  status: EvolutionCandidateStatus | string;
  state_version: number;
  content: JsonObject;
  content_digest?: string | null;
  source_manifest: JsonObject;
  source_manifest_digest?: string | null;
  environment_constraints: JsonObject;
  current_evaluation_id?: string | null;
  created_by?: string | null;
  reviewed_by?: string | null;
  review_reason?: string | null;
  sources: EvolutionSource[];
  evaluations: EvolutionEvaluation[];
  created_at?: string | null;
  updated_at?: string | null;
};

export type EvolutionCandidateListResult = {
  items: EvolutionCandidate[];
  total: number;
  next_cursor?: string | null;
  production_promotion_enabled: false;
};
