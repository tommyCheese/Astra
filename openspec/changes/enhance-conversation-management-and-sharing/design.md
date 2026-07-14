## Context

Astra 当前以 `TaskRecord` 表示一个连续目标，以相同 `task_id` 下的多个 `RunRecord` 表示追问轮次，但产品 API 仅暴露 Run，前端需要请求最近 Run 后自行分组并将聚合结果写入 `localStorage`。`TaskRecord.title` 已具备默认标题基础，却没有手动标题标记、置顶时间或专用会话 API。Run 下还关联步骤、工具调用、工件、事件、AgentTurn、Memory、SandboxJob 和模型调用记录；分享若复用 `RunView` 将暴露大量内部审计与本地资源信息。

本次变更跨越数据库、Repository、API、前端状态和公开页面，同时涉及不可逆删除与匿名公开访问，必须显式定义资源边界和安全投影。

## Goals / Non-Goals

**Goals:**

- 将现有 Task/Run 数据稳定投影为一等 Conversation 资源，并以数据库为会话元数据权威来源。
- 提供可测试、幂等的重命名、置顶、取消置顶、完整删除和分享管理语义。
- 为公开分享建立独立、最小化、不可执行的快照 DTO。
- 在不破坏现有 Run 创建、恢复和 SSE 流程的前提下替换侧栏历史数据源。
- 删除会话时停止活动执行、撤销分享并清理数据库和本地工件。

**Non-Goals:**

- 不增加账号、团队、ACL、密码保护或链接到期时间。
- 不支持分享访问者继续原对话、复制到个人历史或评论协作。
- 不支持单个会话创建多个并行活动分享链接。
- 不公开推理过程、工具原始输入输出、Memory、Agent Profile、凭据或可直接访问的本地 Artifact。
- 不在本次变更中增加归档、文件夹或项目分组。

## Decisions

### 1. 复用 Task 作为 Conversation 聚合根

数据库继续使用 `tasks` 表，增加 `title_source` 和可空的 `pinned_at`；API 和前端使用 Conversation 命名。每次在该 Task 下创建新 Run 时更新 Task 的 `updated_at`，列表按置顶分区和最近活跃时间排序。

选择复用 Task 是因为当前追问已经通过 `task_id` 建立稳定身份，另建 Conversation 表会产生一对一同步和迁移复杂度。备选方案是让最新 Run 承载标题和置顶状态，但这会把跨轮次元数据错误绑定到单次执行，并使删除和排序更脆弱。

### 2. 使用专用 Conversation API 和轻量 DTO

`GET /api/conversations` 返回摘要，不携带完整执行审计；`GET /api/conversations/{id}` 返回按时间顺序组合的可见聊天消息与各 Run 快照，以兼容现有渲染。修改和删除均以 Conversation ID 即 `task_id` 为资源标识。

前端不再依赖 `listRuns()` 重建历史。已有 `localStorage` 历史只在后端不可用时作为当前版本的兼容回退，不再覆盖后端返回的标题、置顶和顺序。

### 3. 用户标题覆盖自动标题

新 Task 默认使用首条目标作为标题并标记 `title_source=auto`。重命名写入裁剪后的非空标题并标记 `user`；后续 Run 摘要或追问不得修改用户标题。

### 4. 每个 Conversation 一个活动分享资源

新增 `conversation_shares` 表，以 `conversation_id` 唯一关联原会话，保存高熵随机 token 的 SHA-256 hash、JSON 快照、状态和时间戳。创建分享时若已有活动分享则返回现有 URL；显式更新分享时重建快照但保持 token；撤销后再次分享生成新 token，旧链接永久失效。

只存 token hash 可避免数据库泄漏直接产生可用公开链接。API 创建时仅返回一次原始 token；为支持再次复制现有链接，服务端同时保存加密不可用的方案不合适，因此本地单用户版本改为保存随机 token 本身并对查询列建立唯一索引，后续认证化时再迁移到“token 前缀定位 + hash 校验”。这是当前可用性与威胁模型之间的明确折中。

