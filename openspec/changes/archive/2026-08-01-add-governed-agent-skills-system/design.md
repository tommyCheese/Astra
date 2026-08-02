## Context

Astra 当前把稳定身份和治理原则放在冻结的 Agent Profile 中，把动态事实放在分隔的不可信运行时上下文中，把实际能力交给 Tool Manifest、Tool Provider Plugin、Permission Engine、Sandbox 和 Completion Gate。系统仍缺少可移植的“领域操作手册”，也没有用于持续创作 `SKILL.md`、脚本、参考资料和模板的完整平台体验。

Agent Skills 开放格式的最小核心是一个带 YAML frontmatter 的 `SKILL.md`，并可附带 `scripts/`、`references/` 和 `assets/`。它通过“名称/描述 → 完整说明 → 单个资源”的渐进式披露控制上下文成本，但没有定义平台编辑器、Draft、发布、安装来源、权限或宿主执行语义。

当前 Astra 产品假设只有一个具备完整管理权限的管理员主体。所有自定义 Skill 都由该主体上传或创建并在平台内共享；本变更不设计用户、租户、workspace、project、ownership、Publisher 或共享策略。管理员拥有管理权并不意味着 Skill 脚本可以绕过 Tool、Effect、审批、Sandbox 或审计边界。

Astra 只有快速响应 `standard` 和可信执行 `trusted` 两种产品模式。快速模式不能为了使用 Skill 创建 TaskContract 或 DAG；可信模式必须在首次外部行动前生成完整 DAG。因此 Skill 解析需要共享基础设施和不同的模式接入点。

## Goals / Non-Goals

**Goals:**

- 兼容开放 Agent Skills 目录格式并保持导入导出能力。
- 只支持 Astra 内建只读 Skill 和管理员创建/上传的全局共享自定义 Skill。
- 提供基于 Monaco 的多文件创作工作台、Markdown 预览、校验、Diff、历史、Draft 测试和显式发布。
- 通过不可变 Published Revision 和 Run snapshot 防止编辑导致运行语义漂移。
- 通过三级渐进式披露支持大量 Skill，而不把完整内容预载入每次模型调用。
- 让快速模式在轻量 Agent Loop 中使用 Skill，让可信模式在 TaskContract/DAG 前解析 Skill。
- 让 Skill 脚本、命令和外部行动完全复用 Tool Plugin、Effect、审批、Sandbox、Workspace 和 Artifact 管线。
- 防止 Skill 内容提升权限、越过路径边界、静默执行或污染 Agent Profile。

**Non-Goals:**

- 第一阶段不实现用户、租户、workspace、project、所有权、可见性、Publisher、多人实时协作或共享审批。
- 不实现公开市场、远程搜索、自动下载、自动更新或商业分发。
- 不在浏览器中嵌入完整 VS Code Workbench、Extension Host、终端或任意本地文件系统。
- 不扩展 Agent Skills 核心格式来定义 Astra 权限；Draft、发布和启停状态保存在包外。
- 不把 Skill 当作 Tool Provider Plugin、MCP server、Agent Profile、Memory 或自定义 Agent 的替代品。
- 不允许 Skill 代码在 Astra API 进程内导入或执行。
- 不定义 Skill 依赖图、组合 DSL、版本求解器或跨包 lockfile。

## Decisions

### 1. Skill 只有 built-in 与 custom 两种 origin

```text
Astra Release
  └── Built-in Skill Revision
      ├── globally available
      ├── immutable
      └── update only with Astra release

Administrator
  └── Custom Skill
      ├── uploaded or created in Astra
      ├── globally shared
      ├── mutable Draft
      └── immutable Published Revisions
```

`astra.*` 是保留 identity namespace。内建 Skill 可以查看、启停和导出，但不能原地编辑；“自定义”操作创建新 identity 的 custom Draft。自定义 identity 在平台内必须唯一，Catalog 不使用扫描顺序或来源优先级解决冲突。

替代方案是保留 platform/workspace/user/project scope。单管理员前提下这些表和策略没有产品价值，只会扩大 API、数据库和 UI，因此删除。

### 2. Portable Package 与平台 revision 状态分离

Portable `SkillPackage` 仍只包含：

