## Context

Astra 已有 eligible Skill 列表、Composer `selectedSkillIds` 状态、Run 创建请求中的 `skill_ids`、冻结 Run Skill Catalog 和 explicit pre-activation。现有入口位于“添加内容”浮层，选中状态只在菜单副文案中显示；用户离开菜单后无法确认绑定，而没有显式选择时模型可以直接 `finalize`，跳过即使描述高度匹配的 Skill。

本变更不改变 Agent Skills 的三级渐进式披露。它新增一个类似 Codex 的显式命令入口，并把 slash 选择定义为本次消息的结构化 Run 配置，而不是普通提示文本。

`activation` 与 `output_contract` 是两个不同的未来扩展点：前者控制 Skill 如何成为 active，后者控制 active Skill 的可机器验证输出约束。本次只落实“slash 显式选择等价于 required activation”，不修改可移植 Skill 包格式，也不实现通用输出校验。

## Goals / Non-Goals

**Goals:**

- 用户在 Composer 的命令边界输入 `/` 后可搜索并选择 eligible Skills。
- 选择结果在菜单关闭后仍以高对比度 token 明确可见，可通过键盘和指针移除。
- slash 查询字符不污染用户消息，Skill qualified identity 只通过 `skill_ids` 传输。
- 用户显式选择的每个 Skill 都在首次模型操作前按冻结 revision 确定性预激活。
- Run 创建成功后消费本次选择；失败时保留文本和 Skill token。
- 保持现有快速响应、可信执行、权限、审批和 Sandbox 语义。

**Non-Goals:**

- 不新增通用 `/help`、`/model`、`/clear` 等命令。
- 不实现自然语言或 embedding 自动路由，也不改变模型自主 `activate_skill` 的兜底能力。
- 不实现 `activation` 或 `output_contract` frontmatter schema、validator DSL、输出重试或后处理。
- 不把 `/skill-name` 作为用户消息正文发送给模型。
- 不改变 Skill 发布、版本选择、权限授予或 answer mode。

## Decisions

### 1. Slash 是 Composer 命令手势，Skill identity 保持结构化

只有位于文本开头或空白字符之后、且 caret 位于该 token 内时，`/` 才打开 Skill 面板。`/` 到下一个空白字符或 caret 之间的内容作为查询，匹配 Skill name、description 和 qualified identity。路径、URL 或单词中间的 `/` 不触发命令面板。

选择 option 时，前端删除当前 slash 查询范围，把 qualified identity 加入 `selectedSkillIds`，恢复 textarea 焦点和合理的 caret 位置。按 Escape 只关闭面板并保留原文本，使它恢复普通输入语义。

替代方案是把 `/hello-astra` 原样放入 goal 并要求后端解析。该方案会污染对话历史、产生转义和歧义问题，并把 UI 选择重新降级为自然语言提示，因此不采用。

### 2. 复用现有多选状态并增加持久可见的 Skill token

slash 面板与“添加内容”菜单写入同一个去重后的 `selectedSkillIds`。Composer 在 textarea 上方展示按选择顺序排列的 token；每个 token 包含 Skill 标识、可访问名称和移除按钮。已选择 option 在面板中显示 selected 状态，再次选择不产生重复项。

面板使用 `listbox`/`option` 语义，支持 ArrowUp、ArrowDown、Home、End、Enter 和 Escape。面板打开时 Enter 选择当前 option 而不提交表单；面板关闭时保持原发送行为。textarea 为空且 caret 位于开头时，Backspace 移除最后一个 token，作为 Codex 风格的快速撤销。

高亮使用现有 Astra accent 色的半透明背景、边框、图标和清晰焦点环；深色、窄屏及 `prefers-reduced-motion` 都提供等价可读状态，不能只依赖颜色表达 selected。

替代方案是在 textarea 内实现 contenteditable mention。它会引入输入法、selection、粘贴、撤销栈和移动端无障碍复杂度；独立 token rail 已能满足当前需求，因此不采用。

