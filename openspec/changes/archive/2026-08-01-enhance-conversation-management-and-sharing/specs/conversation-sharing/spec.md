## ADDED Requirements

### Requirement: User can create one active share link per conversation
系统 SHALL 允许用户为终态 Conversation 创建一个活动的高熵不可预测分享链接；重复打开分享操作 SHALL 返回现有活动链接，不得无提示创建多个并行链接。

#### Scenario: Create first share
- **WHEN** 用户分享一个至少包含一条已完成可见消息的 Conversation
- **THEN** 系统创建公开快照并返回可复制的 `/share/<token>` 链接

#### Scenario: Reopen existing share
- **WHEN** Conversation 已有活动分享且用户再次打开分享
- **THEN** 系统返回同一活动链接和快照更新时间

### Requirement: Share content is an explicit snapshot
分享快照 SHALL 仅在创建或用户主动更新时从当时已完成的可见对话内容生成；分享之后新增的消息 MUST NOT 自动出现在现有快照中。

#### Scenario: Conversation continues after sharing
- **WHEN** 原 Conversation 在分享创建后新增一轮消息
- **THEN** 公开链接仍显示创建分享时的快照

#### Scenario: User updates share
- **WHEN** 用户主动更新已有分享
- **THEN** 系统使用当前已完成可见消息替换快照并保持原链接有效

### Requirement: Public share response is security filtered
公开分享接口 SHALL 使用独立白名单 DTO，仅返回标题、已完成的用户与 Assistant 消息及分享时间；MUST NOT 返回推理事件、过程消息、工具原始输入输出、Memory、Agent Profile、凭据、内部 ID、本地路径或 Artifact 内容地址。

#### Scenario: Shared run contains internal audit data
- **WHEN** 分享来源 Run 包含工具调用、推理事件、Memory、Agent Profile 和本地 Artifact
- **THEN** 公开响应不包含这些字段或其内容

#### Scenario: Share contains incomplete response
- **WHEN** 来源 Conversation 最后一条 Assistant 响应仍在生成或没有完成
- **THEN** 快照排除该未完成响应

### Requirement: Share access is anonymous and read-only
获得活动分享 token 的访问者 SHALL 无需登录即可只读访问独立分享页面，且该页面 MUST NOT 提供继续原 Conversation、修改内容、调用工具或访问主应用审计能力。

#### Scenario: Anonymous visitor opens active share
- **WHEN** 未认证访问者打开有效 `/share/<token>`
- **THEN** 页面显示只读快照且不渲染主应用侧栏、Composer 或执行控件

### Requirement: Share can be revoked and is coupled to source deletion
用户 SHALL 能撤销活动分享；撤销分享或删除原 Conversation 后，旧 token MUST 立即失效。撤销后重新分享 SHALL 生成不同 token。

#### Scenario: Revoke share
- **WHEN** 用户撤销一个活动分享
- **THEN** 公开接口对旧 token 返回不存在且原 Conversation 保持可用

#### Scenario: Delete source conversation
- **WHEN** 用户删除分享来源 Conversation
- **THEN** 系统同时删除或撤销分享，旧 token 不再可访问

#### Scenario: Share again after revocation
- **WHEN** 用户在撤销后再次分享同一 Conversation
- **THEN** 系统生成与已撤销 token 不同的新链接
