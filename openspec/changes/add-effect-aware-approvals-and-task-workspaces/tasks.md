## 1. Permission contracts and persistence

- [x] 1.1 Add PermissionRequest/Decision, subject identity, delegation chain, policy explanation, and audit schemas
- [x] 1.2 Add ActionEffectPlan, effect item, grant proposal, workspace change, and checkpoint schemas
- [x] 1.3 Extend ApprovalRequest persistence with frozen effect plans, analyzer versions, and reviewer identity
- [x] 1.4 Extend ApprovalGrant persistence with Run/Task scope, leases, expiry, usage limits, effect kinds, resource matchers, and invocation constraints
- [x] 1.5 Add Task Workspace, workspace file, change tombstone, and checkpoint persistence
- [x] 1.6 Add Agent identity, delegation, Tool Catalog Snapshot, Credential Grant, and DataFlowState persistence

## 2. Permission engine and policy governance

- [x] 2.1 Implement typed platform, managed, user, Task, Run, and one-time policy layers with deny → ask → allow precedence
- [x] 2.2 Add protected resources and prevent lower-trust policy from expanding higher-trust boundaries
- [x] 2.3 Add conditional permission leases, expiry, usage limits, revocation, and integrity invalidation
- [x] 2.4 Add Policy Explain, permission center, policy simulation, and shadow-decision comparison
- [x] 2.5 Add Permission Bundles and fail-closed behavior for scheduled, headless, and background Runs

## 3. Tool permission and execution-mode policy

- [x] 3.1 Add the tool effect-analyzer interface and conservative fallback
- [x] 3.2 Replace tool-wide approval decisions with the platform-policy × ToolSpec × invocation-effect decision pipeline
- [x] 3.3 Allow read-only and ephemeral-compute actions in plan-only mode while blocking all persistent side effects without approval prompts
- [x] 3.4 Make request-approval auto-run no-side-effect actions and gate side-effect actions by matching grants
- [x] 3.5 Keep auto-approval subject to platform prohibitions, scoped permissions, budgets, and Sandbox enforcement

## 4. Tool effect analyzers

- [x] 4.1 Add explicit read-only effects for web search and web fetch
- [x] 4.2 Add Bash parsing for read-only commands, redirection, file mutation, deletion, network methods, and unknown programs
- [x] 4.3 Add effect analyzers for chart, file, artifact, dependency, credential, delegation, and external-write actions
- [x] 4.4 Add analyzer-version integrity checks and security-focused classification tests
- [x] 4.5 Keep Bash ToolSpec maximum permissions aligned with analyzer-produced temporary-compute and workspace-write effects

## 5. Identity, credentials, data, and extension trust

- [x] 5.1 Issue auditable identities for main Agents, subagents, reviewers, tool runtimes, and external providers
- [x] 5.2 Enforce permission attenuation across delegation and prohibit self-approval or privilege amplification
- [x] 5.3 Implement the Credential Broker with short-lived scoped credentials, redaction, revocation, and on-behalf-of audit
- [x] 5.4 Track sensitive and untrusted DataFlowState and enforce payload/destination-aware egress policy
- [x] 5.5 Inventory and allowlist MCP, plugins, Skills, Hooks, custom Agents, and marketplaces with pinned identities and digests
- [x] 5.6 Freeze per-Run Tool Catalog Snapshots and fail closed on provider or schema drift

## 6. Task Workspace runtime

- [x] 6.1 Create and clean up persistent isolated Task Workspaces with quotas
- [x] 6.2 Mount Task Workspace per ToolCall as none, read-only, or read-write according to the frozen effect plan
- [x] 6.3 Add Task write locking and safe Run restart behavior
- [x] 6.4 Preserve dependency state without exposing cache and dependency files as normal user deliverables
- [x] 6.5 Separate the control plane from Workspace code and use verified read-only runtime entrypoints
- [x] 6.6 Sanitize PATH, HOME, language, package-manager, Git, shell, credential, and startup configuration
- [x] 6.7 Reject unsafe links, special files, path confusion, archive traversal, decompression bombs, and out-of-scope filesystem access
- [x] 6.8 Treat Workspace instructions as untrusted context that cannot grant permissions or alter execution policy

## 7. Workspace change tracking and delivery

- [x] 7.1 Generate bounded before/after manifests and ToolCall-level created, modified, and deleted records
- [x] 7.2 Create Run checkpoints and expose the current Task file view
- [x] 7.3 Expand Artifact validation and preview support for images, documents, text, source, and common data files
- [x] 7.4 Render final file summaries, previews, downloads, and deletion history
- [ ] 7.5 Feed ToolCall-level Workspace changes back into Bash observations and use them to stop completed one-step file tasks

## 8. Permission and approval experience

- [x] 8.1 Replace tool-first approval copy with action, resource, risk, data, identity, cwd, network, and credential summaries
- [x] 8.2 Support allow-once, backend-proposed Run grants, explicit Task grants, rejection, and optional rejection guidance
- [x] 8.3 Restore pending effect approvals after refresh/restart and prevent stale or replayed decisions
- [x] 8.4 Update execution-mode labels and help text to explain side-effect-free plan-only behavior
- [x] 8.5 Add the permission center, Grant revocation, tool trust, delegation, credential use, and policy explanation UI
- [ ] 8.6 Redesign the permission center around human-readable active permissions, files, safety activity, and progressively disclosed technical details
- [x] 8.7 Simplify approval cards to action, user-visible resource, practical risk, and friendly scope choices only
- [ ] 8.8 Render each ToolCall completion state once in the reasoning timeline

## 9. Verification

- [x] 9.1 Test the full execution-mode and effect matrix across read, temporary compute, create, modify, delete, external write, and forbidden actions
- [x] 9.2 Test managed deny precedence, policy simulation, lease expiry, revocation, protected resources, and fail-closed behavior
- [x] 9.3 Test Run and Task grant boundaries, matcher non-escalation, identity chains, and subagent permission attenuation
- [x] 9.4 Test scoped credentials, secret redaction, data-flow egress, network destination controls, and confused-deputy prevention
- [x] 9.5 Test MCP/plugin/Hook/Skill trust, schema drift, malicious annotations, and supply-chain changes
- [x] 9.6 Test cross-tool and cross-Run workspace sharing, checkpoints, deletion tombstones, quotas, and cleanup
- [x] 9.7 Test malicious filenames, symlink/hardlink escapes, Git hooks, shell startup files, package lifecycle scripts, language autoloading, archive bombs, prompt injection, and resource exhaustion
- [x] 9.8 Run backend, frontend, migration, Sandbox, OpenSpec, and security regression suites
- [ ] 9.9 Build and browser-check the redesigned permission center at desktop and narrow viewport sizes
- [ ] 9.10 Verify approval resume preserves the tool-call budget and a successful single-file Bash task executes exactly once
