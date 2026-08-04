# 后端架构与可读性门禁

统一质量命令：

```bash
cd backend
PYTHON=.venv/bin/python bash scripts/check_backend_quality.sh
```

该命令依次运行架构检查、Ruff、快速契约测试和当前 OpenSpec strict validation。CI 使用同一脚本，然后运行完整测试集。

## 门槛

| 范围 | 默认预算 | 硬限制 |
| --- | ---: | ---: |
| 生产模块 | 500 行 | 800 行 |
| 生产函数/方法 | 60 行 | 100 行 |
| 函数圈复杂度 | 10 | 15 |

历史超限项保存在 `backend/architecture-baseline.json`，只能减少或保持，任何增长都会失败。新代码超过硬限制直接失败；超过默认预算但未超过硬限制时，必须在 `backend/architecture-exceptions.json` 添加：

- 精确模块或 `module:qualified_function` 符号；
- 对该职责负责的 owner；
- 不能立即拆分的具体原因；
- ISO 日期格式的失效期限。

失效、缺少 owner 或原因的例外无效。最终迁移阶段会删除全部历史超限基线，硬限制不会成为永久豁免。

## 依赖规则

当前门禁冻结全部模块级循环，并重点禁止增加以下反向依赖：

- `app.scheduling -> app.api`
- `app.repositories -> app.runner`
- `app.runner -> app.subagents`
- `app.subagents -> app.runner`

检查器保存的是实际模块边和互相可达的循环模块对。删除依赖永远允许；新增 forbidden edge 或让此前独立的模块进入同一循环会失败，并输出完整模块名称。

## 类型边界

新架构的 `app.bootstrap`、`app.execution`、`app.planning` 和 `app.run_management` 公共函数/方法必须完整标注参数与返回类型。范围随着能力迁移扩大。Ruff 同时拒绝无错误码的 `type: ignore` 和失效 suppression；架构基线禁止新增任何 `type: ignore`。

## 更新基线

只有确认主线绿色且变更在减少债务时，才能查看候选基线：

```bash
cd backend
.venv/bin/python scripts/check_backend_architecture.py --print-baseline
```

不得用重新生成基线掩盖增长或新增循环。基线修改必须与消除对应债务的代码在同一个变更中评审。
