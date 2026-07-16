## 1. Permission contracts and persistence

- [ ] 1.1 Add PermissionRequest/Decision, subject identity, delegation chain, policy explanation, and audit schemas
- [ ] 1.2 Add ActionEffectPlan, effect item, grant proposal, workspace change, and checkpoint schemas
- [ ] 1.3 Extend ApprovalRequest persistence with frozen effect plans, analyzer versions, and reviewer identity
- [ ] 1.4 Extend ApprovalGrant persistence with Run/Task scope, leases, expiry, usage limits, effect kinds, resource matchers, and invocation constraints
- [ ] 1.5 Add Task Workspace, workspace file, change tombstone, and checkpoint persistence
- [ ] 1.6 Add Agent identity, delegation, Tool Catalog Snapshot, Credential Grant, and DataFlowState persistence

## 2. Permission engine and policy governance

- [ ] 2.1 Implement typed platform, managed, user, Task, Run, and one-time policy layers with deny → ask → allow precedence
- [ ] 2.2 Add protected resources and prevent lower-trust policy from expanding higher-trust boundaries
- [ ] 2.3 Add conditional permission leases, expiry, usage limits, revocation, and integrity invalidation
- [ ] 2.4 Add Policy Explain, permission center, policy simulation, and shadow-decision comparison
- [ ] 2.5 Add Permission Bundles and fail-closed behavior for scheduled, headless, and background Runs

## 3. Tool permission and execution-mode policy

- [ ] 3.1 Add the tool effect-analyzer interface and conservative fallback
- [ ] 3.2 Replace tool-wide approval decisions with the platform-policy × ToolSpec × invocation-effect decision pipeline
- [ ] 3.3 Allow read-only and ephemeral-compute actions in plan-only mode while blocking all persistent side effects without approval prompts
- [ ] 3.4 Make request-approval auto-run no-side-effect actions and gate side-effect actions by matching grants
- [ ] 3.5 Keep auto-approval subject to platform prohibitions, scoped permissions, budgets, and Sandbox enforcement

## 4. Tool effect analyzers

- [ ] 4.1 Add explicit read-only effects for web search and web fetch
- [ ] 4.2 Add Bash parsing for read-only commands, redirection, file mutation, deletion, network methods, and unknown programs
- [ ] 4.3 Add effect analyzers for chart, file, artifact, dependency, credential, delegation, and external-write actions
- [ ] 4.4 Add analyzer-version integrity checks and security-focused classification tests

## 5. Identity, credentials, data, and extension trust

- [ ] 5.1 Issue auditable identities for main Agents, subagents, reviewers, tool runtimes, and external providers
- [ ] 5.2 Enforce permission attenuation across delegation and prohibit self-approval or privilege amplification
- [ ] 5.3 Implement the Credential Broker with short-lived scoped credentials, redaction, revocation, and on-behalf-of audit
- [ ] 5.4 Track sensitive and untrusted DataFlowState and enforce payload/destination-aware egress policy
- [ ] 5.5 Inventory and allowlist MCP, plugins, Skills, Hooks, custom Agents, and marketplaces with pinned identities and digests
- [ ] 5.6 Freeze per-Run Tool Catalog Snapshots and fail closed on provider or schema drift

## 6. Task Workspace runtime

- [ ] 6.1 Create and clean up persistent isolated Task Workspaces with quotas
- [ ] 6.2 Mount Task Workspace per ToolCall as none, read-only, or read-write according to the frozen effect plan
- [ ] 6.3 Add Task write locking and safe Run restart behavior
- [ ] 6.4 Preserve dependency state without exposing cache and dependency files as normal user deliverables
- [ ] 6.5 Separate the control plane from Workspace code and use verified read-only runtime entrypoints
- [ ] 6.6 Sanitize PATH, HOME, language, package-manager, Git, shell, credential, and startup configuration
- [ ] 6.7 Reject unsafe links, special files, path confusion, archive traversal, decompression bombs, and out-of-scope filesystem access
- [ ] 6.8 Treat Workspace instructions as untrusted context that cannot grant permissions or alter execution policy

## 7. Workspace change tracking and delivery

- [ ] 7.1 Generate bounded before/after manifests and ToolCall-level created, modified, and deleted records
- [ ] 7.2 Create Run checkpoints and expose the current Task file view
- [ ] 7.3 Expand Artifact validation and preview support for images, documents, text, source, and common data files
- [ ] 7.4 Render final file summaries, previews, downloads, and deletion history

## 8. Permission and approval experience

- [ ] 8.1 Replace tool-first approval copy with action, resource, risk, data, identity, cwd, network, and credential summaries
- [ ] 8.2 Support allow-once, backend-proposed Run grants, explicit Task grants, rejection, and optional rejection guidance
- [ ] 8.3 Restore pending effect approvals after refresh/restart and prevent stale or replayed decisions
- [ ] 8.4 Update execution-mode labels and help text to explain side-effect-free plan-only behavior
- [ ] 8.5 Add the permission center, Grant revocation, tool trust, delegation, credential use, and policy explanation UI

## 9. Verification

- [ ] 9.1 Test the full execution-mode and effect matrix across read, temporary compute, create, modify, delete, external write, and forbidden actions
- [ ] 9.2 Test managed deny precedence, policy simulation, lease expiry, revocation, protected resources, and fail-closed behavior
- [ ] 9.3 Test Run and Task grant boundaries, matcher non-escalation, identity chains, and subagent permission attenuation
- [ ] 9.4 Test scoped credentials, secret redaction, data-flow egress, network destination controls, and confused-deputy prevention
- [ ] 9.5 Test MCP/plugin/Hook/Skill trust, schema drift, malicious annotations, and supply-chain changes
- [ ] 9.6 Test cross-tool and cross-Run workspace sharing, checkpoints, deletion tombstones, quotas, and cleanup
- [ ] 9.7 Test malicious filenames, symlink/hardlink escapes, Git hooks, shell startup files, package lifecycle scripts, language autoloading, archive bombs, prompt injection, and resource exhaustion
- [ ] 9.8 Run backend, frontend, migration, Sandbox, OpenSpec, and security regression suites
