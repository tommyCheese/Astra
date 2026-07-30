## Why

Astra can discover and fetch public Web sources, but multiple searches do not form a durable evidence set, fetched content is represented as mutable untyped dictionaries, and trusted verification only checks that some source URL exists. Deep Research would amplify those weaknesses unless Web retrieval first exposes stable atomic contracts and trusted execution gains a provider-independent grounding layer.

## What Changes

- Extend Web search with bounded batch queries, explicit freshness/domain/content filters, provider capability disclosures, and append-only candidate accumulation.
- Extend Web reading with immutable source snapshots and stable passages that can be found and reopened without another network request.
- Introduce a run-scoped Evidence Ledger connecting search traces, candidates, snapshots, passages, claims, support edges, and citations to ToolCall, Plan node, execution, and Artifact provenance.
- Route canonical evidence fragments through the plugin invocation pipeline instead of assembling Web evidence through tool-name branches in AgentLoop and NodeWorker.
- Add shared provenance, citation-integrity, and claim-support validators whose outcomes participate in the existing VerificationEngine and CompletionGate.
- Extend trusted results with typed claims and citations while preserving existing summary, findings, sources, and audit fields.
- Keep ordinary trusted execution as the default workflow. This change does not implement or implicitly activate a Deep Research module, research planner, source-count policy, long-report renderer, private connectors, or authenticated browser access.

## Capabilities

### New Capabilities

- `web-atomic-retrieval`: Provider-independent search, read, find, and open operations with explicit constraints and stable source references.
- `evidence-grounding-runtime`: Run-scoped canonical evidence ingestion, storage, context projection, claim/citation binding, and trusted validation.

### Modified Capabilities

- `adaptive-web-crawler`: Successful Web reads produce immutable snapshot and passage metadata in addition to bounded extracted content.
- `google-web-search`: Search requests and normalized results expose applied and unsupported constraints without leaking credentials.
- `source-summary-task`: Trusted Web summaries bind material claims to canonical evidence rather than only listing source URLs.
- `completion-gate`: Mandatory grounding validation outcomes can block trusted completion without making research-specific policies globally mandatory.
- `policy-driven-tool-runtime`: Result processors emit schema-validated canonical evidence fragments that the host persists with invocation lineage.

## Impact

- Backend schemas gain canonical grounding contracts and persisted run-scoped evidence records.
- The built-in `astra.web` plugin, InvocationPipeline, Web tools, AgentLoop finalization, trusted verification, and RunResult projection are affected.
- Database migration adds append-only evidence storage; raw source bodies remain in the existing Artifact store.
- Frontend result types and answer rendering gain inline citation/source metadata while retaining existing source cards.
- Existing Runs remain readable, existing trusted Runs do not select a workflow module, and the Web tool names remain compatible during migration.
