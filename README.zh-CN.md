# Astra

**让 Agent 从“能够调用工具”走向“能够在约束下完成工作”。**

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Astra 是一个面向真实任务执行的开源 Agent Runtime。它不把 Agent 简化为“模型 + Prompt + 工具”的调用循环，而是把每个用户目标转化为一个可持续、可恢复、可审计的 Run：从上下文理解、计划、执行到证据验证，整个生命周期都由 Runtime 管理。

Astra 的通用性并不来自堆叠预设角色或固定工作流，而是来自同一套执行内核对 Web 信息、文件、沙箱计算、结构化产物、记忆、定时任务和 Subagent 的统一治理。无论任务类型如何变化，权限、预算、证据、恢复与完成标准始终是一等公民。

> Astra 当前版本为 `v0.1.0`，仍在快速演进。不同版本之间的接口和部署约定可能发生变化。

## Astra 与通用 Agent 平台有何不同

许多 Agent 平台首先解决“如何让模型调用更多工具、编排更多角色”；Astra 首先解决“如何让一次执行在失败、权限约束和结果不确定性下仍然可控”。下面的对比描述的是架构侧重点，而不是对所有同类项目的一概而论。

| 维度 | 常见通用 Agent 平台的侧重点 | Astra 的侧重点 |
| --- | --- | --- |
| 执行单元 | 一次对话、一次工作流调用 | 持久化 Run；状态、事件与检查点支持恢复和回放 |
| 计划 | 临时任务列表或内存中的编排 | 带稳定节点 ID、依赖、版本与 lineage 的 Plan DAG |
| 工具接入 | 将工具直接暴露给模型 | 计划声明能力，Runtime 再按策略、权限、风险、审批与预算解析可用实现 |
| 完成判断 | 模型给出答案或工具返回成功 | Artifact、Evidence、Evaluation 与 Completion Gate 共同判断是否完成 |
| 多 Agent | 角色对话或自由委派 | 冻结的委派契约、权限衰减、分层预算、隔离上下文与 Supervisor Join |
| 记忆 | 扩充上下文或长期存储 | 命名空间、不可变版本、来源、生命周期与删除传播；记忆始终是非授权数据 |
| 可观测性 | 日志、Token 和调用记录 | 关联 Plan、Turn、ToolCall、Approval、Artifact、Evidence 的语义时间线 |

## 核心差异化能力

- **一套内核，两种执行强度**：日常任务进入轻量快速 Agent Loop；复杂或高风险任务使用版本化可信 Plan DAG、依赖执行和更严格的完成验证。两者共享同一套工具、Workspace、Artifact、审批与安全管线。
- **计划与执行事实分离**：Plan Graph 表示“应该做什么”，Runtime Trace 表示“实际发生了什么”，Evidence 表示“结果凭什么成立”。计划可以修订和比较，执行记录不会被新计划覆盖。
- **能力与工具实现解耦**：Agent 规划所需能力，而不是绑定具体工具。Runtime 在执行时解析合规实现，并在每次影响发生前应用 Authority、数据边界、审批、预算和风险检查。
- **为失败而设计的持久执行**：事件先持久化再流式传递；并行节点使用租约、attempt、heartbeat 和 checkpoint 协调。对结果未知的非幂等操作不会被静默重试。
- **受治理的多 Agent，而不是无边界 Swarm**：子 Agent 只能在冻结目标、工具目录、权限、数据范围和预算内工作；不能发布最终答案，也不能扩大父 Agent 的 Authority。根 Agent 保留合并与完成责任。
- **可审计但不暴露隐藏思维链**：Astra 展示结构化决策、工具影响、面向用户的推理摘要、产物、证据和验证结果，同时隔离隐藏推理、凭据与私有 scratchpad。
- **可扩展但不绕过治理**：Skills、插件、沙箱任务、定时任务及 OpenAI-compatible 模型服务都接入同一 Runtime 边界，而不是形成旁路执行通道。

Astra 尤其适合跨多个步骤和工具、需要故障恢复、涉及权限或预算边界、并且必须交付可验证产物的任务。如果你只需要短生命周期聊天机器人或完全确定性的简单工作流，更轻量的框架通常会更直接。

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
