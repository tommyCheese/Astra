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
  notes: string[];
};

export type FinalResult = {
  summary: string;
  findings: Array<{ text: string; source_urls: string[] }>;
  sources: Array<{ url: string; title?: string | null; retrieved_at?: string | null }>;
  failed_sources?: Array<{ url?: string; title?: string | null; category?: string; message?: string }>;
  source_quality?: Array<{
    url: string;
    quality_score?: number | null;
    extraction_strategy?: string | null;
    warnings?: string[];
  }>;
  conflicts?: Array<Record<string, unknown>>;
  caveats: string[];
  verification_notes: string[];
  memory_references?: Array<Record<string, unknown>>;
  audit_refs?: Record<string, unknown>;
  verification_report?: VerificationReport;
};

export type RunView = {
  id: string;
  task_id: string;
  status: string;
  mode: string;
  summary?: string | null;
  result?: FinalResult | null;
  steps: StepView[];
  tool_calls: ToolCallView[];
  artifacts: ArtifactView[];
  events: RunEvent[];
  turns?: AgentTurnView[];
  memories?: MemoryView[];
  chat_messages?: ChatMessage[];
  verification_report?: VerificationReport | null;
};
