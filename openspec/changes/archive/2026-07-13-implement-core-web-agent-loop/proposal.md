## Why

Astra 当前已经能执行一条可审计的 Web 数据查询流水线，但 runner 仍然是固定流程，不具备通用 Agent 的循环决策、失败反思、记忆召回和持续对话体验。现在需要把现有 Web 工具能力提升为第一版核心 Agent loop，为后续接入更多工具奠定稳定内核。

同时，前端仍像任务控制台。面向真实用户的 Agent 入口应演进为聊天式前台，让用户能像使用 Gemini 一样提交目标、查看 Agent 思考摘要、工具进展、来源证据和最终回复，同时保留可展开的审计细节。

## What Changes

- 新增 Web-only Agent loop：以 `web_search` / `web_fetch` 为首批工具，在受控循环中执行 plan、act、observe、reflect、verify、finalize。
- 新增结构化 ReAct 决策：模型输出可审计的 reasoning summary、下一步动作、工具调用参数、预期观察和停止条件，不保存完整隐藏思维链。
- 新增反思机制：工具失败、低质量来源、证据不足或验证失败时，Agent 生成结构化反思并决定重试、换查询、继续抓取、重新计划、阻塞或结束。
- 新增 Memory 管理基础：支持 run memory、workspace memory、user memory 的结构化读写；持久记忆必须有 provenance、confidence 和 scope。
- 将现有 Web 查询专用流程迁移到通用 Agent loop 之上；保留现有 ToolRegistry、ToolCall、Artifact、Evidence Pack 和验证报告能力。
- 前端改造为聊天式 Agent UI：用户消息、Agent 回复、工具事件、来源证据、反思、Memory 写入和验证结果以对话流呈现；保留可展开 timeline/audit 视图。
- 不引入文件、shell、git、浏览器控制等更高风险工具；第一版只开放现有基础 Web 工具。
- 不实现无限自主后台任务；每次运行仍由用户目标触发，并受最大轮数、最大工具调用数和权限边界限制。

## Capabilities

### New Capabilities

- `web-agent-loop`: Web-only Agent loop 的状态机、循环控制、工具路由、终止条件和验证要求。
- `react-reflection`: 结构化 ReAct 决策、观察记录、失败反思、重试/改写/阻塞决策。
- `memory-management`: run/workspace/user memory 的召回、写入、provenance、confidence、过期和审计规则。
- `agent-chat-ui`: Gemini 风格聊天式 Agent 前台、工具事件展示、来源证据、反思和审计抽屉。

### Modified Capabilities

- None.

## Impact

- Backend runner: 新增通用 Agent loop，并将现有 Web 数据查询流程适配到循环执行模式。
- Backend model client: 增加结构化 Agent decision、reflection、memory extraction、verification report 相关接口。
- Backend persistence: 增加 Memory 和 Agent turn/observation/reflection 所需持久化结构或 artifact/event 表达。
- Tool runtime: 继续使用现有 ToolRegistry；第一版只注册并允许 `web_search` / `web_fetch`。
- API: Run 视图需要暴露聊天消息、Agent turns、memory events、工具事件和最终回复。
- Frontend: 从 dashboard 双栏视图演进为聊天窗口主视图，并保留可展开审计能力。
- Tests: 增加 Agent loop、反思、memory、tool gating、聊天 UI 和端到端 mock run 的确定性测试。
