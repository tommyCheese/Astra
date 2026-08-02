## Context

Astra 当前使用 `TaskRecord` 同时承载产品上的 Conversation；同一 Task 下可包含多个 Run。Run 拥有 ToolCall、ApprovalRequest、ApprovalGrant、Sandbox Job 和 Artifact。当前批准策略只接收 execution mode、tool input 和 Run grants；`plan_only` 一律拒绝工具执行，`request_approval` 对所有未授权工具调用暂停。

Sandbox 当前按 ToolCall 创建一次性容器，使用独立的 `/input`、`/output` 和 `/tmp`，执行结束后立即销毁。Artifact Collector 只收集本次 `/output` 中少数支持类型，无法表达跨工具共享文件、后续 Run 修改、删除 tombstone 或完整任务目录。

业界成熟 Agent 系统普遍把安全分为互补层：权限策略决定“是否允许”，Sandbox 和网络边界决定“技术上能触达什么”，用户审批决定“是否接受这一次边界内行动”，托管策略则限制用户能否启用 bypass/full-access。项目或外部工具提供的声明只能作为不可信提示，不能替代宿主的确定性策略。

## Goals / Non-Goals

**Goals:**

- 建立覆盖本地工具、外部应用、MCP、插件、子 Agent、凭据和数据流的统一权限模型。
- 支持组织不可覆盖策略、用户选择、Task/Run 临时授权和执行时强制边界的清晰优先级。
- 以 invocation 的真实行为和资源范围决定审批，而不是按工具名称或静态风险一刀切。
- 让 `plan_only` 能通过只读查询和临时计算形成证据充分、针对性强的计划，同时保证零持久副作用。
- 保证首次创建持久文件也必须审批。
- 支持一次性、Run 级相似行为和用户明确选择的 Task 级相似行为授权。
- 为同一 Task 的多个工具和多个 Run 提供连续、隔离、可审计的工作区。
- 保证恶意或被污染的 Workspace 内容不能影响 Astra 控制面、宿主系统、其他 Task、工具权限和审批策略。
- 在任务结果中展示所有有意义的创建、修改、删除文件及可预览图片等交付物。

**Non-Goals:**

- 不依赖模型自我约束、自然语言指令或第三方 tool annotations 提供硬安全保证。
- 不让用户批准、自动 reviewer、Hook 或 Workspace 配置覆盖平台 deny 和受保护资源。
- 用户审批不能放行平台明确禁止的宿主 Docker socket、提权、私网访问或越界路径。
- 第一阶段不提供交互式 TTY、长期后台服务或任意宿主工作区读写。
- 第一阶段不提供跨 Task 或全局永久授权。
- 不承诺仅靠 Bash 静态分析准确预测任意脚本；未知程序执行采用保守权限和审批。

## Decisions

### 使用统一权限请求模型

所有受控行动先规范化为 `PermissionRequest`：

```json
{
  "subject": {
    "agent_id": "agent:astra-main",
    "run_id": "run-123",
    "delegation_chain": ["user-7", "task-9", "run-123"]
  },
  "action": "workspace.file.write",
  "resource": "task://task-9/workspace/reports/summary.md",
  "conditions": {
    "tool": "bash_execute@1.1",
    "network_destination": null,
    "data_labels": ["internal"],
    "interactive": true
  },
  "effect_plan_hash": "sha256:..."
}
```

Permission Engine 返回：

```text
allow | ask | deny
```

并附带命中的策略、授予范围、强制执行参数、原因代码和审计字段。`ActionEffectPlan` 是生成 PermissionRequest 的重要输入，但权限系统不局限于工具执行；读取敏感数据、签发凭据、创建子 Agent、连接 MCP、导出 Library 内容和修改安全设置都必须经过同一引擎。

