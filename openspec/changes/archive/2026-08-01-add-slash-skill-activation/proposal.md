## Why

Astra 当前把 eligible Skill 暴露给模型自动选择，并把显式选择藏在“添加内容”菜单中；相关 Skill 可能被模型直接跳过，用户也难以确认下一条消息究竟绑定了哪个 Skill。需要提供类似 Codex 的斜杠选择体验，并把用户选择提升为宿主侧确定性激活，而不是继续依赖模型判断。

## What Changes

- 在聊天 Composer 的命令边界输入 `/` 时打开可搜索、可键盘操作的 Skill 命令面板，列出当前已启用且有 active Published Revision 的 built-in 与 custom Skills。
- 允许用户通过鼠标或键盘选择一个或多个 Skill；选择结果以明显的高亮 Skill token 展示在 Composer 中，并可单独移除。
- 斜杠查询文本只用于选择，不进入用户消息正文；关闭面板或没有选择时恢复普通文本输入语义。
- 发送消息时把高亮 token 对应的 qualified identities 作为现有 `skill_ids` 提交。宿主必须在首次模型操作前验证冻结 Catalog 并预激活这些 Skill，模型不得跳过或替换用户的显式选择。
- Run 创建成功后清除本次消息的 Skill token；创建失败时保留选择，便于用户修正后重试。
- 为选择、预激活失败、不可用或 revision 变化提供可访问的状态和错误反馈，并补充前后端行为测试。
- 本次不实现通用 Skill `activation`/`output_contract` 扩展、输出校验器或自动语义路由；这些属于后续独立变更。

## Capabilities

### New Capabilities

- `explicit-skill-activation`: 定义斜杠选择形成的显式 Skill 绑定、Run 请求传递、冻结 Catalog 校验、首次模型操作前的确定性预激活及失败语义。

### Modified Capabilities

- `agent-chat-ui`: 扩展 Chat Composer，使其提供 `/` Skill 命令面板、高亮选择 token、键盘与无障碍交互以及一次性提交状态。

## Impact

- 前端：`App.tsx` Composer 输入与菜单状态、Skill 选择模型、提交生命周期、焦点/键盘交互、响应式和深浅色样式。
- API：复用现有 Run 创建请求中的 `skill_ids`，不引入破坏性请求字段；需要固定顺序、去重和 qualified identity 校验。
- 后端：复用冻结 Skill Catalog 与 explicit pre-activation 管线，补强首次模型操作前已激活的合同与审计测试。
- 测试：增加 slash 查询、键盘选择/移除、高亮状态、正文清理、成功/失败提交生命周期和确定性预激活的覆盖。
- 依赖：建立在 `add-governed-agent-skills-system` 已提供的 Catalog、Published Revision、`skill_ids` 与 Run Skill snapshot 之上，不增加第三方运行时依赖。
