# 可信执行图谱

可信模式把执行过程分成三个互不混淆的层次：

1. **Plan Graph**：经过校验、持久化且带版本的规范 DAG，表示“应该执行什么”。
2. **Runtime Trace**：关联到稳定 PlanNode ID 的 AgentTurn、ToolCall、Reflection、Evaluation 和审批，表示“实际发生了什么”。
3. **Evidence**：节点接受的证据引用、产物和安全故障摘要，表示“结果凭什么成立”。

图谱不展示模型隐藏的思维链。界面只展示面向用户的推理摘要、结构化决策、工具记录和验证结果。节点位置、缩放和平移都是本地视图状态，不能改变计划语义。

Standard（快速响应）运行不创建占位 Plan、节点或边；以下协议只适用于 trusted Run。

## 快照与版本 API

`GET /api/runs/{run_id}` 的 `plan_graph` 是当前规范快照，`plan_versions` 是轻量版本索引。当前快照 schema version 为 `2`（读取端仍能迁移 version 1），包含稳定节点 ID、显式边、依赖类型、版本 lineage、时间戳、预期结果、证据、安全故障信息、活动 NodeExecution 与并发槽摘要。`nodes[].depends_on` 只作为兼容投影保留，规范拓扑来源是 `edges`。

| API | 用途 |
|---|---|
| `GET /api/runs/{run_id}/plans` | 列出不可变 Plan 版本摘要 |
| `GET /api/runs/{run_id}/plans/{version}` | 懒加载指定版本完整快照 |
| `GET /api/runs/{run_id}/plans/{version}/diff?from_version=N` | 按 lineage 比较较早版本与目标版本 |

差异匹配只使用稳定 ID 与 `lineage_node_id`，不会用标题猜测节点身份。没有 lineage 的同名节点会按删除加新增处理。

## 实时事件

图谱事件与其他 RunEvent 一样先持久化，再经 `GET /api/runs/{run_id}/events` 按事件 ID 回放：

- `plan.graph.snapshot`
- `plan.version.created`
- `plan.version.activated`
- `plan.node.updated`
- `plan.nodes.claimed`
- `plan.node.execution_started`
- `plan.node.waiting_resource`
- `plan.node.waiting_approval`
- `plan.node.execution_result_recorded`
- `plan.node.execution_completed`
- `plan.node.execution_failed`
- `plan.node.execution_cancelled`
- `plan.parallelism.changed`
- `plan.revision.started`
- `plan.revision.completed`
- `plan.revision.rejected`

每个 execution 事件携带 Plan version、PlanNode、attempt、dispatch batch 与 execution ID。客户端只把节点增量应用到同一 Plan 版本和当前 attempt，并在一个动画帧内合并可见更新；版本缺口、未知节点或不一致 payload 会触发权威快照刷新。事件不包含用户修订原文、模型隐藏推理、凭据、原始敏感工具输入或宿主机路径。

## 并行执行与恢复

Coordinator 按依赖层级、节点 index 和稳定 ID 确定性认领 ready 节点。默认上限为 3；Run 策略、服务端硬上限、provider/capability 配额和预算共同决定实际槽位。Worker 只执行一个 NodeExecution attempt，使用不可变节点上下文与独立数据库 session，结果由 Coordinator 通过 Plan version、attempt 和状态版本校验后合并。

只读租约可共享；同一或祖先/子路径写冲突、未知资源与非幂等外部写会串行化。对外快照只返回哈希化资源类别摘要。分支失败仅阻断必要后继；等待审批释放普通槽位；取消会终止全部活动 execution、释放租约并结算预留。

进程恢复依赖持久化 heartbeat、checkpoint、结果、租约和预算，而非原协程。已记录结果的 attempt 从 committing 阶段重放提交；安全幂等 attempt 可继续；非幂等未知结果进入 `result_unknown`。将 `AGENT_PARALLEL_EXECUTION_ENABLED=false` 可把新调度降到单槽，作为无需迁移数据的回滚方式。

## 执行前调整计划

等待 `plan_confirmation` 时，客户端可向 `POST /api/runs/{run_id}/resume` 提交：

```json
{
  "action": "revise_plan",
  "content": "将资料搜索拆成两个并行分支，并在汇总前增加来源核验。",
  "continuation_token": "...",
  "plan_id": "...",
  "expected_plan_version": 1,
  "expected_state_version": 1
}
```

服务端一次性领取当前 token，生成并完整校验替代 DAG。成功时创建 `vN+1`、记录 supersedes/lineage，并以新 token 返回 `plan_confirmation`；不会执行任何节点，也不会批准之后的工具影响。非法 DAG 会被拒绝，原版本保持不变，并签发可继续使用的新等待 token。过期或重放请求返回状态冲突且不创建版本。

确认执行仍使用同一路由，但 `action` 为 `execute_plan`，并必须精确绑定新版本的 token、Plan ID、Plan version 和 state version。
