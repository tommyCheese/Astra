## 1. Approval persistence and contracts

- [x] 1.1 Add database models and migration for approval requests, Run-scoped grants, and the `bash_execute` tool setting
- [x] 1.2 Add approval request, decision, grant, pending-view, and command-result schemas
- [x] 1.3 Add repository operations for creating, deciding, matching, and serializing approvals with atomic replay protection

## 2. Runtime approval enforcement

- [x] 2.1 Add an approval policy component that evaluates execution mode and backend-generated exact or similar matchers
- [x] 2.2 Pause `request_approval` tool calls before execution with a frozen awaiting-approval checkpoint
- [x] 2.3 Resume the exact frozen ToolCall after approval and turn rejection into an Agent observation without executing the tool
- [x] 2.4 Add the dedicated approval decision API and emit approval lifecycle events

## 3. Sandboxed command tool

- [x] 3.1 Implement and register the disabled-by-default `bash_execute` manifest and tool switch
- [x] 3.2 Execute Bash through the existing one-time Docker sandbox with no network or host mounts and bounded structured output
- [x] 3.3 Add unit and sandbox tests for command results, timeout, registration, isolation metadata, and sanitization

## 4. Approval user experience

- [x] 4.1 Add frontend approval DTOs and API client support for submitting decisions
- [x] 4.2 Render a responsive approval card above the composer with safe details and the available once, similar, and reject actions
- [x] 4.3 Restore pending cards from Run snapshots, refresh after decisions, prevent duplicate submission, and add localized copy
- [x] 4.4 Add frontend tests for display, all decisions, unavailable similar rules, refresh recovery, and execution-mode behavior

## 5. Verification

- [x] 5.1 Add backend integration tests for request approval, auto approval, grant matching, rejection, frozen-action recovery, stale tokens, and replay
- [x] 5.2 Run backend tests, frontend tests/lint/build, OpenSpec validation, and inspect the final diff for security regressions
