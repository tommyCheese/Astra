## Why

当前前端会把一次 Run 中所有已验证 Artifact 集中渲染在最终答案下方，虽然 Artifact 已保存 `tool_call_id` 和 `sandbox_job_id`，但答案的 `findings` 没有表达“哪个结论由哪个工具输出支撑”。当一轮对话多次生成图表、文件或交互页面时，用户无法快速判断每个输出属于哪段结论，结果数量增加后也会形成与上下文脱节的堆叠展示。

## What Changes

- 为最终答案中的结论增加结构化工具输出引用，使 finding 能按稳定 Artifact ID 关联一个或多个输出。
- 在后端最终化与验证阶段校验 Artifact 引用只指向当前 Run、已验证且可访问的输出，并对无效引用生成审计 warning。
- 在前端按照答案顺序，将关联的图片、HTML 和文件紧邻对应 finding 展示，而不是统一堆放在答案底部。
- 在思考过程内保留 ToolCall 顺序，并允许用户从工具调用定位到其产出的 Artifact 或对应答案结论。
- 对未被任何 finding 引用的已验证 Artifact 提供明确的“其他输出”降级区域，确保旧数据、部分模型输出和非结论型文件不会丢失。
- 在流式 summary 阶段保持当前低延迟文本展示；完整 Run 到达后原子切换为结构化结果，避免流式过程中因 Artifact 关系尚未确定而反复跳动。
- 为纯文本回答、单一图表、多图表、多 Artifact 类型、重复引用、无效引用和旧 Run 数据增加兼容与展示测试。

## Capabilities

### New Capabilities

- `contextual-tool-output-presentation`: 定义最终答案与工具输出的关联协议、后端引用完整性验证、按结论就近渲染、过程定位以及未关联输出的降级展示。

### Modified Capabilities

无。

## Impact

- 后端结果 schema：`FinalAnswer`、`Finding`、验证报告和 RunView 输出。
- Agent Loop：最终化提示、Artifact 引用校验、Evidence Pack 审计引用和 Completion 结果 warning。
- Artifact 与 ToolCall 关系：继续复用现有 `artifact.id`、`tool_call_id`、`sandbox_job_id`，不改变工具执行协议。
- 前端类型与展示：`frontend/src/types.ts`、`App.tsx` 中的 FinalAnswer、ProcessPanel、ArtifactGallery 以及响应式样式。
- 兼容性：旧 Run 和未返回 Artifact 引用的模型输出继续可读，未关联 Artifact 进入统一降级区域；不要求数据库破坏性迁移。
- 测试：后端 schema/验证/Agent Loop 测试与前端多输出关联展示测试。