运行时只允许一个可执行授权入口：`PermissionEngine.authorize_invocation(...)`。该入口把一个冻结的 ActionEffectPlan 规范化为一个或多个 PermissionRequest，并在内部统一合并 ToolSpec 权限上限、受保护资源、执行模式、Run/Task lease、一次性批准、无人值守 Permission Bundle、DataFlowState 和网络外发约束。它返回聚合后的唯一 `allow | ask | deny` 结果；AgentLoop 只负责按结果执行、创建 ApprovalRequest 或阻断，不能再分别调用 Bash 特判、外发判断、Bundle 判断或旧 ApprovalPolicy。ApprovalRequest 只是 `ask` 决策的交互与恢复载体，不是第二套授权系统。

### 策略层级只允许收窄权限

策略来源按信任边界分层：

```text
平台硬禁止 / 受保护资源
          ↓
组织托管策略
          ↓
部署与 Workspace 管理策略
          ↓
用户账户策略
          ↓
Task 策略与 Task Grants
          ↓
Run 策略与 Run Grants
          ↓
一次性批准
```

高层策略不能被低层覆盖。规则采用 `deny → ask → allow` 优先级；任意命中 deny 即拒绝。下层 allow 不能覆盖上层 ask 或 deny。Workspace 文件、项目设置、Skill、Hook、插件和 Agent 自身都不是强制策略来源，除非由组织管理员明确登记为受信策略组件。

策略支持版本、来源、签名/摘要、生效时间、过期时间和撤销状态。每次 Run 冻结有效策略摘要，恢复执行时验证策略没有发生导致权限扩大的不兼容变化。

### 区分授权、审批和强制执行

- Authorization：确定主体是否可对资源执行动作。
- Approval：由用户或受控 reviewer 决定是否接受某个 `ask` 行动。
- Enforcement：Sandbox、文件代理、网络代理、Credential Broker 和外部服务令牌实际限制能力。

Approval 只把 `ask` 转成有范围和期限的 allow lease，不能改变 enforcement boundary。自动 reviewer 只是替换审批者，不能增加 writable roots、网络范围、凭据 scope 或受保护资源访问。

### Agent 身份和权限委托必须可追踪

每个主 Agent、子 Agent、后台 Agent、Reviewer 和 Tool Runtime 都有独立、可审计 identity。调用链记录：

- 发起用户或服务身份；
- Task、Run 和当前 Agent；
- 父 Agent 与子 Agent；
- 使用的工具/MCP server/runtime；
- 最终访问的资源和外部服务身份。

子 Agent 使用权限衰减委托：

```text
child_permissions ⊆ parent_permissions ∩ task_policy ∩ delegated_scope
```

子 Agent 不能批准自己的提权请求，不能把权限继续委托为更大范围，也不能继承父 Agent 未显式委托的凭据、网络或敏感数据访问。Reviewer 与执行 Agent 分离，Reviewer 只拥有读取审批上下文和返回决定的权限。

### 权限使用租约而不是永久布尔值

Grant 是带条件的 capability lease，至少包含：

- subject、action、resource matcher；
- Run 或 Task scope；
- tool/provider/version 条件；
- data labels 和网络目标；
- 最大调用次数、最大数据量或成本；
- created、expires、last_used 和 revoked；
- 来源用户、ApprovalRequest 或托管策略。

Task Grant 默认可撤销，并可配置空闲过期和绝对过期。工具、版本、schema、effect analyzer 或资源范围发生实质变化时，相关 Grant 失效或重新进入 ask。

### 建立凭据代理和代表用户执行语义

模型、Workspace、普通 ToolResult 和日志不直接获得长期 secret。Credential Broker 在执行时根据 PermissionRequest 签发或注入：

- 服务和租户限定；
- 最小 OAuth scope/role；
- 资源限定；
- 动作限定；
- 短 TTL；
- 不可转移给其他 Agent 或 Tool；
- 可撤销且可审计。

外部操作必须记录使用的是 Agent 自身身份、服务身份还是代表用户的 delegated identity。代表用户执行不能自动升级到用户账户的全部权限；如果目标服务不支持细粒度临时令牌，使用受控代理代为调用。

