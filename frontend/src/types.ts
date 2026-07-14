export type RunEvent = {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type StepView = {
  id: string;
  index: number;
  title: string;
  intent: string;
  status: string;
  evidence?: Record<string, unknown> | null;
};

export type ToolCallView = {
  id: string;
  step_id?: string | null;
  tool_name: string;
  status: string;
  input: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
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
  sandbox_job_id?: string | null;
  provenance?: Record<string, unknown>;
  content_url?: string | null;
};

export type AgentTurnView = {
  id: string;
  run_id: string;
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

export type MemoryView = {
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

export type VerificationReport = {
  status: string;
  source_count: number;
  caveat_count: number;
  low_quality_sources: Array<Record<string, unknown>>;
  failed_sources: Array<Record<string, unknown>>;
  memory_references: Array<Record<string, unknown>>;
  invalid_artifact_references: number;
  notes: string[];
};

export type SandboxJobView = {
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

export type FailedSource = {
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

export type SourceQuality = {
  url: string;
  title?: string | null;
  quality_score?: number | null;
  extraction_strategy?: string | null;
  warnings: string[];
};

export type CompletionDecision = {
  state: 'continue' | 'completed' | 'completed_with_warnings' | 'waiting_user' | 'blocked' | 'failed';
  reason: string;
  unmet_criteria: string[];
  warnings: string[];
  required_user_action?: string | null;
};

export type RunError = {
  type: string;
  code: string;
  message: string;
  retryable: boolean;
  trace_id?: string | null;
  details: Record<string, unknown>;
};

export type RunResult = {
  summary: string;
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
  plan_graph?: Record<string, unknown>;
  agent_state?: Record<string, unknown>;
  state_version?: number;
  terminal_reason?: Record<string, unknown> | null;
  waiting_state?: Record<string, unknown> | null;
  task_adapter?: string;
};

export type ConversationSummary = {
  id: string;
  title: string;
  title_source: string;
  pinned_at: string | null;
  created_at: string;
  updated_at: string;
  last_run_status: string | null;
  last_message_preview: string;
  has_active_share: boolean;
};

export type ConversationView = ConversationSummary & { runs: RunView[] };
export type ConversationShare = { url: string; created_at: string; updated_at: string };
export type SharedConversation = { title: string; messages: Array<{ role: 'user' | 'assistant'; content: string }>; shared_at: string; updated_at: string };
