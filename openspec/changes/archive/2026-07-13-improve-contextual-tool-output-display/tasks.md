## 1. 结果关联 Schema 与兼容层

- [x] 1.1 为后端 `Finding` 增加默认空列表 `artifact_ids`，并验证旧 FinalAnswer payload 仍可解析
- [x] 1.2 更新前端 `FinalResult.findings` 类型与 RunView normalize 逻辑，将缺失或非法 `artifact_ids` 归一为空列表
- [x] 1.3 更新真实模型与 MockModelClient 的最终化输出协议，要求只引用上下文中实际出现且支撑 finding 的 Artifact ID
- [x] 1.4 增加 schema 与模型 payload normalization 测试，覆盖空引用、单引用、多引用和历史 payload

## 2. 后端 Artifact 引用完整性

- [x] 2.1 实现确定性的 FinalAnswer Artifact 引用规范化函数，按 finding 内首次出现顺序去重
- [x] 2.2 只允许引用当前 Run 中 `verified` 且具有 `storage_key` 的 Artifact，并移除不存在、跨 Run、未验证和不可访问引用
- [x] 2.3 将无效引用数量与安全 warning 写入 verification notes/report，禁止回显其他 Run 元数据、storage key 或路径
- [x] 2.4 在 Agent Loop 最终 result 与 final_answer Artifact 持久化前调用引用规范化，确保 API 和审计产物使用同一结果
- [x] 2.5 增加后端测试，覆盖有效引用、重复引用、跨 Run、未知 ID、pending/expired Artifact 和无 storage key Artifact

## 3. Finding 就近输出展示

- [x] 3.1 将 ArtifactGallery 拆为可复用的局部 Gallery/ArtifactCard，并保持图片、隔离 HTML 与文件的现有安全行为
- [x] 3.2 在 FinalAnswer 中实现按 findings 顺序的首次引用消费算法，并按 `artifact_ids` 顺序就近渲染输出
- [x] 3.3 为后续 finding 对已展示 Artifact 的重复引用增加轻量定位链接，避免重复渲染主内容
- [x] 3.4 将未被 finding 消费的 verified Artifact 按 `created_at`、`id` 排序后放入“其他输出”区域
- [x] 3.5 实现“其他输出”默认展示策略：不超过 2 个时展开，超过 2 个时可折叠，并补充中英文文案
- [x] 3.6 更新响应式样式，确保桌面局部 Gallery 与移动端单列布局不溢出、不重叠

## 4. ProcessPanel 输出定位与流式切换

- [x] 4.1 按 `tool_call_id` 构建 ToolCall 到可见 Artifact 的映射，并在有输出的过程步骤显示输出数量
- [x] 4.2 为 Artifact 展示节点生成基于 Artifact ID 的稳定 DOM 锚点，实现 ProcessPanel“查看输出”滚动定位
- [x] 4.3 确保失败或无 Artifact 的 ToolCall 不显示空入口，且不在 UI 中使用 storage key、文件路径或 sandbox 内部路径
- [x] 4.4 保持 answer.delta 阶段只渲染流式 summary，并在终态 RunView 到达后一次性切换到结构化 finding/Artifact 布局

## 5. 全链路验证与文档

- [x] 5.1 增加前端测试，覆盖纯文本、单图表、多 finding 多图表、一个 finding 多类型输出、重复引用和未关联输出
- [x] 5.2 增加前端测试，覆盖旧 Run 缺字段、ProcessPanel 定位、流式到终态切换、图片 alt、iframe sandbox 和文件链接
- [x] 5.3 运行后端完整测试与 Ruff，修复所有本变更引入的回归
- [x] 5.4 运行前端 TypeScript 检查、Vitest 与生产构建，验证桌面和移动端布局
- [x] 5.5 更新 Agent 执行顺序文档和结果格式说明，记录关联协议、降级行为与当前限制
