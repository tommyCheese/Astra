## Context

Agent Profile 当前通过 `importlib.resources` 从后端包读取，并由进程级 `load_agent_profile()` 缓存。创建 Run 时会把完整 Profile 快照写入 Run，执行和恢复则优先使用该快照。Runtime 配置已经有本机 JSON 存储、原子替换写入和仅回环 API 边界，适合承载单用户部署的激活 Profile；本变更无需引入在线多 Profile、账号权限或新的数据库实体。

## Goals / Non-Goals

**Goals:**

- 让本机用户在 Runtime 设置中读取、编辑、校验、激活和恢复四份 Profile 文档。
- 保存后让所有后续 Profile 加载立即看到同一激活版本，同时不改变已有 Run 的不可变快照。
- 复用现有 Profile schema、章节、大小、角色矩阵和哈希校验，避免出现第二套校验规则。
- 复用 Runtime 配置的原子持久化和本机 API 安全边界。

**Non-Goals:**

- 不支持多个命名 Profile、草稿/审批/发布工作流或跨设备同步。
- 不允许编辑角色到文档的组合矩阵、schema 元数据约束或权限/工具配置。
- 不把 Profile 文本公开到 Run、分享或审计 API。
- 不迁移历史 Run 快照，也不修改运行中的 Run。

## Decisions

1. **在现有 Runtime JSON 中持久化激活 Profile。** 保存结构包含来源、版本和四份规范化文档。这样与用户提出的“运行时配置”一致，并复用原子替换写入。备选的 revision 数据库表适合多 Profile 与发布治理，但对当前单本机激活版本会引入不必要的迁移和仓储复杂度。

2. **由 `RuntimeProfileService` 成为当前 Profile 的解析入口。** 服务在读取时用内置 Profile 补齐默认展示，在更新时调用 `AgentProfileLoader` 完整校验，并维护一个经锁保护的当前不可变 Profile。应用启动后把该解析器注册给 `load_agent_profile()`；测试和未初始化路径仍回退到包内默认值。

3. **更新和恢复使用独立 Runtime 子资源。** `PUT /api/runtime/agent-profile` 接收完整文档集合并原子激活，`POST /api/runtime/agent-profile/reset` 删除用户覆盖。依赖镜像构建与 Profile 保存互不绑定，避免编辑提示词触发容器构建。

4. **API 返回可编辑内容，但 Run API 继续脱敏。** Profile 设置接口位于已有的本机 Runtime 边界中，可以返回全文；普通 Run、分享和事件 API 仍只返回安全 manifest。任何校验异常通过统一 ValidationError 返回，不回显无关服务端配置。

5. **保存采用整组替换。** 客户端每次提交四份文档，后端先整体构建 `AgentProfile` 再写盘并切换内存引用，杜绝部分更新造成的混合版本。并发保存以最后一次完成的有效整组写入为准。

## Risks / Trade-offs

- [用户可改变高信任提示内容] → 仅对本机用户开放，并继续在 Prompt Composer 中追加不可编辑的信任/能力边界；Profile 不能注册工具或绕过权限门控。
- [Runtime JSON 包含较长 Markdown] → 四份文档各受 16 KiB 限制，总体远低于现有配置可接受规模，写入继续使用原子替换。
- [多进程部署的内存缓存可能短暂不一致] → 当前产品是单后端进程的本机应用；文件是持久化事实来源，多进程协调留给未来 revision 存储方案。
- [保存与创建 Run 竞态] → 切换单位是完整不可变 `AgentProfile`；Run 只会冻结保存前或保存后的完整版本，不会得到部分内容。

## Migration Plan

1. 部署后，缺少 `agent_profile` 字段的现有 Runtime JSON 自动展示并使用内置 Profile，不写入迁移数据。
2. 用户首次保存时写入新字段并立即激活；恢复默认时移除该字段。
3. 回滚到旧版本时，旧服务会保留但忽略未知字段；再次升级后仍可读取该覆盖。

## Open Questions

无。多 Profile、版本历史和多人权限在实际出现需求时另建变更。