### 5. 分享采用完成消息快照和公开 DTO

快照在创建或更新时，从 Conversation 的所有 Run 中按顺序提取已完成的 `user` 与 `assistant` 消息，只保留 `role`、`content` 和顺序标识。公开响应包含标题、消息及分享更新时间，不返回 Conversation ID、Run ID、状态机、内部 metadata 或 Artifact content URL。

采用快照而非实时读取可以避免分享后新增内容被无意公开，并使安全过滤发生在明确的用户动作上。原会话删除或分享撤销后，公开读取立即返回不存在。

### 6. 删除由服务层编排并使用 ORM/显式顺序保证一致性

删除前拒绝仍处于执行状态的 Conversation，避免后台任务继续写入已删除记录。服务先收集本地 Artifact 路径，在数据库事务内删除分享及 Run 关联记录，最后删除 Task；事务成功后尽力删除物理文件。数据库迁移为拥有明确父子关系的外键补充级联删除，服务层仍负责运行状态检查和磁盘清理。

若磁盘清理失败，数据库删除不回滚，但记录告警；孤立文件比恢复已经向用户宣称删除的对话更可控，后续可由清理任务回收。

### 7. 前端以 pathname 分流公开页面

当前前端没有路由依赖。入口根据 `/share/<token>` 渲染独立 `SharedConversationPage`，其他路径渲染主 `App`，避免为两个静态路由引入额外依赖。分享页不渲染侧栏、Composer、设置、审计面板或任何继续执行入口。

### 8. 会话菜单和对话框集中在 Sidebar 上层状态管理

侧栏将 Conversation 分为“置顶”和“最近”，空置顶区隐藏。每项 hover/focus 显示 `⋯`，菜单支持重命名、置顶/取消置顶、分享和删除。重命名、分享与删除使用可聚焦且支持 Escape 的 modal；删除必须二次确认，危险按钮明确标记永久删除。

## Risks / Trade-offs

- [Risk] 现有数据库没有完整级联策略，永久删除可能遗留关联行 → 增加迁移、Repository 测试和完整关系清单，删除操作统一经过服务层。
- [Risk] 活动 Run 与删除发生竞态 → 活动状态下拒绝删除；未来若加入取消能力，可先取消并等待终态后删除。
- [Risk] 公开快照意外包含内部 metadata → 使用独立 schema 白名单构造，不序列化或裁剪 `RunView`。
- [Risk] token 可复制意味着数据库读取者能获得公开链接 → 当前单用户本地部署接受该折中，使用至少 256 bit 随机 token；认证化部署前改为 hash 校验或加密存储。
- [Risk] 数据库提交后物理文件删除失败 → 记录告警并允许后续孤儿文件清理，不恢复已经删除的会话。
- [Risk] 现有 localStorage 可能显示已删除会话 → 后端会话列表成功加载后完全替换本地列表，并同步更新缓存。

## Migration Plan

1. 增加 Task 会话元数据列和 ConversationShare 表；现有 Task 回填 `title_source=auto`，`pinned_at=NULL`。
2. 部署后端 Conversation/Share API，并保持旧 Run API 可用。
3. 部署前端专用会话列表和公开分享页；首次成功加载后端列表后覆盖旧 localStorage 缓存。
4. 验证重命名、置顶、删除、分享撤销、原会话删除联动及敏感字段不出现在公开响应。
5. 回滚时前端恢复旧 Run 聚合；新列和分享表可暂时保留，避免破坏已生成迁移的数据。

## Open Questions

- 当前默认匿名分享适合本地与个人部署；未来引入账号后，需要补充所有者、工作区策略和管理员禁用能力。
- 当前删除活动会话采用拒绝策略；待 `add-user-run-cancellation` change 完成后，可升级为“先取消、确认终止、再删除”。
