## 1. Provider transport contract

- [x] 1.1 Add a model-thinking stream observer contract and bind it only for Runs whose effective model thinking is enabled
- [x] 1.2 Parse OpenAI-compatible streaming and non-streaming `reasoning_content` separately from ordinary content
- [x] 1.3 Request Anthropic summarized thinking and parse only visible `thinking`/`thinking_delta` text while ignoring signatures and redacted blocks
- [x] 1.4 Record thinking-content visibility in non-sensitive reasoning usage metadata

## 2. Runtime events and persistence

- [x] 2.1 Implement a buffered Run model-thinking writer with stable stream IDs, operation metadata, per-invocation and per-Run limits
- [x] 2.2 Emit and persist `model_thinking.started`, `delta`, `completed`, and `unavailable` events with explicit summary/reasoning and truncation state
- [x] 2.3 Coalesce live model-thinking deltas without changing snapshot recovery order

## 3. Chat presentation

- [x] 3.1 Extend frontend process-stream types and reducer to reconstruct model-thinking streams from live and snapshot events
- [x] 3.2 Render model thinking as a separate expandable process item that preserves whitespace and labels summaries, unavailability, and truncation accurately
- [x] 3.3 Update model-thinking documentation and localized UI copy to explain Provider-visible content and persistence boundaries

## 4. Verification

- [x] 4.1 Add backend transport tests for Qwen/DeepSeek-compatible reasoning content, Anthropic thinking deltas, disabled thinking, and protected-field exclusion
- [x] 4.2 Add runtime tests for event ordering, buffering, unavailable/truncated states, persistence recovery, and no contamination of Astra reasoning summaries
- [x] 4.3 Add frontend reducer/component tests for live streaming, history recovery, Provider summary labels, unavailable state, and preserved formatting
- [x] 4.4 Run focused backend and frontend suites plus OpenSpec validation, and resolve all failures caused by this change
