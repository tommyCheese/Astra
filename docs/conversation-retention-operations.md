# 历史对话老化运维

Astra 后端可以周期性硬删除超过保留期的历史对话及其归属数据。该能力默认关闭，升级不会自动删除已有记录；启用前应先确认数据库与文件目录已有可恢复备份，并已运行 `alembic upgrade head` 创建候选扫描索引。

## 配置

```text
CONVERSATION_RETENTION_ENABLED=false
CONVERSATION_RETENTION_DAYS=180
CONVERSATION_RETENTION_SWEEP_SECONDS=86400
CONVERSATION_RETENTION_BATCH_SIZE=100
```

- `ENABLED`：唯一启用开关。只有 `true` 才会选择和删除候选。
- `DAYS`：按 Conversation 的 `updated_at` 计算最后活动期限，允许 1–36500 天。
- `SWEEP_SECONDS`：两次后台扫描的间隔，允许 60–2592000 秒。
- `BATCH_SIZE`：一次最多处理的对话数量，允许 1–1000。

启用时，后端启动阶段先执行一个有上限的批次，随后按间隔继续扫描。积压量大于批量上限时会在后续周期逐批清理，不会在一次扫描中无限循环。

## 保护与删除规则

对话同时满足以下条件才会老化：

- `updated_at` 已到保留截止时间；
- 至少包含一个 Run；
- 所有 Run 都处于 `completed`、`completed_with_warnings`、`blocked`、`failed` 或 `cancelled`；
- 没有置顶；
- 没有处于 active 状态的分享。

`waiting_user`、运行中或其他非终态 Run 会保护整个对话。重命名、切换回答模式、置顶变化和新建 Run 都会刷新 `updated_at`。候选在实际删除前会重新查询和校验，避免扫描后状态变化导致误删。

硬删除会移除 Conversation、Runs、执行与审计记录、Memory、审批、分享、Artifact 数据库记录和 Task Workspace 数据库记录；随后尽力删除 Artifact 文件及 Workspace 目录。外部文件清理失败不会回滚已经提交的数据库删除，但会产生 warning 日志。

## 可观测性

启用前应先在测试环境使用较长保留期和较小批量。关注以下结构化日志：

- `conversation_retention.disabled`：策略未启用。
- `conversation_retention.sweep_complete`：包含 `trigger`、`selected`、`deleted`、`skipped`、`failed`。
- `conversation_retention.delete_failed`：单个候选失败；同批后续候选仍会继续。
- `conversation.artifact_cleanup_failed`、`conversation.workspace_cleanup_failed`：数据库已删除，但外部内容清理失败。

当前 Release Compose 以单 backend 进程运行。自定义多 worker 部署不应在每个 worker 同时启用保留任务；引入数据库调度租约前，应只让一个实例负责扫描。

## 启用与回滚

1. 备份数据库、Artifact 目录和 Workspace 目录。
2. 先设置保守的 `DAYS` 与较小的 `BATCH_SIZE`。
3. 设置 `CONVERSATION_RETENTION_ENABLED=true` 并重启 backend。
4. 检查首个 `sweep_complete` 的数量和 cleanup warning。
5. 需要停止时将开关改回 `false` 并重启。

关闭策略只会停止后续删除；已硬删除的数据只能从基础设施备份恢复。本机制不清理数据库或文件系统的历史备份副本。