凭据不可写入 Task Workspace、Library、模型 prompt、Artifact、stdout/stderr 或可下载日志。读取 `.env`、密钥文件、浏览器会话、云凭据和 token store 是独立的 `sensitive_data.read` 权限，不因普通文件读取授权而获得。

### 数据权限覆盖读取、持有、处理、持久化和外发

数据访问不仅判断“能否读取”，还追踪：

- 来源信任：用户、Workspace、网页、MCP、Library、内部系统；
- 敏感标签：public、internal、confidential、secret、credential、personal；
- 允许用途：回答、临时计算、持久化、索引、分享；
- 允许目的地：当前用户、指定服务、指定域名或禁止外发；
- 保留期限和日志策略。

Run 在读取不可信内容或敏感数据后更新 `DataFlowState`。后续行动若同时具备“敏感数据访问 + 不可信指令来源 + 外发能力”，权限引擎提高风险、强制人工审批或直接 deny，避免通过 Web/MCP/消息工具形成数据外泄链。

网络权限按 scheme、host、port、path、HTTP method、请求大小和数据标签约束；deny 优先，默认阻止 localhost、私网、metadata endpoints、Unix sockets 和 DNS/IP 解析后的非公开地址。读取 Web Search 不等于授予任意 Bash 网络访问。

### MCP、插件和扩展属于供应链边界

工具注册时保存 provider identity、来源、版本、schema digest、权限上限、运行位置和信任级别。第三方 MCP tool annotations（如 read-only、destructive、idempotent、open-world）只作为 UI/分析提示；未受信服务器的声明不得直接触发 auto-allow。

管理员可以：

- allow/deny MCP servers、插件市场、Skill 脚本和 Hook 来源；
- 固定版本或内容摘要；
- 禁止项目级扩展；
- 只允许托管 Hooks 和权限规则；
- 要求扩展变更后重新信任；
- 限制每个 Agent profile 可见的工具集合。

Run 创建时冻结 Tool Catalog Snapshot。执行期间发现工具 schema、server identity、签名、权限声明或版本变化时 fail closed，要求重新建立连接或重新授权。

### 受保护资源和高影响操作使用强化控制

以下资源默认不允许普通 Task Grant 覆盖：

- 权限策略、Grant、ApprovalRequest 和审计日志；
- Credential Broker、身份和 token store；
- Astra 控制面配置、系统 runtime、Sandbox policy；
- 其他 Task/用户 Workspace 和 Library；
- `.git`、Agent 配置、Hook、插件配置等可能改变后续执行行为的路径；
- 生产部署、权限管理、支付、公开发布和批量删除等高影响外部操作。

高影响操作可要求：

- 重新认证或 step-up；
- 双人/双角色复核；
- dry-run 或变更预览；
- 最大影响数量；
- 延迟执行和撤销窗口；
- 执行后验证与通知。

### 无人值守执行必须 fail closed

Headless、定时任务和后台 Run 没有即时用户审批界面，只能使用预先批准的 Permission Bundle。任何 ask 决策都变成暂停或 deny，不能由主执行 Agent自行升级为 allow。

Permission Bundle 必须声明工具、资源、网络、数据标签、凭据、预算、期限和输出目的地。自动 reviewer 可以处理组织允许自动评审的中低风险 ask，但失败、超时、解析错误或策略不确定时必须 fail closed。

### 权限必须可解释、可查看和可撤销

系统提供权限中心和 Policy Explain API，展示：

- 当前有效平台/组织/用户/Task/Run 策略；
- 有效、过期和已撤销 Grants；
- 每项 Grant 的创建者、原因和最近使用；
- Agent identity 和 delegation chain；
- MCP/插件/Hook 来源及信任状态；
- “为什么允许、为什么询问、为什么拒绝”；
- 强制 Sandbox、网络、凭据和数据流边界。

权限中心默认采用面向普通用户的渐进披露结构：首层只回答“当前允许什么、有效多久、用了几次、生成了哪些文件、最近为何询问或阻止”；身份类型、Tool Catalog digest、原始事件名和 matcher 等工程信息放入折叠的技术与审计详情。空检查点和无信息的内部记录不占据主要阅读区域。

