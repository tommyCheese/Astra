## Why

Astra 的记忆能力已经覆盖生产、召回、作用域、审计和 AutoDream 整理，但这些概念目前主要散落在设置项和实现细节中。用户很难仅凭界面判断一条记忆何时产生、何时真正影响回答，以及不同范围和模式之间的边界，因此需要一个与产品上下文相连、可以持续扩展的文档入口。

## What Changes

- 在 Astra 全局侧边栏增加“帮助文档”入口，并提供明确的选中状态。
- 新增内置“Astra 文档中心”，在不离开当前应用和不丢失会话上下文的情况下打开、关闭和返回。
- 文档中心采用可扩展的主题导航，首个且默认打开的主题为“记忆”。
- 首篇记忆文档说明能力背景、解决的问题、概念边界、生产流程、生效时机、召回方式、作用范围、运行模式、AutoDream 整理和常见误解。
- 为桌面端和窄屏布局提供一致的可读性、键盘可达性和中英文界面文案。

## Capabilities

### New Capabilities

- `in-app-documentation-center`: 提供 Astra 内置帮助入口、文档主题导航，以及以记忆为首个主题的产品说明页面。

### Modified Capabilities

- None.

## Impact

- Frontend application shell and view state in `frontend/src/App.tsx`.
- New documentation center component and static product documentation content under `frontend/src/`.
- Responsive documentation styles in `frontend/src/styles.css`.
- Chinese and English UI copy in `frontend/src/i18n.tsx`.
- Frontend navigation, content, accessibility, and regression tests.
- No backend API, database schema, or external documentation service is required.
