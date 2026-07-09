# Astra

Astra 是一个 AI 原生的通用 Agent 平台。

我们的目标不是复制一个只会写代码的聊天助手，而是在前沿大模型之上构建一层通用任务操作系统：它能够理解用户目标、工作空间、知识环境和外部系统，规划可持续执行的任务，通过工具完成真实操作，验证结果，并随着时间沉淀长期记忆。

从这里开始：

- [Astra Agent Platform v0.1](docs/astra-agent-platform-v0.1.md)

## 第一条纵向切片

当前实现方向是 `implement-web-data-query-task-runner`：一个由 Python 后端支撑的 Web App，用真实模型接口和 `web_search` / `web_fetch` 工具运行通用 Web 数据查询任务。

核心闭环：

```text
用户目标 -> 创建 Task/Run -> 模型规划 -> 工具执行 -> 结果综合 -> 证据验证 -> 时间线报告
```

### 本地开发

后端：

```bash
cd backend
cp .env.example .env
# 配置 DATABASE_URL；默认模型 provider 为 mock，可先不填 MODEL_API_KEY
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

默认前端运行在 `http://localhost:5173`，通过 Vite proxy 访问 `http://localhost:8000/api`。

### 真实模型配置

`.env` 中默认使用 mock provider，便于本地确定性验证：

```text
MODEL_PROVIDER=mock
MODEL_NAME=mock-web-query
```

接入真实 OpenAI-compatible API 时，配置：

```text
MODEL_PROVIDER=openai
MODEL_NAME=<model-name>
MODEL_API_KEY=<your-api-key>
MODEL_BASE_URL=https://api.openai.com/v1
```

### Web 搜索/抓取配置

`web_fetch` 支持直接抓取 URL；`web_search` 默认使用 mock provider。接入 Brave Search 时配置：

```text
WEB_SEARCH_PROVIDER=brave
WEB_SEARCH_API_KEY=<your-search-api-key>
```

网络读取可以通过 `ALLOW_NETWORK_READ=false` 关闭。
