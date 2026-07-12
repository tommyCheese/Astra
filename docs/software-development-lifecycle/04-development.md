# 开发与代码管理

## 1. 开发前

确认需求和验收标准已批准；较大变更存在完整 OpenSpec proposal/design/specs/tasks；识别受影响 API、数据库、权限、配置、遥测和文档；建立最小可交付切片。不要在任务边界不清时用大量实现替代设计讨论。

## 2. 本地环境

后端基线：Python 3.9+、FastAPI、SQLAlchemy、Alembic。前端基线：Node.js、React、TypeScript、Vite。常用流程：

```bash
cd backend
cp .env.example .env
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd frontend
npm ci
npm run dev
```

密钥只进入本地环境变量或密钥系统，不提交 `.env`、数据库文件、Artifact、日志或真实用户数据。

## 3. 实现规范

- 业务规则放在明确领域/运行时边界，不散落在 API handler 和 UI。
- 模型输出始终按不可信输入解析、规范化和验证。
- 错误使用稳定类别与安全消息，内部细节通过 trace ID 关联日志。
- 外部调用设置 timeout；重试遵守幂等性与预算。
- 数据写入在清晰事务边界内完成，事件与状态避免不一致。
- 新配置提供安全默认值、说明、验证和环境差异。
- 新能力同时加入测试、日志/指标、运行文档和回滚路径。
- 注释解释“为什么”和约束，不重复代码表面行为。

## 4. 分支、提交与评审

分支保持短生命周期；提交应单一目的、可构建、可审查。提交消息说明意图，重大 schema/API 变化在正文说明迁移影响。Pull Request 至少包含：关联需求/OpenSpec、变更摘要、验证证据、风险、安全/隐私影响、数据库和配置变化、截图或 API 示例、发布与回滚方式。

评审重点不是风格挑错，而是正确性、边界、失败模式、安全、测试充分性、可维护性和需求一致性。作者不能自行批准高风险例外；安全敏感、迁移或公共协议变更需相应 owner 评审。

## 5. 数据库变更

每次 ORM 变化必须有 Alembic migration，并在空库升级、已有数据升级和目标数据库上验证。禁止依赖 ORM 自动建表替代生产迁移。破坏性操作分阶段执行，回填可暂停、可重入、有进度指标。回滚若不能逆转数据，必须明确采用前向修复和备份恢复。

## 6. Agent 与模型变更

模型名、provider、system prompt、tool manifest、策略编译和完成门禁的变化都视为行为变更。除单元测试外，应在固定评测集比较成功率、错误类型、工具调用、延迟和成本。保存评测版本、采样参数和结果，避免只用少量“看起来不错”的示例批准上线。

模型不得自行扩大权限或宣布强制准则通过。对 `waiting_user`、`blocked`、预算耗尽、失败 fingerprint、来源冲突和低质量证据建立回归用例。

## 7. 完成定义

代码完成要求：实现符合规范；新增/修改测试通过；静态检查通过；迁移已验证；安全和隐私检查完成；文档同步；监控和告警可用；feature flag、发布和回滚方案明确；评审意见关闭；无未记录的高风险 TODO。

仓库当前可执行的基础检查：

```bash
cd backend && ruff check . && pytest
cd frontend && npm run lint && npm test && npm run build
```

若 CI 尚未配置这些步骤，应把建立 CI 作为发布到共享环境前的阻断任务。