```text
skill-name/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

平台记录：

```text
SkillRecord
├── identity
├── origin: builtin | custom
├── enabled
├── active_published_revision
├── draft_revision
└── publication_history[]
```

`SkillRevision` 保存文件 manifest、规范化 bytes、per-file digest、package digest、validation report、predecessor 和创建/发布时间。Astra 状态不写回 `SKILL.md`，因此导出仍符合开放格式。

### 3. Monaco + Astra Virtual Skill Filesystem 构成创作工作台

工作台采用 Monaco Editor 作为代码编辑组件，而不是完整 VS Code Web：

```text
Skill Library
  └── Skill Workbench
      ├── virtual file tree
      ├── Monaco URI models + tabs
      ├── Markdown source/preview
      ├── diagnostics + navigation
      ├── search/replace
      ├── Draft/Published diff
      ├── revision history
      └── test + publish controls
```

后端提供根受限的虚拟文件 API；Monaco model URI 使用 `skill-draft://<skill-id>/<path>`，避免把服务端实际路径暴露给浏览器。支持创建、移动、重命名、删除和批量原子保存。Markdown 源码是唯一事实来源，frontmatter 表单或预览都不得进行有损回写。

工作台不启动终端、Extension Host 或进程。脚本测试只能创建显式 Draft test Run。Markdown preview 使用安全 sanitizer，禁止 embedded script、active HTML 和未授权远程内容。

替代方案是嵌入完整 VS Code/code-server。它会引入远程计算、扩展、终端、认证和更大的资源/安全边界，对 Skill 包编辑并非必要，因此第一阶段不采用。

### 4. Draft 可变，Published Revision 与 test snapshot 不可变

```text
Edit
  → autosaved Draft(revision_token)
  → validate exact Draft revision
  → show diff + diagnostics
  → explicit publish
  → immutable Published Revision(digest)
  → eligible for ordinary Runs
```

保存不影响 active Published Revision。发布使用乐观并发：验证和 commit 必须针对同一个 Draft revision token，否则拒绝。历史 Published Revision 只能查看、导出或恢复为新 Draft，不能修改。

Draft 测试冻结一个临时不可变 snapshot。测试期间继续编辑不会改变运行内容，且 test snapshot 永远不进入普通 Catalog。测试必须显式选择快速或可信模式。

替代方案是保存即生效。它会让半成品或语法错误进入新 Run，并使编辑行为难以审计，因此不采用。

### 5. Package validation 与发布验证共用同一管线

上传、平台编辑、发布和测试使用同一 parser/path/digest/safety pipeline：

```text
bounded bytes
  → frontmatter validation
  → root/path/link validation
  → file/media/size limits
  → resource manifest + digest
  → safety/compatibility diagnostics
```

导入只创建 Draft，不自动发布或执行。critical finding 阻止发布和执行型测试，但允许继续编辑和安全预览。检查过程本身不执行包内容。

### 6. Catalog 只包含发布且启用的 revision

每个普通 Run 的 eligible Catalog 由以下内容确定：

- 当前 Astra release 的 enabled built-in revisions；
- enabled custom Skills 的 active Published Revision；
- compatibility、runtime availability、Tool Catalog 和 budget。

Draft 不进入普通 Catalog。identity 冲突在创建/导入/发布前拒绝。Catalog 用 `(origin, identity, revision_digest)` 定位 revision，并按稳定 identity 排序生成 digest。

Catalog metadata 超过预算时，使用名称/描述文本索引和任务关键词生成确定性 shortlist；管理员显式选择可绕过 shortlist，但不能绕过 revision、compatibility 或 runtime 检查。

### 7. Activation Service 实现三级渐进式披露

Run 创建时冻结 eligible metadata。Prompt Composer 首先只给模型 `name`、`description`、origin、qualified identity、revision 和兼容摘要。管理员显式点名时宿主预激活；否则模型通过结构化 `activate_skill` runtime action 请求激活。

Activation Service 返回：

```text
<astra_skill
  name="..."
  identity="..."
  origin="builtin|custom"
  revision="sha256:...">
  [validated SKILL.md body]
  Skill root: skill://<run-snapshot>/<identity>/
  Resources: [bounded manifest, bodies omitted]
</astra_skill>
```

具体资源通过 `read_skill_resource` 从 frozen snapshot 读取并校验 path、digest、media type 和 byte budget。该动作不读取 Draft 或 live storage。

