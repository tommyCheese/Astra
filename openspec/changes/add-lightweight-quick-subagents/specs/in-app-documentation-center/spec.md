## ADDED Requirements

### Requirement: 帮助中心独立说明快速与可信模式
系统 SHALL 在应用内帮助文档提供可独立导航的“快速模式与可信模式”主题，并 SHALL 以当前产品行为完整说明两种模式的定义、共享能力、规划与验证差异、Subagent 行为、适用场景和选择建议。

#### Scenario: 用户打开回答模式帮助主题
- **WHEN** 用户在帮助中心选择“快速模式与可信模式”
- **THEN** 页面显示两种模式的定义、执行流程和逐项比较
- **THEN** 页面说明两种模式共享工具安全边界与 Subagent Supervisor，但不会将可信模式描述为绝对正确保证

#### Scenario: 用户查看 Subagent 模式差异
- **WHEN** 用户导航到该主题的 Subagent 章节
- **THEN** 页面说明快速 Subagent 无规范根 DAG、可信 Subagent 受 TaskContract 与 Plan DAG 约束
- **THEN** 页面说明两者都使用独立 child 上下文和共享受治理运行时

#### Scenario: 用户在长文章中使用页内目录
- **WHEN** 用户打开任一帮助主题并滚动正文
- **THEN** 桌面端在正文右侧显示独立的粘性页内目录，而不是把目录放在文章开头
- **THEN** 窄屏端使用保持可见且可横向滚动的紧凑目录

### Requirement: 帮助中心提供关于 Astra 的项目信息
系统 SHALL 在应用内帮助文档提供可独立导航的“关于 Astra”主题，并 SHALL 说明 Astra 的创建动机、使命、核心原则和版权许可证信息；版权表述 MUST 以仓库现有许可证和项目元数据为依据，不得虚构版权主体。

#### Scenario: 用户打开关于 Astra
- **WHEN** 用户在帮助中心选择“关于 Astra”
- **THEN** 页面说明 Astra 为什么被创建、希望解决的问题和长期使命
- **THEN** 页面展示 Apache License 2.0、权利人和贡献者版权归属、使用条件、免责声明以及完整许可证入口

#### Scenario: 仓库没有单一版权主体
- **WHEN** 项目元数据和 NOTICE 未声明单一版权主体
- **THEN** 页面使用“各自权利人和贡献者”的中性表述
- **THEN** 页面不推断个人、组织或版权年份
