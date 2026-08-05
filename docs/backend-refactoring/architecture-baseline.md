# 后端架构重构基线

基线日期：2026-08-04。工作目录为 `backend/`，解释器为项目 `.venv`。

## 可重复生成方式

```bash
.venv/bin/python scripts/analyze_backend_architecture.py --format json
.venv/bin/python scripts/analyze_backend_architecture.py --format markdown --limit 30
```

JSON 输出是完整清单，包含每个生产模块的行数、公共符号、内部 imports，以及每个函数/方法的位置、长度和圈复杂度。Markdown 输出用于人工审阅热点。分析器只依赖 Python AST，避免本地与 CI 采用不同统计定义。

## 总量

| 指标 | 基线 |
| --- | ---: |
| 生产 Python 行数 | 54,668 |
| 生产模块 | 166 |
| 公共符号 | 1,135 |
| 函数与方法 | 1,535 |
| 收集的测试 | 820 |
| 通过 / 跳过 | 812 / 8 |

## 最大生产模块

| 行数 | 模块 | 公共符号 | 内部 imports |
| ---: | --- | ---: | ---: |
| 3,292 | `app.application.runner.agent_loop` | 7 | 39 |
| 2,685 | `app.infrastructure.repositories.runs` | 6 | 9 |
| 1,841 | `app.application.runner.model_client` | 20 | 11 |
| 1,636 | `app.infrastructure.db.models` | 57 | 0 |
| 1,582 | `app.infrastructure.tools.web` | 32 | 3 |
| 1,249 | `app.application.memory.consolidation` | 19 | 3 |
| 1,176 | `app.common.schemas.agent` | 94 | 2 |
| 1,075 | `app.infrastructure.repositories.memory_consolidation` | 7 | 3 |
| 1,047 | `app.application.subagents.executor` | 3 | 18 |
| 986 | `app.interfaces.api.skills` | 26 | 14 |
| 984 | `app.infrastructure.repositories.memories` | 1 | 2 |
| 976 | `app.application.runner.planning` | 7 | 4 |
| 952 | `app.application.runner.engine` | 6 | 22 |

## 最大与最复杂函数

| 行数 | 复杂度 | 函数 |
| ---: | ---: | --- |
| 2,372 | 374 | `app.application.runner.agent_loop.AgentLoop.run` |
| 477 | 26 | `app.application.runner.node_worker.ReadOnlyAgentNodeExecutor.__call__` |
| 340 | 49 | `app.application.subagents.executor.LocalAstraAgentExecutor.execute` |
| 324 | 66 | `app.infrastructure.repositories.runs.run_to_view` |
| 264 | 48 | `app.application.skills.packages.parse_skill_package` |
| 260 | 75 | `app.application.workspaces.deliverables.DeliverableCatalog.list` |
| 238 | 28 | `app.infrastructure.repositories.memory_consolidation.MemoryConsolidationRepository.publish` |
| 227 | 12 | `app.application.subagents.executor.LocalAstraAgentExecutor._call_tool` |
| 216 | 55 | `app.application.runner.agent_loop.ContextAssembler.assemble` |
| 214 | 42 | `app.application.subagents.context.SubagentContextComposer.compose` |
| 200 | 31 | `app.application.runner.engine.RunEngine._run_with_repo` |
| 184 | 57 | `app.infrastructure.repositories.runs.RunRepository.decide_approval` |
| 179 | 72 | `app.application.runner.reasoning.CompletionGate.evaluate` |

复杂度是以 1 为基础，对分支、循环、异常分支、布尔分支、match 分支和 comprehension 分支累加的确定性近似值。它用于发现热点和防止恶化，不替代设计评审。

## 当前包级双向依赖

以下为直接的双向包依赖，均纳入迁移清单：

- `api ↔ scheduling`
- `conversation_context ↔ runner`
- `grounding ↔ schemas`
- `memory ↔ repositories`
- `permissions ↔ plugins`
- `permissions ↔ repositories`
- `plugins ↔ runner`
- `plugins ↔ tools`
- `repositories ↔ runner`
- `repositories ↔ scheduling`
- `root_context_compaction ↔ runner`
- `runner ↔ subagents`
- `subagents ↔ tools`

包级统计会把不同能力的子模块合并，因此只能用于导航；最终门禁必须在模块级报告完整违规路径。

## 绿色行为与数据基线

| 检查 | 基线结果 |
| --- | --- |
| Ruff | `All checks passed` |
| Pytest | `812 passed, 8 skipped in 30.44s` |
| OpenAPI paths / operations / schemas | 93 / 110 / 198 |
| OpenAPI canonical SHA-256 | `904475c47d2d55691ee1e78df214d29579ba4b8461297213fa4e85aaeace8465` |
| ORM tables | 54 |
| ORM table-name SHA-256 | `fda6533f5277bb7e75828ed01b8180e57150627e33a43baa0107396c9d63a84f` |
| Alembic head | `0006_runtime_profiles` |
| Fresh SQLite upgrade + `alembic check` | no new upgrade operations detected |

`alembic check` 当前会报告 `agent_executions`、`node_executions`、`plan_nodes` 和 `plans` 外键环导致的 SQLAlchemy 排序 warning；这是已记录基线，不是本次 ORM 拆分可以忽略的新错误。