### 8. Skill 是受管程序性指导，不是平台策略或授权主体

Prompt 层次固定为：

```text
Platform policy
  > frozen Agent Profile
  > trusted role protocol
  > explicit administrator intent
  > active governed Skill guidance
  > conversation/memory/tool/external context
```

每个 Skill block 保留 origin、identity 和 revision，不把多个 Skill 合并成匿名文本。冲突时显式选择优先；未显式选择且无法兼容时应呈现冲突，而不是按加载顺序覆盖。

`allowed-tools` 标准化为 requested capability metadata，只用于诊断和候选过滤。Tool Catalog 决定可用性，Permission Engine 和 approval behavior 决定每次调用是否执行。单管理员完全管理权限不改变这一运行时边界。

### 9. Skill 脚本是不可变输入，执行仍是普通工具调用

打开、编辑、保存、预览、激活和发布都不执行代码。模型或测试流程决定运行脚本时，runtime 将 frozen resource 作为只读 digest-checked input 交给 `astra.shell` 或未来专用 sandbox provider。Invocation Pipeline 继续执行 schema validation、effect analysis、authorization、approval behavior、sandbox execution、result processing 和 verification。

需要使用模板时，显式复制到 Task Workspace，并进入 workspace change tracking。脚本依赖不会因写入 `compatibility` 或说明文档而自动安装。

### 10. 快速模式在轻量 Agent Loop 中激活 Skill

```text
Request + frozen Skill metadata
  → explicit pre-activation OR quick controller activate_skill
  → load instructions/resources on demand
  → quick decide/call tool/finalize loop
```

快速 Run 不创建 TaskContract、Plan、PlanNode、PlanEdge 或 trusted Completion Gate。Skill 可以指导多步行动，但不能把 Run 变成可信执行。若 Skill 显示出长流程、多交付物或强验证需求，UI/Agent 可以建议重新以 trusted 启动，不能静默切换。

普通 quick Run 只使用 Published Revision；workbench quick test 使用 frozen Draft test snapshot。两者都共享 Tool、Effect、审批、Sandbox、Artifact、取消和错误边界。

### 11. 可信模式在 TaskContract 与 DAG 前完成 Skill Resolution

```text
Request + frozen Skill metadata
  → Skill Resolution
  → activate exact revisions
  → TaskContract
  → complete canonical Plan DAG
  → optional Plan confirmation
  → NodeExecution with attenuated Skill subset
  → full Verification + Completion Gate
```

TaskContract 保存 selected Skill revisions；Plan node 可声明 `required_skill_ids`；NodeExecution 只重建节点所需子集。强制 Skill 检查只有被接纳为 success criterion 并获得证据后才能影响 Completion Gate。

完整 Plan 持久化后若需要激活 frozen Catalog 中的新 Skill，必须通过 PlanPatch/replan 修改未完成 DAG。完成节点与已接受证据保持不可变。Skill 激活不能改变 `trusted` 的 Plan confirmation 或 effect approval 语义。

### 12. Run Skill Snapshot 保存可恢复内容而非 live 引用

Run 首次模型操作前保存 `RunSkillCatalogSnapshot`：

- Catalog schema/version/digest 和 normalized metadata；
- origin、identity、Published Revision 或 Draft test digest；
- 完整 resource manifest；
- durable `SKILL.md`/resource blob references；
- activation history、resource reads 和 attributed tool calls；
- answer mode 和 trusted Plan/node bindings。

编辑、重新发布、禁用或删除 custom Skill 不改变已有 Run。紧急 revocation 可以阻止旧 snapshot 发起新的 executable/external action，但不删除审计内容。

### 13. API 分为 Library、Draft Files、Revision 和 Run Skill

```text
GET    /api/skills
POST   /api/skills/import
POST   /api/skills
GET    /api/skills/{skill_id}
POST   /api/skills/{skill_id}/clone
GET    /api/skills/{skill_id}/draft/files
PUT    /api/skills/{skill_id}/draft/files
POST   /api/skills/{skill_id}/validate
POST   /api/skills/{skill_id}/test-runs
POST   /api/skills/{skill_id}/publish
GET    /api/skills/{skill_id}/revisions
POST   /api/skills/{skill_id}/revisions/{revision}/restore
GET    /api/skills/{skill_id}/export
PUT    /api/skills/{skill_id}/state
DELETE /api/skills/{skill_id}
GET    /api/runs/{run_id}/skills
```

