## MODIFIED Requirements

### Requirement: 实际推理配置可观测
系统 SHALL 为每次模型调用记录实际选用的推理适配器、是否应用参数、思考正文可见性以及降级原因，并 MUST NOT 记录 API 凭据、提示正文、签名、加密思考或供应商未公开的隐藏思维链。仅当用户为 Run 开启模型思考且供应商明确返回可见文本时，系统 SHALL 通过受限的模型思考事件保存该文本。

#### Scenario: 调用应用了 Provider 参数
- **WHEN** 模型调用成功应用推理参数
- **THEN** 对应 ModelInvocation usage metadata 包含适配器标识、已应用配置和思考正文可见性

#### Scenario: 调用降级为基础请求
- **WHEN** 当前 Provider/模型或传输不支持推理参数
- **THEN** usage metadata 记录降级原因且调用继续执行

#### Scenario: 兼容传输返回可见思考正文
- **WHEN** 已开启思考的兼容响应通过已声明字段返回文本
- **THEN** 传输只把该字段交给模型思考回调，并继续独立解析普通模型输出

