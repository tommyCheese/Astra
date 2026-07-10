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
  events: RunEvent[];
};