管理员在部署前可使用 policy simulation 对代表性 PermissionRequest 做 dry-run，防止策略更新意外扩大权限。撤销 Task Grant 后，尚未开始的调用立即失效；正在执行的长期操作按风险决定终止或禁止后续步骤。

### 审批冻结 ActionEffectPlan

Tool Router 完成注册、schema 和平台 capability 校验后，调用工具的 effect analyzer，生成后端可信的 `ActionEffectPlan`：

```json
{
  "tool_name": "bash_execute",
  "tool_version": "1.1",
  "summary": "创建 reports/summary.md",
  "cwd": "/workspace",
  "effects": [
    {
      "kind": "workspace_write",
      "resource": "reports/summary.md",
      "risk": "moderate",
      "reversible": true
    }
  ],
  "required_permissions": ["process_execute", "workspace_write"],
  "network_scope": "none",
  "analyzer_version": "1",
  "approval_required": true
}
```

ApprovalRequest 冻结工具输入、工作目录、effect plan、规范化哈希及 analyzer version。批准恢复时同时验证这些字段，防止“批准 A、执行 B”或分析规则变化后的重放。

### 将执行模式定义为副作用处理策略

执行模式不直接决定工具是否可调用，而是在平台权限和 effect plan 已解析后决定如何处理：

| 平台允许的行为 | `plan_only` | `request_approval` | `auto_approval` |
|---|---|---|---|
| 只读查询 | 直接执行 | 直接执行 | 直接执行 |
| 非持久临时计算 | 直接执行 | 直接执行 | 直接执行 |
| 首次持久文件创建 | 不执行，记录为计划动作 | 审批或匹配 Grant | 无交互执行 |
| 修改、覆盖、删除 | 不执行，记录为计划动作 | 审批或匹配 Grant | 无交互执行 |
| 外部系统写入 | 不执行，记录为计划动作 | 审批或匹配 Grant | 无交互执行 |
| 平台禁止行为 | 拒绝 | 拒绝 | 拒绝 |

`plan_only` 遇到副作用行动时不创建可批准弹窗，也不执行该行动。Agent 收到结构化 `effect_blocked_by_mode` observation，并继续形成包含依据、预计变更、风险和验证方式的可执行计划。这样仅规划模式仍能搜索网页、读取工作区、检查项目结构、运行真正非持久的分析，但不会改变任何持久状态。

### 工具静态权限与 invocation 动态权限共同生效

ToolSpec 声明工具可能使用的最大权限集合；effect analyzer 根据本次输入收窄为实际所需权限。动态计划不得扩大 ToolSpec 或平台策略授予的权限。

```text
平台允许权限
      ∩
ToolSpec 最大权限
      ∩
Invocation Effect Plan 实际权限
      =
本次可授予执行权限
```

如果 analyzer 无法确定行为，采用保守 effect，例如 `process_execute_unknown`，并只在 `request_approval` 或 `auto_approval` 下按受限读写/网络策略运行。`plan_only` 不执行未知副作用程序。

### Bash 使用分层行为分析并在执行时强制权限

Bash analyzer 识别明确只读命令和危险参数，解析重定向、删除、移动、权限变更、网络方法及工作目录。未知脚本、复杂 shell 和自定义二进制默认视为可能修改工作区。

分析结果不仅用于 UI：

- 只读命令只读挂载 Task Workspace。
- 临时计算不挂载持久工作区，或只读挂载并使用易失 `/tmp`。
- 获批写入行为才可读写挂载工作区。
- 无网络权限保持 `network=none`。
- 网络访问继续受域名、协议、方法和私网拦截策略限制。

### Grant 匹配能力、资源范围和调用约束

Grant 包含：

- `scope`: `run` 或 `task`
- effect kinds，例如 `workspace_write`
- resource matcher，例如 `reports/**`
- invocation matcher，例如工具版本、命令前缀、固定工作目录或网络域名
- 来源 ApprovalRequest 和创建时间

