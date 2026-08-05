# Astra

**A general-purpose, AI-native agent platform for work that must actually get done.**

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Astra is an open-source agent runtime built on top of frontier language models. It turns a user goal into a durable run: understanding context, planning work, selecting tools, executing actions, validating results, and preserving an auditable history.

It is designed as a task operating system rather than a coding-only assistant. Astra can work with web information, files, sandboxed computation, structured artifacts, memory, scheduled tasks, and governed subagents while keeping permissions and evidence visible.

> Astra is currently at `v0.1.0` and evolving quickly. Interfaces and deployment contracts may change between releases.

## Why Astra

- **Quick and trusted execution** — use a lightweight agent loop for everyday work, or create a versioned Plan DAG with stricter verification and completion gates.
- **Capability-driven tools** — plans describe what the task needs; the runtime resolves an eligible implementation at execution time and applies policy, permission, risk, approval, and budget checks.
- **Traceable by design** — turns, tool calls, artifacts, evidence, plan revisions, and verification results are persisted as part of the run timeline.
- **Governed memory and identity** — Agent Profile instructions are separated from user memory, runtime permissions, and tool authority. Run snapshots keep historical behavior reproducible.
- **Safe delegation** — subagents receive bounded goals, budgets, permissions, and isolated execution contexts, with supervisor-controlled join, cancellation, and recovery.
- **Extensible runtime** — use built-in web and chart capabilities, sandboxed workloads, Skills, plugins, scheduled tasks, and OpenAI-compatible model providers.

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
