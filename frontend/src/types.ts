type RunEvent = {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type StepView = {
  id: string;
  plan_id?: string | null;
  plan_version?: number | null;
  node_key?: string | null;
  index: number;
  title: string;
  intent: string;
  status: string;
  depends_on?: string[];
  required_capabilities?: string[];
  success_criteria_refs?: string[];
  expected_outcome?: Record<string, unknown> | null;
  risk_level?: string;
  optional?: boolean;
  evidence_refs?: string[];
  failure?: Record<string, unknown> | null;
  evidence?: Record<string, unknown> | null;
  lineage_node_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

export type PlanNodeStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'blocked' | 'skipped' | 'superseded';

export type PlanGraphNode = {
  id: string;
  plan_id: string;
  plan_version: number;
  node_key: string;
  index: number;
  title: string;
  intent: string;
  status: Exclude<PlanNodeStatus, 'ready' | 'superseded'>;
  depends_on: string[];
  required_capabilities: string[];
  success_criteria_refs: string[];
  expected_outcome?: { kind: string; success_condition: string; required_fields?: string[] } | null;
  risk_level: string;
  optional: boolean;
  evidence_refs: string[];
  failure?: Record<string, unknown> | null;
  lineage_node_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

export type PlanGraphEdge = {
  id: string;
  plan_id: string;
  predecessor_node_id: string;
  successor_node_id: string;
  dependency_type: string;
};

type NodeExecutionPhase =
  | 'claimed'
  | 'running'
  | 'waiting_resource'
  | 'waiting_approval'
  | 'committing'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'result_unknown';

type NodeExecutionStatus =
  | 'active'
  | 'waiting'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'blocked';

type ResourceLease = {
  id: string;
  node_execution_id: string;
  resource_summary: string;
  mode: 'read' | 'write' | 'exclusive';
  fencing_token: number;
  acquired_at: string;
  expires_at: string;
  released_at?: string | null;
  release_reason?: string | null;
};

type BudgetReservation = {
  id: string;
  node_execution_id: string;
  budget_kind: string;
  reserved: number;
  consumed: number;
  status: string;
  created_at: string;
  settled_at?: string | null;
};

export type NodeExecution = {
  execution_id: string;
  run_id?: string;
  plan_id?: string;
  plan_node_id: string;
  plan_version: number;
  attempt: number;
  dispatch_batch_id?: string | null;
  slot_index?: number | null;
  worker_id?: string | null;
  phase: NodeExecutionPhase;
  status: NodeExecutionStatus;
  state_version: number;
  wait_reason?: string | null;
  checkpoint?: Record<string, unknown>;
  started_at?: string | null;
  heartbeat_at?: string | null;
  finished_at?: string | null;
  resource_leases?: ResourceLease[];
  budget_reservations?: BudgetReservation[];
};

type ParallelismSummary = {
  requested_slots: number;
  total_slots: number;
  used_slots: number;
  active_count: number;
  waiting_count: number;
};

export type PlanGraphSnapshot = {
  schema_version: 1 | 2;
  id: string;
  run_id: string;
  version: number;
  status: 'planned' | 'active' | 'superseded' | 'completed';
  supersedes_plan_id?: string | null;
  nodes: PlanGraphNode[];
  edges: PlanGraphEdge[];
  created_at?: string | null;
  activated_at?: string | null;
  completed_at?: string | null;
  active_executions?: NodeExecution[];
  parallelism?: ParallelismSummary | null;
};

type LegacyPlanGraph = {
  id: string;
  run_id?: string;
  version?: number;
  status?: PlanGraphSnapshot['status'];
  nodes?: Array<{
    id: string;
    node_key: string;
    index: number;
    title: string;
    intent: string;
    status: string;
    depends_on: string[];
  }>;
  edges?: PlanGraphEdge[];
};

export type PlanVersionSummary = {
  id: string;
  run_id: string;
  version: number;
  status: PlanGraphSnapshot['status'];
  supersedes_plan_id?: string | null;
  node_count: number;
  created_at: string;
  activated_at?: string | null;
  completed_at?: string | null;
};

export type PlanGraphDiff = {
  from_plan_id: string;
  to_plan_id: string;
  from_version: number;
  to_version: number;
  nodes: Array<{
    node_id: string;
    node_key: string;
    change: 'added' | 'removed' | 'unchanged' | 'modified' | 'inherited_completed';
    previous_node_id?: string | null;
  }>;
  edges: Array<{
    predecessor_node_id: string;
    successor_node_id: string;
    change: 'added' | 'removed' | 'unchanged';
  }>;
};

type ToolCallView = {
  id: string;
  step_id?: string | null;
  plan_node_id?: string | null;
  node_execution_id?: string | null;
  tool_name: string;
  status: string;
  input: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
};

export type PendingApproval = {
  id: string;
  tool_call_id: string;
  node_execution_id?: string | null;
  execution_attempt?: number | null;
  expected_execution_state_version?: number | null;
  tool_name: string;
  preview: string;
  permission: string;
  impact: string;
  action_summary?: string | null;
  affected_resources?: string[];
  risk_reason?: string | null;
  working_directory?: string | null;
  network_scope?: Record<string, unknown>;
  effect_kinds?: string[];
  grant_proposals?: Array<Record<string, unknown>>;
  reviewer_identity?: Record<string, unknown> | null;
  decisions: Array<'approve_once' | 'allow_similar' | 'allow_task' | 'reject'>;
  created_at: string;
};

export type ArtifactView = {
  id: string;
  type: string;
  path?: string | null;
  content_ref?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  mime_type?: string | null;
  size_bytes?: number;
  checksum?: string | null;
  security_status?: string;
  tool_call_id?: string | null;
  plan_node_id?: string | null;
  sandbox_job_id?: string | null;
  provenance?: Record<string, unknown>;
  content_url?: string | null;
};

type AgentTurnView = {
  id: string;
  run_id: string;
  plan_node_id?: string | null;
  node_execution_id?: string | null;
  turn_index: number;
  decision_type: string;
  reasoning_summary: string;
  selected_tool?: string | null;
  decision: Record<string, unknown>;
  observation?: Record<string, unknown> | null;
  reflection?: Record<string, unknown> | null;
  tool_call_id?: string | null;
  artifact_id?: string | null;
  memory_reads: Array<Record<string, unknown>>;
  memory_writes: Array<Record<string, unknown>>;
  status: string;
  evaluation?: Record<string, unknown> | null;
  reflection_patch?: Record<string, unknown> | null;
  state_version_before?: number | null;
  state_version_after?: number | null;
  plan_version?: number;
  phase?: string;
  idempotency_key?: string | null;
  paused_node?: string | null;
  created_at: string;
  updated_at: string;
};

type MemoryView = {
  id: string;
  run_id?: string | null;
  scope: string;
  kind: string;
  content: string;
  structured_data: Record<string, unknown>;
  provenance: Record<string, unknown>;
  confidence: number;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
};

export type ChatMessage = {
  id: string;
  role: string;
  content: string;
  status: string;
  metadata: Record<string, unknown>;
};

type VerificationReport = {
  status: string;
  assurance_level?: 'basic' | 'full';
  source_count: number;
  caveat_count: number;
  low_quality_sources: Array<Record<string, unknown>>;
  failed_sources: Array<Record<string, unknown>>;
  memory_references: Array<Record<string, unknown>>;
  invalid_artifact_references: number;
  notes: string[];
};

type SandboxJobView = {
  id: string;
  tool_call_id?: string | null;
  status: string;
  executor: string;
  runtime_profile: Record<string, unknown>;
  resource_limits: Record<string, unknown>;
  runtime_name?: string | null;
  image_digest?: string | null;
  exit_reason?: string | null;
  error?: Record<string, unknown> | null;
  stdout_summary?: string | null;
  stderr_summary?: string | null;
  input_artifact_ids: string[];
  output_artifact_ids: string[];
};

type FailedSource = {
  url?: string | null;
  title?: string | null;
  type?: string | null;
  category?: string | null;
  code?: string | null;
  message?: string | null;
  retryable: boolean;
  trace_id?: string | null;
  details: Record<string, unknown>;
};

type SourceQuality = {
  url: string;
  title?: string | null;
  quality_score?: number | null;
  extraction_strategy?: string | null;
  warnings: string[];
};

type CompletionDecision = {
  state: 'continue' | 'completed' | 'completed_with_warnings' | 'waiting_user' | 'blocked' | 'failed';
  reason: string;
  unmet_criteria: string[];
  warnings: string[];
  required_user_action?: string | null;
};

type RunError = {
  type: string;
  code: string;
  message: string;
  retryable: boolean;
  trace_id?: string | null;
  details: Record<string, unknown>;
};

type RunResult = {
  summary: string;
  answer_mode?: 'standard' | 'trusted';
  assurance_level?: 'basic' | 'full';
  findings: Array<{ text: string; source_urls: string[]; artifact_ids: string[] }>;
  sources: Array<{ url: string; title?: string | null; retrieved_at?: string | null }>;
  failed_sources: FailedSource[];
  source_quality: SourceQuality[];
  conflicts: Array<{
    statement?: string | null;
    conflicting_statement?: string | null;
    source_urls: string[];
    details: Record<string, unknown>;
  }>;
  caveats: string[];
  verification_notes: string[];
  memory_references: Array<{
    id?: string | null;
    scope?: string | null;
    kind?: string | null;
    content?: string | null;
    confidence?: number | null;
    details: Record<string, unknown>;
  }>;
  audit_refs: {
    evidence_pack_artifact_id?: string | null;
    agent_turn_count: number;
    referenced_artifact_ids: string[];
  };
  verification_report: VerificationReport | null;
  completion_decision: CompletionDecision | null;
  error: RunError | null;
};

export type RunView = {
  id: string;
  task_id: string;
  status: string;
  mode: string;
  answer_mode?: 'standard' | 'trusted';
  execution_profile?: {
    version: 2;
    answer_mode: 'standard' | 'trusted';
    plan_execution: 'auto' | 'confirm' | null;
    contract_mode: 'system_minimal' | 'model';
    assurance_level: 'basic' | 'full';
    reasoning_policy: Record<string, unknown>;
    validators: string[];
    interactive: boolean;
    permission_bundle: Record<string, unknown> | null;
  };
  summary?: string | null;
  result: RunResult | null;
  steps: StepView[];
  tool_calls: ToolCallView[];
  artifacts: ArtifactView[];
  sandbox_jobs?: SandboxJobView[];
  events: RunEvent[];
  turns?: AgentTurnView[];
  memories?: MemoryView[];
  chat_messages?: ChatMessage[];
  reasoning_policy?: Record<string, unknown>;
  task_contract?: Record<string, unknown>;
  plan_graph?: PlanGraphSnapshot | LegacyPlanGraph | Record<string, never>;
  plan_versions?: PlanVersionSummary[];
  agent_state?: Record<string, unknown>;
  state_version?: number;
  terminal_reason?: Record<string, unknown> | null;
  waiting_state?: ({
    kind: 'plan_confirmation';
    continuation_token: string;
    plan_id: string;
    plan_version: number;
    state_version: number;
    request: string;
  } | Record<string, unknown>) | null;
  pending_approval?: PendingApproval | null;
  node_executions?: NodeExecution[];
  parallelism?: ParallelismSummary | null;
  task_adapter?: string;
};

export type ConversationSummary = {
  id: string;
  title: string;
  title_source: string;
  preferred_answer_mode?: 'standard' | 'trusted';
  pinned_at: string | null;
  created_at: string;
  updated_at: string;
  last_run_status: string | null;
  last_message_preview: string;
  has_active_share: boolean;
};

export type ConversationView = ConversationSummary & { runs: RunView[] };
export type ConversationShare = { url: string; created_at: string; updated_at: string };
export type ConversationShareSummary = ConversationShare & { conversation_id: string; title: string; message_count: number };
type SharedProcessItem = { kind: 'reasoning' | 'tool' | 'reflection' | 'verification'; title: string; detail: string; status: 'completed' | 'failed' | 'cancelled' };
export type SharedConversation = { title: string; messages: Array<{ role: 'user' | 'assistant' | 'process'; content: string; items: SharedProcessItem[] }>; shared_at: string; updated_at: string };
