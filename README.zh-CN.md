# Astra

**一个为“真正完成工作”而构建的 AI 原生通用 Agent 平台。**

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Astra 是一个构建在前沿大模型之上的开源 Agent Runtime。它把用户目标转化为可持续、可恢复的 Run：理解上下文、规划任务、选择工具、执行操作、验证结果，并保留完整的可审计历史。

Astra 的定位不是只会编程的聊天助手，而是一层通用任务操作系统。它可以处理 Web 信息、文件、沙箱计算、结构化产物、记忆、定时任务和受治理的 Subagent，同时让权限边界与证据链保持可见。

> Astra 当前版本为 `v0.1.0`，仍在快速演进。不同版本之间的接口和部署约定可能发生变化。

## 为什么选择 Astra

- **快速模式与可信模式**：日常任务可直接进入轻量 Agent Loop；复杂任务可生成版本化 Plan DAG，并执行更严格的验证与完成闸门。
- **能力驱动的工具系统**：计划只描述任务需要什么能力，Runtime 在执行时解析合规实现，并依次应用策略、权限、风险、审批和预算检查。
- **原生可追溯**：Turn、工具调用、Artifact、Evidence、计划修订和验证结果都会写入 Run 时间线。
- **受治理的记忆与身份**：Agent Profile、真实用户记忆、运行权限和工具 Authority 相互分离；Run 快照让历史行为可以复现。
- **安全委派**：Subagent 拥有受限目标、预算、权限和隔离执行上下文，由 Supervisor 管理 Join、取消与恢复。
- **可扩展 Runtime**：支持内置 Web 与图表能力、沙箱任务、Skills、插件、定时任务以及 OpenAI-compatible 模型服务。

## 工作原理

```text
用户目标
   ↓
Task / 可持续 Run
   ↓
快速 Agent Loop  或  版本化可信 Plan DAG
   ↓
能力解析 → 策略与权限门控 → 工具执行
   ↓
证据与产物 → 结果评估 → 完成验证
   ↓
最终回答 + 可审计时间线 + 可选记忆
```

两种模式共享同一套工具、Workspace、审批、Artifact 与安全管线。可信模式额外提供规范计划、计划修订、按依赖执行和更严格的完成验证，但它不代表模型结论绝对正确。

## 快速开始

### 安装 Release

稳定版 [GitHub Releases](https://github.com/tommyCheese/Astra/releases) 提供 Compose 安装包、SHA-256 校验和、SPDX SBOM，以及带构建来源证明的 `linux/amd64` / `linux/arm64` 镜像。

```bash
tar -xzf astra-v0.1.0.tar.gz
cd astra-v0.1.0
./install.sh
```

打开 <http://127.0.0.1:8080>。默认安装仅监听本机地址，并使用确定性的 mock 模型，无需 API Key 即可完成验证。

### 从源码运行

环境要求：Python 3.10+ 与 Node.js/npm。

```bash
git clone https://github.com/tommyCheese/Astra.git
cd Astra
./start.sh
```

Windows 用户可运行 `start.bat`。首次启动会安装缺失依赖、执行数据库迁移，并在不存在 `backend/.env` 时创建本地 mock 配置。

前端地址为 <http://localhost:5173>，并将 API 请求代理到 <http://localhost:8000/api>。

## 接入真实模型

Astra 默认使用 mock provider：

```dotenv
MODEL_PROVIDER=mock
MODEL_NAME=mock-web-query
```

如需接入 OpenAI-compatible API，请修改 `backend/.env`：

```dotenv
MODEL_PROVIDER=openai
MODEL_NAME=<model-name>
MODEL_API_KEY=<your-api-key>
MODEL_BASE_URL=https://api.openai.com/v1
```

工具能力通过“设置 → 工具”动态配置，也可以使用按 identity 定位的
`/api/tools/{tool_name}/state` 与 `/api/tool-providers/{provider_id}/state` 接口。
固定的 `TOOL_<NAME>_ENABLED` 环境变量已不再支持。

图表执行默认关闭；显式启用后通过 Docker 运行，而不会在 API 进程内执行任意代码。将 Astra 暴露到受信任本机之外前，请先审查 Runtime 的安全边界。

## 架构概览

| 分层 | 主要职责 |
| --- | --- |
| React + TypeScript 前端 | 对话、流式 Run 状态、计划图谱、产物、记忆、Skills、定时任务和审计界面 |
| FastAPI 后端 | API、模型客户端、规划、Run 生命周期、持久化、调度和流式事件 |
| Agent Runtime | 能力解析、策略、权限、审批、反思、完成闸门和 Subagent 监管 |
| 工具与沙箱层 | Web 操作、文件与 Artifact 工作流、图表、隔离计算和插件能力 |
| 持久化 | 单后端本地部署使用 SQLite；多副本部署使用 PostgreSQL |

## 文档

- [文档中心](docs/README.md)
- [系统详细设计](docs/astra-system-detailed-design.md)
- [可信执行图谱](docs/trusted-execution-graph.md)
- [受治理的 Subagent Runtime](docs/governed-subagent-runtime.md)
- [Agent Skills](docs/agent-skills.md)
- [深度记忆、AutoDream 与 Agent 自进化](docs/deep-memory-autodream-evolution.md)
- [Token 消耗与性能](docs/token-performance.md)
- [发布指南](docs/releasing.md)
- [部署指南](deploy/README.md)

## 开发

运行后端测试：

```bash
cd backend
pip install -e ".[dev]"
pytest -q
```

运行前端检查：

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

测量端到端问答时延，或使用配对 Case 比较快速模式与可信模式：

```bash
cd backend
python -m benchmarks.qa_latency --runs 20 --warmup 2
python -m benchmarks.mode_performance --runs-per-case 3 --warmup 1
```

配对基准会读取供应商上报的 usage，并拒绝把缺失的 Token 数据视为零。如需隔离供应商网络和推理波动，可运行 `python -m benchmarks.model_stub` 启动确定性的 OpenAI-compatible 流式端点；完整方法见 [Token 消耗与性能](docs/token-performance.md)。

## 安全与部署提示

- Compose 默认仅绑定 `127.0.0.1`。向网络开放前，请配置带身份认证的 TLS 反向代理。
- Docker socket 等同于宿主级 Docker 管理权限。只应挂载到受信任的本机 Astra 后端，并审查已启用的沙箱能力。
- 内置 SQLite 配置仅支持一个后端进程。运行多个后端副本前请配置 PostgreSQL。
- 升级或修改数据保留策略前，请备份部署数据目录。

## 许可证

Astra 使用 [Apache License 2.0](LICENSE) 开源。
