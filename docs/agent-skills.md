# Astra Agent Skills

Astra Skills 是全局共享、版本化的 Agent 工作流包。当前只有一种完全权限的管理员身份，
没有用户、租户、工作区或发布者权限模型；所有管理员看到同一份 Skill Library。

## 包格式

每个包必须以 `SKILL.md` 为根入口，可选包含 `scripts/`、`references/` 和 `assets/`。
`SKILL.md` 以 YAML frontmatter 开始，必须包含与目录同名的 `name` 和 `description`；
可选字段包括 `license`、`compatibility`、`metadata` 与空格分隔的 `allowed-tools`。
`allowed-tools` 仅表达期望能力，不会授予工具、网络、凭据或文件权限。

自定义名称不能使用保留的 `astra-` 前缀。导入和保存会校验 UTF-8、路径逃逸、文件数量与
大小、可执行二进制、异常二进制位置、混淆、策略绕过和凭据外传模式。校验和预览不执行代码。

## Draft、发布与编辑

- 自定义 Skill 有一个可变 Draft；批量保存以 Draft revision token 做乐观并发控制。
- 发布生成不可变、带 SHA-256 digest 的 Revision，并将它设为 active Revision。
- 历史 Revision 可查看、导出或恢复为新 Draft；恢复不会改写历史。
- 内建 Skill 随 Astra 发布且只读。需要修改时先克隆为自定义 Skill。
- 移除使用 tombstone；历史 Run 仍按原 Revision 和 digest 审计。

Skill Library 使用 Monaco，支持多文件模型、标签、撤销/重做、语言高亮、Markdown
源码/安全预览、诊断、自动保存、Diff 与历史。它不是完整 VS Code 工作台：不提供扩展宿主、
终端或在编辑器进程内执行脚本。

## 快速与可信模式

Run 创建前冻结 Eligible Skill Catalog。Composer 显式选择只能引用 Catalog 中已启用的
内建 Revision 或自定义 active Published Revision。

- 快速响应不创建 TaskContract 或 Plan DAG；控制器可从冻结 Catalog 激活 Skill，并按需读取
  活跃 Skill 的文本资源。
- 可信执行在 TaskContract 和完整 DAG 前完成 Skill Resolution；identity、Revision 和 digest
  写入 TaskContract，节点以 `required_skill_ids` 绑定所需子集。后续新增 Skill 需要正常
  PlanPatch/replan，不能静默切换模式。

Skill 指令低于平台、Agent Profile、角色协议和管理员显式指令。Skill 不能绕过 Tool Catalog、
Effect 分析、批准、Sandbox、Artifact、预算或完成门。

## Draft 测试

Workbench 可用必填目标启动快速或可信 Draft 测试。系统冻结当时 Draft 为 test-only
Revision，标记 Run 为 Draft test，并沿用相同的激活、资源、权限、Sandbox 与审计边界。
后续编辑不会改变测试 digest，test-only Revision 也不会进入普通 Eligible Catalog。

## API 概览

- `GET/POST /api/skills`：列表和创建。
- `POST /api/skills/import`、`/{id}/clone`、`/{id}/export`：可移植包。
- `GET/PUT /api/skills/{id}/draft/*`：虚拟文件读取和原子批量保存。
- `POST /api/skills/{id}/validate|publish|test-runs`：校验、发布和 Draft 测试。
- `GET /api/skills/{id}/revisions|diff|preview`：历史、Diff 和安全预览。
- `GET /api/runs/{run_id}/skills`：冻结 Catalog、激活和资源读取审计。

## Rollout、回滚和排障

先启用 `ASTRA_SKILLS_ENABLED` 以创建表并加载内建包，再启用
`ASTRA_SKILLS_CUSTOM_AUTHORING_ENABLED` 开放自定义编辑。回滚模式集成时保留表和 Blob，
关闭开关即可让新 Run 不再冻结 Catalog；不要删除历史 Revision。

- `SKILL_DRAFT_STALE`：其他窗口已保存，重新加载并在 Diff 中合并。
- `SKILL_PACKAGE_INVALID`：按诊断中的文件、行列和 code 修改。
- 激活失败：确认 Skill 已启用、已发布、未撤销且存在于该 Run 的冻结 Catalog。
- 脚本无法运行：编辑/预览不执行脚本；执行必须由注册工具通过标准 Sandbox 管线完成。
