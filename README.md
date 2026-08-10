# Astra

**Move agents from “able to call tools” to “able to finish work under constraints.”**

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Astra is an open-source agent runtime for real task execution. It does not reduce an agent to a “model + prompt + tools” loop. Instead, every user goal becomes a durable, recoverable, and auditable Run whose full lifecycle—from context and planning to execution and evidence-based verification—is managed by the runtime.

Astra's generality does not come from accumulating preset roles or fixed workflows. It comes from applying one execution kernel to web research, files, sandboxed computation, structured artifacts, memory, schedules, and subagents. Across task types, permissions, budgets, evidence, recovery, and completion criteria remain first-class concerns.

> Astra is currently at `v0.1.0` and evolving quickly. Interfaces and deployment contracts may change between releases.

## How Astra differs from general-purpose agent platforms

Many agent platforms start with “how can a model call more tools and orchestrate more roles?” Astra starts with “how can an execution remain controlled when failures, permission boundaries, and uncertain results are real?” This comparison describes architectural emphasis rather than making a universal claim about every other project.

| Dimension | Common platform emphasis | Astra's emphasis |
| --- | --- | --- |
| Unit of execution | A conversation or workflow invocation | A durable Run whose state, events, and checkpoints support recovery and replay |
| Planning | A temporary task list or in-memory orchestration | A Plan DAG with stable node IDs, dependencies, versions, and lineage |
| Tool access | Expose tools directly to the model | Declare capabilities, then resolve eligible implementations through policy, authority, risk, approval, and budget gates |
| Completion | The model answers or a tool returns success | Artifacts, Evidence, Evaluation, and a Completion Gate determine whether the work is complete |
| Multi-agent | Role conversations or open-ended delegation | Frozen delegation contracts, attenuated authority, hierarchical budgets, isolated context, and supervisor-controlled Join |
| Memory | More context or long-term storage | Namespaces, immutable versions, provenance, lifecycle, and deletion propagation; memory never carries authority |
| Observability | Logs, tokens, and call traces | A semantic timeline connecting Plans, Turns, ToolCalls, Approvals, Artifacts, and Evidence |

## Differentiating capabilities

- **One kernel, two execution strengths** — everyday work uses a lightweight quick agent loop; complex or high-risk work uses a versioned trusted Plan DAG, dependency-aware execution, and stricter completion verification. Both share the same tools, Workspace, artifacts, approvals, and security pipeline.
- **Plans are separate from execution facts** — the Plan Graph says what should happen, the Runtime Trace records what did happen, and Evidence records why a result should be accepted. Plans can be revised and compared without rewriting execution history.
- **Capabilities are separate from tool implementations** — agents plan for a required capability rather than binding to a specific tool. At execution time, the runtime resolves an eligible implementation and checks authority, data boundaries, approvals, budgets, and risk before effects occur.
- **Durable execution designed for failure** — events are persisted before streaming; parallel nodes coordinate through leases, attempts, heartbeats, and checkpoints. Non-idempotent operations with unknown outcomes are not silently retried.
- **Governed multi-agent execution, not an unbounded swarm** — every child works within a frozen goal, tool catalog, permission scope, data boundary, and budget. A child cannot publish the final answer or amplify its parent's authority; the root retains merge and completion ownership.
- **Auditable without exposing hidden chain of thought** — Astra presents structured decisions, tool effects, user-facing reasoning summaries, artifacts, evidence, and verification results while isolating hidden reasoning, credentials, and private scratchpads.
- **Extensibility without governance bypasses** — Skills, plugins, sandboxed workloads, schedules, and OpenAI-compatible model providers enter through the same runtime boundary instead of creating side-channel execution paths.

Astra is a strong fit for work that spans multiple steps and tools, must recover from failure, crosses permission or budget boundaries, and needs to deliver verifiable artifacts. If you only need a short-lived chatbot or a simple deterministic workflow, a lighter framework will usually be more direct.

## How it works

```text
User goal
   ↓
Task / durable Run
   ↓
Quick agent loop  OR  versioned trusted Plan DAG
   ↓
Capability resolution → policy & permission gates → tool execution
   ↓
Evidence + artifacts → evaluation → completion verification
   ↓
Answer + auditable timeline + optional memory
```

