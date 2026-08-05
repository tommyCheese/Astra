# Astra Token 消耗与性能

本章说明如何在同一模型、同一环境和相同任务下，对比 Astra 简单模式
（`standard`）与可信模式（`trusted`）的 Token 消耗和端到端完成时延。

## 先理解统计边界

Astra 的权限判断、Schema 校验、Artifact/Evidence 引用检查和 Completion Gate
主要由本地确定性代码执行，它们本身不会调用模型。可信模式的额外 Token 主要来自：

1. 生成 Task Contract；
2. 生成并执行 Plan DAG；
3. 任务需要时进行额外决策、反思、重规划或最终综合。

因此不能用一个固定百分比描述所有任务。短任务中固定规划开销占比更高；复杂任务则可能
因可信计划减少无效工具调用，也可能因反思和重规划增加消耗。应使用配对基准测量实际模型。

## 配对基准 Case

基准内置三个不依赖 Web、文件或其他外部工具的友好 Case，降低网络和工具可用性带来的噪声：

| Case | 任务特征 | 用途 |
|---|---|---|
| `short_explanation` | 三句话和极短伪代码 | 观察小任务的固定治理开销 |
| `structured_comparison` | 有界表格和一句建议 | 观察结构化交付约束的成本 |
| `bounded_checklist` | 恰好五项且限制长度 | 观察明确成功条件的规划与校验成本 |

每次重复都对同一 Case 依次运行 `standard` 和 `trusted`。执行顺序每轮反转，避免供应商
负载随时间变化时总是偏向同一模式。所有 Run 串行执行；可信模式显式使用自动执行计划，
不会把等待人工确认的时间计入结果。

## 运行方法

先启动 Astra 后端，并配置需要评估的真实模型。供应商必须返回 Token usage；内置
`mock` provider 不报告真实 Token，不能用于得出 Token 比例。

```bash
cd backend
python -m benchmarks.mode_performance --runs-per-case 3 --warmup 1
```

常用选项：

- `--case short_explanation`：只运行一个 Case；默认运行全部 Case。
- `--base-url http://127.0.0.1:8000`：指定后端地址。
- `--keep-runs`：保留基准创建的对话；默认完成统计后清理。
- `--allow-incomplete-usage`：仅用于排查不完整 usage；此时 Token 结果不得用于比较。
- `--timeout 300`：为慢模型提高单次请求超时。

一次默认测量包含 3 个 Case × 3 次重复 × 2 种模式，共 18 个计量 Run，另有两次
预热 Run。若模型价格较高，可先用单 Case 和单次重复做冒烟验证：

```bash
python -m benchmarks.mode_performance \
  --case short_explanation \
  --runs-per-case 1 \
  --warmup 0
```

## 输出与解读

命令输出 JSON。`summary.standard` 和 `summary.trusted` 分别包含以下指标的
mean、p50 和 p95：

- `model_invocations`：模型调用次数；
- `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_tokens`；
- `total_tokens`：供应商上报的总 Token；
- `complete_ms`：从提交请求到 `answer.completed` 的端到端时延；
- `minimum_usage_coverage`：样本内最低 Token 上报覆盖率。

`summary.comparison` 是所有配对样本的加权汇总：

- `trusted_token_ratio`：可信模式总 Token ÷ 简单模式总 Token；
- `trusted_token_overhead_percent`：可信模式相对 Token 增幅；
- `trusted_latency_ratio` 和 `trusted_latency_overhead_percent`：对应的时延指标。

例如 `trusted_token_ratio = 1.35` 表示本次受控样本中，可信模式使用了简单模式的
1.35 倍 Token，即增加 35%。`summary.cases` 还会给出每个 Case 的独立结果，避免汇总值
掩盖短答案和结构化答案之间的差异。

只有在 `minimum_usage_coverage = 1.0`、两种模式均成功完成，并且模型、模型参数、
Astra 配置与工具开关一致时，Token 比例才可用于结论。默认情况下，只要任一模型调用
缺失 usage，基准会直接失败而不是把缺失值当作零。

## 建议的报告方式

至少运行三次重复，并同时报告：

1. 模型供应商、模型名和采集时间；
2. 每个 Case 的两种模式 Token mean/p50/p95；
3. 总体 Token ratio 和 overhead percent；
4. 完成时延、模型调用数和 usage coverage；
5. 是否启用 Memory、Subagent、模型思考及缓存。

不要把不同任务的历史 Run 平均值直接相除，也不要把本地确定性校验耗时误称为模型
Token。供应商缓存命中会降低计费输入，但不一定降低逻辑输入长度，因此报告中应单独保留
`cached_input_tokens`。

## 基准自身的自动化校验

基准的配对完整性、百分位数和增幅计算由测试覆盖：

```bash
cd backend
python -m pytest -q tests/test_mode_performance_benchmark.py
python -m ruff check benchmarks/mode_performance.py tests/test_mode_performance_benchmark.py
```

这些测试使用确定性样本验证统计逻辑，不冒充真实模型性能数据。发布或性能评审时仍应运行
上述端到端配对命令。
