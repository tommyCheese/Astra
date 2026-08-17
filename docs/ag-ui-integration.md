# Astra AG-UI 集成说明

## 支持的 Profile

Astra 公共协议 profile 为 `astra-ag-ui-v1`，固定使用 `@ag-ui/core` 与 `@ag-ui/client` `0.0.57`。共享兼容性清单位于 `contracts/ag-ui/profile-v1.json`。AG-UI 是 Astra `RunEvent` 与权威 Run 快照的公开投影，不替代 Runtime、权限、审计或持久化模型。

HTTP/SSE 入口为 `POST /api/ag-ui`，能力发现为 `GET /api/ag-ui/capabilities`。每条新连接首先收到 `RUN_STARTED`、`STATE_SNAPSHOT` 和 `MESSAGES_SNAPSHOT`；文本、Reasoning、工具和 Activity 随后渐进发送。React 收到第一个可显示事件后立即提交，不等待 `RUN_FINISHED`。

运行指标由 `GET /api/ag-ui/metrics` 提供，包括 profile、活跃流、投影错误、安全抑制、截断、patch 回退、Interrupt 结果、恢复连接、断流和显式取消计数；日志统一写入 `astra.ag_ui` logger。

## Astra 扩展

结构化 Activity 使用 schema version 1：

- `astra.plan`
- `astra.agent_tree`
- `astra.verification`
- `astra.artifact`
- `astra.tool_activity`

Activity 包含 `revision`、`sourceEventId`、`order`、`byId` 和 `fallbackText`。Delta 是 RFC 6902 patch，并携带 `baseRevision`、目标 revision 和源游标。缺失基线、游标缺口、schema/计划变化、非法路径、过大 patch 或重连都会回退到替换快照。

## 安全边界

- `RunAgentInput.tools` 永远不能注册或授权 Astra 后端工具。
- `forwardedProps.astra` 使用拒绝未知字段的版本化白名单。
- thread 必须属于当前 principal；不存在和无权访问统一返回不可发现响应。
- 公开对象会递归移除凭据、token、permission bundle、私有路径、异常栈、Workspace 内部数据和不安全 URL。
- 只有显式 reasoning summary 可公开；供应商隐藏推理和 scratchpad 被抑制。
- 先清洗完整对象，再计算 patch，敏感字段不能借增量重新出现。
- Interrupt binding 在服务端保存 continuation token；协议响应不携带该 token。

## Interrupt、恢复与取消

等待审批或用户输入时，Astra 先发送 State/Message 快照，再以 interrupt outcome 结束当前 protocol Run。恢复必须创建新的 protocol Run，并完整响应绑定在同一内部 Run 上的 Interrupt。持久化唯一约束、版本条件更新和已消费 outcome 保证重启安全与幂等。

关闭浏览器 SSE 只关闭传输，不取消 Runtime。显式取消使用能力声明中的：

`POST /api/ag-ui/runs/{runId}/cancel?threadId={threadId}`

## Feature flag 与回滚

后端默认 `AG_UI_ENABLED=false`，产品根路径始终保留现有 Astra 界面。开发者完成迁移后，可同时设置后端 `AG_UI_ENABLED=true`、前端 `VITE_AG_UI_ENABLED=true`，并访问 `/__dev/ag-ui` 检查隔离的协议预览页。该预览页不能替代产品 UI，也不是生产入口。回滚只需关闭任一开关；原生 `/api/runs/stream` 和事件端点不会删除。

## 验收门禁与证据

第一方默认切换要求：

| 门禁 | 阈值 | 证据 |
|---|---:|---|
| 协议一致性 | 所有 golden event 通过固定版本 Zod schema | `aguiCompatibility.test.ts` |
| 首内容适配开销 | 1000 个文本事件投影低于 500 ms | `test_ag_ui_performance.py` |
| SSE 单事件大小 | 普通文本事件低于 2 KiB | 性能测试 |
| 恢复 | cursor gap、缓存丢失、断线均由快照收敛 | 后端投影及前端 store 测试 |
| 安全 | nested secret、路径、URL、隐藏推理和超限结构全部被抑制 | adversarial projection tests |
| 可访问性 | Activity 有 region label；Interrupt 可使用 label、表单和键盘提交 | `aguiComponents.test.tsx` |
| 双栈一致性 | 原生 answer 内容与 AG-UI 最终 result 内容一致 | golden parity test |

协议、安全、恢复和延迟测试已经建立，但现有产品聊天壳尚未完成传输无关化迁移，因此 AG-UI 不作为第一方默认入口。开发预览只用于继续验证事件投影；待既有会话、设置、模型、Skills、计划、审批和响应式布局全部通过真实浏览器回归后，才能重新评估默认切换。原生 transport 的删除必须另开 breaking change。