用户点击“允许类似”默认创建 Run Grant。只有 UI 明确展示“允许本任务……”且用户主动选择时才创建 Task Grant。Task Grant 仍限同一 Task，不跨历史对话。

首次创建、修改和删除必须分别经过 effect 分类；允许写入 `reports/**` 不自动允许删除该目录，除非授权范围明确包含 `workspace_delete`。

### 使用短生命周期 Sandbox 加 Task Workspace

每个 Task 拥有持久隔离 Workspace，多个 Run 共享。Sandbox 容器仍按 ToolCall 短生命周期创建，从而保留故障隔离和资源回收能力。

```text
Task / Conversation
└── Workspace Volume
    ├── current files
    ├── dependency state
    └── Run checkpoints
          ▲       ▲       ▲
          │       │       │
       Bash     Chart    File tool
       sandbox   sandbox  sandbox
```

Web Search 等不需要文件的工具不挂载 Workspace；读取工具只读挂载；已获写权限的工具读写挂载。容器结束不删除 Task Workspace。删除 Conversation/Task 时按保留策略清理 Workspace 和相关快照。

### 将 Workspace 内容视为不可信数据

Workspace 中的文件可能来自用户上传、网页下载、Library 恢复、依赖包或前序 Agent 输出。任何来源都不能自动获得比普通不可信输入更高的信任级别。

Workspace 文件可以作为任务数据或项目上下文提供给模型，但其中的指令不得：

- 修改系统、开发者、平台或 Run policy；
- 直接授予工具、网络、工作区写入或敏感数据权限；
- 伪造用户审批、Grant 或 ActionEffectPlan；
- 要求控制面读取宿主秘密、Docker socket、其他 Task 或内部 API；
- 绕过 `plan_only`、`request_approval` 或 Sandbox 限制。

项目级说明文件可以影响任务实现偏好，但只作为低信任上下文；其请求的任何副作用仍必须经过正常 effect 分析和权限决策。

### 控制面与工作区执行面分离

Agent 编排器、Tool Router、effect analyzer、审批校验和 Workspace 管理服务运行在控制面，不从 Task Workspace 加载代码、插件、Python 模块、Node 模块或配置。

工具 Sandbox 使用固定且验证过的只读 runtime image 和入口点。运行时：

- 使用显式最小环境变量，不继承宿主环境；
- 使用固定系统 `PATH`，不包含 `.` 或 Workspace 可写目录；
- `HOME`、XDG 配置、缓存和临时目录指向隔离位置；
- 不读取 Workspace 中的 `.bashrc`、`.profile`、shell rc、编辑器配置或语言级自动启动文件；
- 不挂载 Docker socket、宿主凭据、SSH agent、云凭据或 Astra 内部控制接口；
- 默认 `no-new-privileges`、drop capabilities、非 root、只读 rootfs、PID/CPU/内存/时间限制；
- Workspace 默认使用 `nodev`、`nosuid`，并尽可能使用 `noexec`；确需执行项目代码时由受控解释器显式读取文件，而不是依赖工作区可执行位。

工具运行时二进制和协议文件必须位于只读镜像路径，不允许 Workspace 文件以同名覆盖。

### 防止隐式代码执行

读取、索引、预览和 Manifest 扫描不得执行文件内容。对可能隐式执行项目代码的操作使用单独 effect：

- 包管理器生命周期脚本；
- Git hooks、filters、external diff 和自定义配置；
- Makefile、任务脚本、测试收集和语言插件；
- Python `sitecustomize`、Node preload、shell rc 等自动加载机制；
- 文档宏、HTML/JavaScript、SVG active content 和媒体解析器插件。

依赖安装默认禁用生命周期脚本；如果任务确实需要 install/build scripts，则作为 `process_execute_unknown` 或更具体的副作用行动单独分析和审批。Git 操作使用隔离的系统配置并禁用 hooks、filters、pager 和外部命令。

文件名和工具参数始终通过结构化参数或 argv 传递；除显式 `bash_execute` 外，不将 Workspace 路径拼接进 shell 字符串。即使文件名包含换行、前导 `-`、命令替换文本或控制字符，也只能被当作数据。

