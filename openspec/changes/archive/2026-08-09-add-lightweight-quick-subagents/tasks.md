## 1. Shared Policy And Contracts

- [x] 1.1 Allow standard Run profiles and create requests to use `subagent_mode = required` without `plan_execution`
- [x] 1.2 Preserve the compiled Subagent policy for standard Runs while keeping fast reasoning, basic verification and no canonical Plan
- [x] 1.3 Add one shared Subagent execution eligibility helper used by Run creation and AgentLoop runtime gating

## 2. Shared Runtime Integration

- [x] 2.1 Expose `swarm` to eligible standard root Agents without creating Plan or AgentState placeholders
- [x] 2.2 Start the existing SubagentSupervisor for eligible standard Runs and reuse existing Join, pending-wait, cancellation and recovery paths
- [x] 2.3 Keep required-group and pending-Join finalization gates active for standard Runs while skipping trusted Completion Gate behavior

## 3. Command And UI Behavior

- [x] 3.1 Route `/subagent <task>` using the current answer mode, with auto Plan execution only for trusted Runs
- [x] 3.2 Reuse the existing compact SubagentPanel for standard Runs and verify no trusted graph pane is mounted

## 4. Verification And Documentation

- [x] 4.1 Add backend tests for standard policy compilation, eligibility, required-mode validation, shared Supervisor creation and no-DAG execution
- [x] 4.2 Update frontend tests for quick/trusted `/subagent` routing and compact quick Subagent rendering
- [x] 4.3 Update governed Subagent documentation with lightweight quick-mode semantics and shared-runtime boundaries
- [x] 4.4 Run focused backend/frontend tests and strict OpenSpec validation
- [x] 4.5 Add a standalone in-app help chapter for quick and trusted mode definitions, differences, Subagent behavior and selection guidance, with navigation and rendering tests
- [x] 4.6 Add an About Astra help chapter covering creation motivation, mission, principles, and repository-grounded copyright and Apache-2.0 license information
- [x] 4.7 Move each help article's table of contents into a responsive sticky side navigation with rendering coverage