批量文件保存和 publish 使用 revision token/ETag。创建与导入都进入 Draft。built-in 写操作返回明确只读错误；clone 是自定义入口。普通 Run 响应返回 safe summary，完整内容通过专用 Skill/revision 或受控审计接口读取。

### 14. Skill 与其他扩展保持正交

- Skill：告诉模型如何完成一类任务，可带只读资源。
- Tool Provider Plugin：向宿主提供可执行动作和受信任运行组件。
- Agent Profile：定义 Astra 的稳定身份与治理原则。
- Memory：保存有 provenance 的动态事实。
- Answer mode：定义 quick 或 trusted 的规划与验证生命周期。

Skill 可以引用工具或建议写入 Memory，但不能注册工具、修改 Profile、绕过 Memory provenance、改变 answer mode，或创建权限更大的执行主体。

## Risks / Trade-offs

- [管理员上传恶意或错误 Skill] → 上传者拥有平台管理权仍不代表代码受信；发布检查、prompt framing、effect/approval、Sandbox、只读 snapshot 和 revocation 保持有效。
- [编辑器被误认为完整 VS Code] → 产品明确称为 Skill Workbench；提供完整多文件创作能力但不承诺 Extension Host、终端或调试器。
- [保存即影响生产 Run] → Draft 与 Published Revision 严格分离，只有显式 publish 改变普通 Catalog。
- [发布期间出现混合文件版本] → validation 与 commit 绑定原子 Draft revision token。
- [Markdown preview 触发 active content] → sanitize HTML、禁用脚本与未授权远程资源。
- [大量 metadata 膨胀上下文] → compatibility filtering、确定性 shortlist 和三级渐进披露。
- [描述过宽导致误激活] → structured activation、显式事件、可停用和触发测试；高风险行动仍需权限门控。
- [多 Skill 冲突] → identity 唯一、独立 block、显式选择优先和冲突事件，不采用 load-order wins。
- [脚本依赖不齐] → compatibility 诊断、依赖不自动安装和可操作 capability gap。
- [snapshot 存储重复大文件] → content-addressed blob 去重，Run 保存 durable digest reference。
- [快速模式执行复杂 Skill 结果不够可靠] → 保持模式语义并给出 trusted 建议，不伪造可信验证。
- [可信 Skill 选择增加首次行动延迟] →显式选择可跳过语义选择调用；可信模式接受 Skill Resolution 换取可验证计划。

## Migration Plan

1. 新增 package parser、validator、content-addressed storage、built-in loader 和 custom Skill/Draft/Published Revision 数据模型，不影响现有 Prompt。
2. 新增 shared Skill Library、虚拟文件 API、Monaco workbench、Markdown preview、autosave、Diff 和 revision history。
3. 新增 Draft validation、publish、clone、import/export 和 quick/trusted Draft test Run。
4. 新增 deterministic Catalog、`activate_skill`、`read_skill_resource` 和 Prompt Composer Skill layer。
5. 接入 quick Agent Loop，证明 Skill 使用不会创建 TaskContract/DAG 或改变 answer mode。
6. 接入 trusted Skill Resolution、TaskContract/Plan binding、node attenuation、PlanPatch 和 Completion Gate。
7. 接入 sandbox script/resource materialization、Skill-attributed events、Run snapshot、恢复和审计 UI。
8. 随 Astra release 启用内建 Skill，再开放 custom create/import/edit/publish；用 context cost、激活准确率、Draft test 和安全数据校准默认预算。

回滚时关闭 Skill Catalog 注入、activation 和 Workbench publish feature flags；安装记录、Draft、Published Revision 和 Run snapshot 保持只读可审计。数据库迁移只新增表和引用，不改变非 Skill Run 的现有数据。

## Open Questions

- 后续是否需要为 custom Skill 引入 Git remote、package lockfile 或市场分发，独立于本提案处理。
- 内建 Skill 数量达到何种规模后需要为 release bundle 增加分组或延迟加载索引。
- 真实 Catalog 规模达到何种阈值后值得用 embedding/reranker 替换确定性 metadata shortlist。
- 是否需要在后续版本加入多人身份后再设计 Draft 锁、评论、共享范围和发布审核。
