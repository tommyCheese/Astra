# Astra 文档中心

本文档中心服务于 Astra 从产品构想到生产运营的完整生命周期。文档中的 `Task`、`Run`、`ToolCall`、`Artifact` 等接口名保留英文，解释性正文统一使用中文。

## 推荐阅读路径

1. [软件开发生命周期总览](software-development-lifecycle/README.md)：阶段、责任、门禁和交付物。
2. [产品愿景与需求管理](software-development-lifecycle/01-product-and-requirements.md)：明确为什么做、为谁做、如何验收。
3. [项目计划与交付管理](software-development-lifecycle/02-planning-and-delivery.md)：拆解里程碑、风险、依赖和变更。
4. [架构与详细设计](software-development-lifecycle/03-architecture-and-design.md)：建立可演进的系统边界。
5. [开发与代码管理](software-development-lifecycle/04-development.md)：从 OpenSpec 到代码合入。
6. [测试与质量保证](software-development-lifecycle/05-testing-and-quality.md)：分层验证功能与非功能质量。
7. [发布、部署与回滚](software-development-lifecycle/06-release-and-deployment.md)：安全地把变更送达环境。
8. [运行、事件与维护](software-development-lifecycle/07-operations-and-maintenance.md)：稳定运行和持续改进。
9. [安全、隐私与合规](software-development-lifecycle/08-security-privacy-compliance.md)：贯穿全周期的治理基线。
10. [模板与检查表](software-development-lifecycle/09-templates-and-checklists.md)：可直接复制使用的记录模板。

## 现有专题文档

- [跟着上下文流读懂 Astra Agent](agent-implementation-execution-walkthrough.md)：按真实交互顺序理解 Agent Loop Harness、工具、证据、反思、验证与前端交付。
- [一次 Run 如何返回并展示工具输出](run-result-and-contextual-tool-output.md)：沿最终化、引用校验、持久化和前端消费顺序解释结果格式与多工具输出布局。

## 文档状态约定

- **现状**：已由当前代码、配置、迁移或测试证实。
- **目标**：计划达到但尚未完整实现的能力。
- **决策**：经评审生效的约束，应记录日期、负责人和替代方案。
- **草案**：仍可讨论，不得作为已交付能力对外承诺。

文档必须与代码同版本评审。接口、数据库、权限、运行参数、部署方式或用户行为发生变化时，相应文档属于同一变更的完成条件。
