## ADDED Requirements

### Requirement: Conversation is a persisted aggregate
系统 SHALL 以 Conversation 聚合同一 `task_id` 下的全部 Run，并 SHALL 从数据库返回会话标题、置顶状态、最近活跃时间和可见消息，不得以浏览器本地历史作为权威来源。

#### Scenario: Restore conversations after reload
- **WHEN** 用户重新加载应用且后端可用
- **THEN** 系统从 Conversation API 恢复会话列表、用户标题和置顶状态

#### Scenario: Continue an existing conversation
- **WHEN** 用户在已有 Conversation 中提交追问
- **THEN** 系统在相同 Conversation 下创建新 Run 并更新会话最近活跃时间

### Requirement: User can rename a conversation
系统 SHALL 允许用户将 Conversation 重命名为裁剪后的非空标题，并 SHALL 标记该标题为用户标题，使后续自动摘要不得覆盖它。

#### Scenario: Rename succeeds
- **WHEN** 用户提交符合长度限制的非空标题
- **THEN** 系统持久化新标题并立即在所有会话列表和详情中返回该标题

#### Scenario: Rename rejects empty title
- **WHEN** 用户提交仅包含空白字符的标题
- **THEN** 系统拒绝请求并保留原标题

### Requirement: User can pin and unpin conversations
系统 SHALL 持久化 Conversation 的置顶时间，并 SHALL 将置顶 Conversation 与普通最近 Conversation 分区返回；置顶区按最近置顶时间排序，最近区按最近活跃时间排序且不得重复显示置顶项。

#### Scenario: Pin a recent conversation
- **WHEN** 用户置顶一个普通 Conversation
- **THEN** 该 Conversation 出现在独立置顶区并从最近区移除

#### Scenario: Unpin a conversation
- **WHEN** 用户取消置顶 Conversation
- **THEN** 该 Conversation 按最近活跃时间回到普通最近区

### Requirement: Conversation deletion is confirmed and complete
客户端 MUST 在提交永久删除前显示二次确认；后端 SHALL 删除 Conversation 的分享、全部 Run 及其关联执行数据，并 SHALL 清理可归属的本地 Artifact。删除完成后该 Conversation 不得再出现在列表、详情或公开分享中。

#### Scenario: User cancels confirmation
- **WHEN** 用户打开删除确认后选择取消
- **THEN** 客户端不得发送删除请求且 Conversation 保持不变

#### Scenario: User confirms deletion
- **WHEN** 用户确认永久删除一个终态 Conversation
- **THEN** 系统删除该 Conversation、关联运行数据和活动分享，并将用户导航到空白新对话

#### Scenario: Conversation is still running
- **WHEN** 用户尝试删除包含活动 Run 的 Conversation
- **THEN** 系统拒绝删除并说明必须等待执行终止或先取消运行

### Requirement: Conversation APIs use stable resource errors
Conversation 管理接口 SHALL 对不存在的资源、无效标题、活动运行冲突和数据库失败返回 Astra 统一错误信封与稳定错误码。

#### Scenario: Mutate missing conversation
- **WHEN** 客户端重命名、置顶或删除不存在的 Conversation
- **THEN** 系统返回资源不存在错误且不修改其他 Conversation