### 防止文件系统逃逸和资源攻击

Workspace 服务在写入、恢复、解压、扫描和挂载时拒绝：

- 逃逸 Workspace 根目录的 `..`、绝对路径和路径规范化差异；
- 指向 Workspace 外的符号链接、硬链接、设备节点、FIFO 和 socket；
- archive path traversal、symlink pivot、压缩炸弹和超额解压；
- 超过文件数、目录深度、单文件、总字节、inode、checkpoint 或变更数量配额的输入；
- 利用大小写、Unicode 规范化或保留文件名制造的路径混淆。

Manifest 和 Artifact 安全检查使用不跟随链接的文件描述符或等价安全原语，并验证检查对象与实际读取或交付对象具有相同身份，降低 TOCTOU 风险。

### Library promotion 不提升信任

保存到 Library 时创建不可变、带来源和校验和的安全快照。Library 文件复制或引用到新 Task 后仍是数据，不获得执行权限；如果后续需要运行其中的脚本、宏或项目代码，必须重新生成 effect plan 并按当前 Run/Task 权限处理。安全扫描结果可以复用，但不能替代执行审批。

### 每个 ToolCall 记录工作区变更

执行前后生成 bounded manifest，并记录：

- relative path
- created、modified、deleted
- MIME、size、checksum
- ToolCall、Run 和 checkpoint
- security status
- 是否为候选交付物

删除使用 tombstone 保存，不能只依赖最终目录扫描。Run 完成后生成 checkpoint 和本次变更摘要；Task 当前文件视图来自最新 checkpoint。

Workspace File 与 Artifact 分离：Workspace File 表示任务中的全部文件；Artifact 表示经过安全检查、可预览或交付给用户的文件。图片、HTML、PDF、Markdown、源码和常用数据格式使用各自安全预览策略。

### 审批面板以行为为中心

面板默认展示行为标题、受影响资源、危险原因、工作目录、网络范围和命令摘要；工具名、版本、完整参数和权限集合放在详情区。

按钮文案由后端返回的安全 grant proposal 决定，例如：

- `允许本次`
- `允许当前运行写入 reports/`
- `允许本任务写入 reports/`
- `拒绝`

无法安全生成相似 matcher 时，只提供一次性允许和拒绝。

## Risks / Trade-offs

- [权限模型过于复杂导致错误配置] → 默认最小权限、deny 优先、类型化策略 schema、静态校验、Policy Explain 和变更前 simulation。
- [Agent 使用用户高权限身份形成 confused deputy] → 独立 Agent identity、明确 on-behalf-of 链、资源限定临时令牌和不可委托权限。
- [敏感数据经多个安全工具组合后外泄] → DataFlowState 追踪读取来源和标签，在具备外发能力时重新授权，不按单个工具孤立判断。
- [第三方 MCP/插件谎报只读或变更行为] → annotations 仅作提示，工具来源和 schema 固定，宿主 effect analyzer 与 enforcement 提供保证。
- [子 Agent 或 reviewer 权限升级] → 权限衰减委托、Reviewer 与执行身份分离，禁止自我批准。
- [无人值守任务卡住或静默提权] → 预先声明 Permission Bundle，ask 变暂停/拒绝，所有异常 fail closed。
- [Bash 静态分析不完整] → 未知命令保守分类，执行时用只读/读写挂载和网络策略强制实际能力。
- [Task Grant 范围过宽] → 必须由后端生成、UI 明示资源范围、用户明确选择，且写入不隐含删除权限。
- [Workspace 长期积累] → 设置文件数、总字节、单文件、checkpoint 和保留期限配额。
- [Manifest 扫描成本] → 使用 bounded manifest、增量索引和变更检测，超限时阻止写入或降级为明确错误。
- [同一 Task 并发 Run 冲突] → 第一阶段对可写 Run 使用 Task Workspace 写锁；只读 Run 可并发。
- [Workspace 内容诱导 Agent 或工具绕过策略] → Workspace 永远是低信任数据，不能授予权限；控制面不加载 Workspace 代码，所有副作用仍经过冻结 effect plan。
- [依赖或项目配置触发隐式执行] → 净化环境和 Git/语言配置，默认禁用生命周期脚本、hooks 和自动加载；需要执行时单独分析审批。
- [链接、归档或恶意文件名逃逸] → 不跟随链接的安全文件操作、路径规范化、类型拒绝、结构化 argv 和解压配额。
- [恶意 Workspace 耗尽系统资源] → Task、Run、ToolCall 和解压多层配额，控制面扫描有界且可中止。
- [`plan_only` 名称与可调用只读工具看似矛盾] → UI 文案解释为“调研并制定计划，不执行任何副作用操作”，并在结果中区分已完成的调查与尚未执行的变更。

