## Why

Astra 需要一个很薄但真实的纵向切片，用来证明通用 Agent 平台的核心承诺：接收用户目标，规划工作，调用工具，持久化执行证据，验证结果，并报告实际发生了什么。从基于 Web 的数据查询任务开始，可以验证“通用任务”方向，同时避免把产品过早收窄成只会写代码的助手。

## What Changes

- 增加一个由 Python 后端支撑的 Web App 流程，用于创建和运行通用数据查询任务。
- 增加持久化的任务运行生命周期，记录 Task、Run、Step、ToolCall、Artifact 和最终结果。
- 通过可配置的模型客户端接入真实模型，并为计划和最终答案使用结构化输出。
- 增加初始网络读取工具 `web_search` 和 `web_fetch`，让 Agent 能收集外部来源材料。
- 在 Web App 中通过 server-sent events 展示实时运行时间线。
- 增加带证据的结果报告，包含来源、限制说明和验证备注。
- 第一条切片保持刻意收窄：不包含多 Agent 编排、长期记忆、生产部署自动化或团队认证。

## Capabilities

### New Capabilities
- `task-runner`：持久化任务和运行编排，覆盖规划、执行工具支撑的步骤、跟踪状态，以及报告带证据的结果。
- `web-data-query`：通用 Web 数据查询工作流，使用模型规划以及 Web 搜索/抓取工具来收集、综合并引用外部信息。

### Modified Capabilities
- 无。

## Impact

- 引入 Python 后端，预计使用 FastAPI、SQLAlchemy、Alembic、PostgreSQL 和 Pydantic settings。
- 引入 Web App 前端，预计使用 React、TypeScript 和 Vite。
- 增加模型提供方配置，通过用户提供的 API Key 调用真实 API。
- 增加 Web 搜索/抓取操作的网络读取工具执行和审计记录。
- 为 Astra 的运行、时间线事件、工具调用、产物和最终报告建立第一套持久化数据模型和 API 表面。