### 3. 选择是一次 Run 的草稿配置

Skill token 与尚未发送的 goal 一起构成本次 Composer draft。Run 创建成功后清空 goal、slash 状态和 selected Skill tokens；API、网络或 Catalog 校验失败时三者均保留。切换视图不应意外消费选择，新建对话则清空草稿选择。

这避免 Skill 在后续无关问题中静默持续生效。未来若需要 conversation-level pinned Skill，应使用单独的持久设置和明显状态，而不是复用一次性 token。

### 4. `skill_ids` 是确定性预激活合同

客户端发送非空 `skill_ids` 时先按选择顺序去重，并仅提交当前 eligible 列表中的 qualified identities。服务端仍以新建 Run 时冻结的 Catalog 为唯一事实源：每个 identity 必须存在、revision 可重建、未撤销且未超出 activation budget。

全部显式 Skill 激活成功并提交 `skill.activated`（`initiator=explicit`）后，Run 才能进入首次模型操作；任何一项失败都使创建请求失败且不得产生未绑定 Skill 的模型回答。Engine 随后从 Run snapshot 重建 prompt blocks，因此模型可以组合使用显式 Skills，但不能取消、替换或跳过它们。

复用现有 `skill_ids` 字段避免 API 迁移。实现需要补充集成测试，固定“成功响应意味着所有显式 identities 已处于 active snapshot”这一现有但未充分暴露的合同。

### 5. 为未来元数据保留命名空间，但本次不解析

后续可以在标准允许的 `metadata` 对象下增加 namespaced Astra 扩展，例如：

```yaml
metadata:
  astra:
    activation: auto
    output_contract:
      type: every_sentence_prefix
      value: "hello 欧尼酱~"
      on_failure: repair
```

`activation` 可描述 `auto`、`explicit_only` 或 `required_when_selected` 等宿主路由偏好；`output_contract` 可描述结构化、格式或业务 validator 及失败策略。二者都不能授予工具或权限。具体 schema、兼容策略和执行器必须由后续 OpenSpec 变更定义，本次 parser 必须继续把未知 metadata 当作不影响运行的可移植数据。

## Risks / Trade-offs

- [输入 `/` 与路径文本冲突] → 仅在命令边界触发，Escape 恢复普通文本，并覆盖 URL、路径和中文输入法测试。
- [Skill 在面板打开后被禁用或重新发布] → 提交时以后端冻结 Catalog 校验为准，失败时保留 draft 并展示可操作错误。
- [多个显式 Skills 指令冲突] → 保留现有独立 block 和确定性排序，不使用选择顺序覆盖；冲突继续由现有审计协议暴露。
- [token rail 挤压移动端 Composer] → 允许横向/换行布局并限制单个 token 文本宽度，保持 textarea 和发送按钮可用。
- [成功后立即清除导致用户看不到使用记录] → Composer token 是草稿态；提交后的激活身份由过程事件与审计视图承接。
- [显式激活增加首轮延迟] → 激活只读取冻结本地 blob，禁止为了 slash 选择增加模型路由调用。

## Migration Plan

1. 先增加前端 slash 面板、token rail 和现有 `skill_ids` 提交测试，不改变 API schema。
2. 增加后端事务和审计集成测试，确认显式 Skills 在首次模型 invocation 前全部激活。
3. 灰度启用 slash 入口，同时保留“添加内容”菜单作为等价入口和回退。
4. 观察 slash 打开、选择、移除、提交失败和 explicit activation 指标。
5. 回滚时可移除 slash UI 与 token rail；现有附件菜单及 `skill_ids` API 保持兼容。

## Open Questions

- 后续是否需要 conversation-level pinned Skills，应作为独立设置设计，不能由本次一次性 token 隐式演化。
- `activation` / `output_contract` 的 Astra metadata schema、validator 类型与失败策略需要单独提案。