## Migration Plan

1. 引入统一 PermissionRequest/Decision、策略层级、Agent identity 和 audit schema；先以 shadow mode 对现有调用记录决策，不改变行为。
2. 引入 effect plan schema、analyzer 接口和新的执行模式决策表，同时保持旧 ToolSpec 字段兼容。
3. 先让现有只读 Web 工具生成显式 read-only effect；再覆盖 Bash、Chart 和文件工具。
4. 增加 Task Workspace、挂载权限、变更 Manifest 和 checkpoint，不立即开放宿主工作区。
5. 在开放可写 Workspace 前完成控制面隔离、环境净化、链接/归档防护和恶意内容安全测试。
6. 增加 Credential Broker、DataFlowState、Tool Catalog Snapshot 和 MCP/插件信任控制。
7. 扩展 ApprovalRequest/Grant 支持 effect plan、Run/Task scope、资源 matcher、租约、撤销和解释。
8. 部署新的审批面板、权限中心与工作区输出视图。
9. 在策略 shadow 对比、权限矩阵和安全测试通过后切换为强制模式，并移除旧的“每个工具都审批”策略。

## Open Questions

- Task Workspace 是否需要支持用户上传的初始文件快照，以及上传文件的只读/可修改策略。
- 对依赖安装产生的大量文件，是否应单独使用 dependency volume，避免最终文件列表被 `node_modules` 或 `.venv` 淹没。
- 哪些高影响外部操作需要强制双人复核，而不能仅由单个用户 allow-once。
- Astra 首期是否接入企业身份系统签发 Agent identity，还是先使用内部 identity 并保留外部映射。

## Industry References

- [OpenAI Codex Agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security) 将 Sandbox mode 与 Approval policy 视为互补层，并支持托管 requirements、受保护路径、网络 allowlist、项目 trust 和独立 auto-reviewer。
- [Claude Code permissions](https://code.claude.com/docs/en/permissions) 使用 deny → ask → allow 规则、托管不可覆盖设置、只读 Plan mode、Sandbox 强制边界和项目/扩展 trust。
- [Gemini CLI Trusted Folders](https://google-gemini.github.io/gemini-cli/docs/cli/trusted-folders.html) 和 [Enterprise controls](https://google-gemini.github.io/gemini-cli/docs/cli/enterprise.html) 使用 trusted folders、安全模式、工具 allowlist 和 Sandbox，未受信目录不会加载项目设置和自动授权。
- [GitHub Copilot cloud agent firewall](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-firewall) 使用默认网络防火墙、组织/仓库分层 allowlist，并明确网络控制只是深度防御的一层。
- [MCP Tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) 要求把第三方 tool annotations 视为不可信提示，不能作为授权保证。
- [Microsoft Entra Agent ID authorization](https://learn.microsoft.com/en-us/entra/agent-id/authorization-agent-id) 与 [Agentic Zero Trust guidance](https://learn.microsoft.com/en-us/security/zero-trust/sfi/manage-agentic-risk) 强调 Agent 独立身份、最小权限、资源范围、生命周期、审计和供应链治理。
- [OWASP Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) 强调最小化 Agent 功能、权限和自治范围，避免用高权限共享账户连接工具。