Both execution modes share the same tool, workspace, approval, artifact, and security pipeline. Trusted mode adds canonical planning, plan revisions, dependency-aware execution, and stricter completion verification; it does not guarantee that model conclusions are infallible.

## Quick start

### Install a release

Stable [GitHub Releases](https://github.com/tommyCheese/Astra/releases) include a Compose bundle, SHA-256 checksums, an SPDX SBOM, and provenance-attested `linux/amd64` and `linux/arm64` images.

```bash
tar -xzf astra-v0.1.0.tar.gz
cd astra-v0.1.0
./install.sh
```

Open <http://127.0.0.1:8080>. The default installation binds only to localhost and uses a deterministic mock model, so it can be verified without an API key.

### Run from source

Prerequisites: Python 3.10+ and Node.js/npm.

```bash
git clone https://github.com/tommyCheese/Astra.git
cd Astra
./start.sh
```

On Windows, run `start.bat`. The first start installs missing dependencies, runs database migrations, and creates a local mock configuration when `backend/.env` does not exist.

The frontend is available at <http://localhost:5173> and proxies API requests to the backend at <http://localhost:8000/api>.

## Use a real model

Astra starts with the mock provider:

```dotenv
MODEL_PROVIDER=mock
MODEL_NAME=mock-web-query
```

To connect an OpenAI-compatible API, update `backend/.env`:

```dotenv
MODEL_PROVIDER=openai
MODEL_NAME=<model-name>
MODEL_API_KEY=<your-api-key>
MODEL_BASE_URL=https://api.openai.com/v1
```

Tool availability is configured dynamically in Settings → Tools or through the
identity-based `/api/tools/{tool_name}/state` and `/api/tool-providers/{provider_id}/state`
APIs. Fixed `TOOL_<NAME>_ENABLED` environment fields are no longer supported.

Chart execution is disabled unless enabled explicitly and runs through Docker rather than inside the API process. Review the runtime security boundary before exposing Astra beyond a trusted local environment.

## Architecture

| Layer | Responsibilities |
| --- | --- |
| React + TypeScript frontend | Chat, streaming run state, plan graph, artifacts, memory, Skills, schedules, and audit views |
| FastAPI backend | API, model clients, planning, run lifecycle, persistence, scheduling, and streaming events |
| Agent runtime | Capability resolution, policy, permissions, approvals, reflection, completion gates, and subagent supervision |
| Tool and sandbox layer | Web operations, file/artifact workflows, charts, isolated computation, and plugin-provided capabilities |
| Persistence | SQLite for a single local backend process; PostgreSQL for multi-replica deployments |

## Documentation

- [Documentation center](docs/README.md)
- [System design](docs/astra-system-detailed-design.md)
- [Trusted execution graph](docs/trusted-execution-graph.md)
- [Governed subagent runtime](docs/governed-subagent-runtime.md)
- [Agent Skills](docs/agent-skills.md)
- [Deep memory, AutoDream, and agent evolution](docs/deep-memory-autodream-evolution.md)
- [Token usage and performance](docs/token-performance.md)
- [Release guide](docs/releasing.md)
- [Deployment guide](deploy/README.md)

Most engineering documentation is currently written in Simplified Chinese. Contributions that improve or translate it are welcome.

## Development

Run the backend test suite:

```bash
cd backend
pip install -e ".[dev]"
pytest -q
```

Run frontend checks:

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

Measure end-to-end answer latency or compare quick and trusted modes with paired cases:

```bash
cd backend
python -m benchmarks.qa_latency --runs 20 --warmup 2
python -m benchmarks.mode_performance --runs-per-case 3 --warmup 1
```

The paired benchmark reads provider-reported usage and refuses to treat missing token data as zero. For an isolated, deterministic OpenAI-compatible streaming endpoint, run `python -m benchmarks.model_stub`; see [Token usage and performance](docs/token-performance.md) for the complete methodology.

## Security and deployment notes

- The Compose endpoint binds to `127.0.0.1` by default. Put an authenticated TLS reverse proxy in front of Astra before network exposure.
- The Docker socket grants host-level Docker control. Mount it only into a trusted local Astra backend and review enabled sandbox capabilities.
- The bundled SQLite setup supports one backend process. Configure PostgreSQL before running multiple backend replicas.
- Back up the deployment data directory before upgrades or retention-policy changes.

## License

Astra is licensed under the [Apache License 2.0](LICENSE).
