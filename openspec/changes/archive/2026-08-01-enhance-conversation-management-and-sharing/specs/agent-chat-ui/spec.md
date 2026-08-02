## ADDED Requirements

### Requirement: Sidebar separates pinned and recent conversations
系统 SHALL 在侧栏将置顶 Conversation 显示在独立“置顶”区，将其他 Conversation 显示在“最近”区；置顶区为空时 SHALL 隐藏整个分区。

#### Scenario: Sidebar has pinned conversations
- **WHEN** 后端返回一个或多个置顶 Conversation
- **THEN** 侧栏在最近列表上方显示独立置顶区且 Conversation 不重复出现

#### Scenario: Sidebar has no pinned conversations
- **WHEN** 后端没有返回置顶 Conversation
- **THEN** 侧栏不显示空的置顶标题或占位内容

### Requirement: Conversation actions are available from an item menu
每个 Conversation 项 SHALL 在 hover 或键盘聚焦时提供操作菜单，菜单 SHALL 包含重命名、置顶或取消置顶、分享和删除；菜单操作不得误触发会话切换。

#### Scenario: Open conversation menu
- **WHEN** 用户点击 Conversation 行的更多操作按钮
- **THEN** UI 显示与该 Conversation 关联的操作菜单并保持当前会话选择不变

### Requirement: Destructive and text-editing actions use accessible dialogs
重命名、分享和删除 SHALL 使用支持键盘焦点与 Escape 关闭的对话框；删除对话框 MUST 明确说明不可撤销并要求用户再次确认。

#### Scenario: Confirm permanent deletion
- **WHEN** 用户从会话菜单选择删除
- **THEN** UI 显示包含会话标题、不可撤销说明、取消按钮和危险样式永久删除按钮的确认框

#### Scenario: Rename with keyboard
- **WHEN** 用户打开重命名对话框并输入有效标题后按 Enter
- **THEN** UI 保存标题、关闭对话框并更新侧栏显示

### Requirement: Share dialog exposes snapshot lifecycle
分享对话框 SHALL 显示公开范围提示、当前分享链接和复制操作；已有分享时 SHALL 提供更新快照与停止分享操作。

#### Scenario: Share is created
- **WHEN** 用户首次确认创建分享
- **THEN** UI 显示可复制链接并提示任何获得链接的人均可查看该只读快照

#### Scenario: Existing share is managed
- **WHEN** 用户打开已有分享的分享对话框
- **THEN** UI 显示原链接、快照更新时间、更新分享和停止分享操作
