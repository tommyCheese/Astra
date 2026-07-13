# desktop-chat-materials Specification

## Purpose
TBD - created by archiving change polish-macos-chat-and-streaming-latency. Update Purpose after archive.
## Requirements
### Requirement: 分层桌面聊天界面
系统 SHALL 使用可区分的侧栏、顶部栏、会话画布和输入器材质层级，且 MUST NOT 模拟系统红绿灯或额外悬浮窗口。

#### Scenario: 桌面端打开聊天页
- **WHEN** 用户在桌面宽度打开聊天页
- **THEN** 系统显示半透明侧栏、内容画布和输入器构成的统一界面，不显示红绿灯或广告浮板

### Requirement: Astra 原生玻璃材质
系统 SHALL 使用 Astra 青绿、蓝色与暖金作为环境光和焦点色，并通过半透明表面、背景模糊、内高光和柔和阴影表达玻璃材质。

#### Scenario: 切换浅色和深色主题
- **WHEN** 用户切换主题
- **THEN** 各材质层保持足够对比度、清晰边界和一致的 Astra 色彩识别

### Requirement: 流畅且克制的交互反馈
系统 SHALL 为窗口进入、消息出现、输入器聚焦、按钮操作和流式光标提供不阻塞输入与滚动的反馈动画。

#### Scenario: 用户发送并接收流式回答
- **WHEN** 消息进入会话且回答开始流式输出
- **THEN** 消息平滑出现、输入器保持响应、自动跟随不产生明显跳动

### Requirement: 动效与材质可访问性降级
系统 MUST 响应 `prefers-reduced-motion`，并在不支持背景模糊或小屏设备上提供清晰的实色降级。

#### Scenario: 系统启用减少动态效果
- **WHEN** 用户操作系统偏好为 reduced motion
- **THEN** 系统关闭背景漂移动画和非必要位移动画，但保留状态可见性

### Requirement: 首 Token 等待态
系统 SHALL 在用户消息提交后立即显示轻量、语义明确的启动反馈，并在首个 answer delta 到达时无跳变地转换为回答内容。

#### Scenario: 模型尚未输出首 Token
- **WHEN** 运行已创建但还没有可展示的 answer delta
- **THEN** 会话显示 Astra 正在准备回答的紧凑状态而非空白或冻结界面

