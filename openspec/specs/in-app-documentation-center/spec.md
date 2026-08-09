# in-app-documentation-center Specification

## Purpose
TBD - created by archiving change add-in-app-documentation-center. Update Purpose after archive.
## Requirements
### Requirement: Global help documentation entry
The Astra application SHALL expose a clearly labelled help documentation control in the persistent application navigation and SHALL indicate when the documentation center is the active view.

#### Scenario: Open documentation from the application shell
- **WHEN** a user activates the “帮助文档” control from any primary application view
- **THEN** Astra displays the in-app documentation center and marks the help control as active

### Requirement: Context-preserving documentation navigation
The documentation center SHALL open without navigating to an external site or clearing the current task state, and SHALL return the user to the view from which it was opened when closed.

#### Scenario: Return to the previous view
- **WHEN** a user opens the documentation center from settings and then activates the close control
- **THEN** Astra returns to settings with the existing application state preserved

#### Scenario: Direct fallback
- **WHEN** the documentation center has no valid previous view and the user closes it
- **THEN** Astra returns to the chat view

### Requirement: Memory is the initial documentation topic
The documentation center SHALL use an extensible topic navigation model and SHALL select “记忆” as its initial and default topic.

#### Scenario: First visit
- **WHEN** a user opens the documentation center
- **THEN** the topic navigation identifies “记忆” as selected and the memory article is visible

### Requirement: Memory documentation explains the complete lifecycle
The memory article SHALL explain the background and user problem, the distinction between `MEMORY.md`, runtime settings, saved memory records, audit activity, and AutoDream, memory production, activation and recall timing, recall modes and safeguards, supported scopes, AutoDream supersession behavior, and common misconceptions.

#### Scenario: Understand when saved memory affects an answer
- **WHEN** a user reads the memory article
- **THEN** the article distinguishes production, active storage, eligible retrieval, and prompt injection instead of implying that every saved memory affects every answer

#### Scenario: Understand scope
- **WHEN** a user consults the scope section
- **THEN** the article explains run, task, workspace, and user scope with both matching boundaries and practical examples

#### Scenario: Understand AutoDream
- **WHEN** a user consults the AutoDream section
- **THEN** the article explains that successful consolidation creates a replacement version and supersedes source memories rather than hard-deleting them

### Requirement: Documentation center is readable and accessible
The documentation center SHALL provide semantic navigation and article landmarks, keyboard-operable controls, visible focus states, and a responsive single-column layout on narrow screens.

#### Scenario: Narrow viewport
- **WHEN** the documentation center is rendered at a narrow viewport width
- **THEN** topic navigation and article content remain readable without requiring horizontal page scrolling

#### Scenario: Assistive navigation
- **WHEN** a user navigates the documentation center with a keyboard or assistive technology
- **THEN** the help entry, topic selection, article sections, and close control expose meaningful accessible labels and states

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

