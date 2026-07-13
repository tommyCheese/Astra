# 一次 Run 如何返回并展示工具输出

本文沿一次回答真正发生的先后顺序，说明最终结果格式，以及同一轮多次工具调用时图表、HTML 和文件会出现在什么位置。

## 1. 工具先产生 Artifact，答案暂时还没有布局

工具执行成功后，输出先成为当前 Run 的 `ArtifactRecord`。记录包含稳定 `id`、所属 `tool_call_id`、MIME、校验和、安全状态和受控存储键。此时前端不根据文件路径安排位置，模型也不能指定组件或 CSS；Artifact ID 是后面建立关联的唯一标识。

Chart 等工具把可引用的 Artifact ID 放进 Tool output 和 Observation。后续模型看到的是本轮已经出现的 ID，所以只能在某条结论确实由该输出支撑时引用它。

## 2. 模型生成 FinalAnswer，并在 finding 中声明关联

Agent Loop 离开逐轮执行后生成 `FinalAnswer`。结果主体如下：

```json
{
  "summary": "面向用户的完整回答",
  "findings": [
    {
      "text": "第一个结论",
      "source_urls": ["https://example.com/source"],
      "artifact_ids": ["artifact-chart-a"]
    },
    {
      "text": "第二个结论",
      "source_urls": [],
      "artifact_ids": ["artifact-chart-b", "artifact-report"]
    }
  ],
  "sources": [],
  "failed_sources": [],
  "source_quality": [],
  "conflicts": [],
  "caveats": [],
  "verification_notes": [],
  "memory_references": [],
  "audit_refs": {}
}
```

`summary` 是可以独立阅读的完整回答。`findings` 按展示顺序提供补充结论；每条 finding 的 `artifact_ids` 也有顺序。普通文本问答使用空数组。旧 Run 没有该字段时，后端默认值和前端 normalize 都把它当作空数组。

## 3. 后端先校验引用，再允许结果落库

模型返回的 ID 只是候选关系。Harness 随即查询当前 Run 的 Artifact，并只接受同时满足以下条件的记录：属于当前 Run、`security_status` 为 `verified`、存在 `storage_key`。

清洗从第一条 finding 开始依次进行。同一条 finding 重复出现的 ID 只保留第一次；不存在、跨 Run、pending、expired 或没有可访问内容的 ID 被移除。系统只把拒绝数量写入 `verification_report.invalid_artifact_references`，并加入一条不含敏感信息的 verification warning。

经过这一步的同一个 `FinalAnswer` 才继续流向 Verification、Run result 和 `final_answer` Artifact，因此 API 返回值与审计产物不会出现不同的关联关系。最终 Run 的 `result` 还会加入：

```json
{
  "verification_report": {
    "status": "completed",
    "invalid_artifact_references": 0,
    "notes": []
  },
  "audit_refs": {
    "evidence_pack_artifact_id": "artifact-evidence-pack",
    "agent_turn_count": 4,
    "referenced_artifact_ids": ["artifact-chart-a", "artifact-chart-b", "artifact-report"]
  },
  "completion_decision": {
    "state": "completed"
  }
}
```

## 4. 流式阶段只显示 summary

模型生成 summary 时，后端通过 `answer.delta` 连续发送文字。findings 和 Artifact 关系尚未完成校验，所以前端此时只更新一个临时 summary 气泡，不提前插入或移动图表。`answer.settling` 表示文字已经结束、结构化和验证仍在进行。

`answer.completed` 后，前端重新读取完整 RunView。只有拿到终态快照，临时气泡才一次性替换成 ProcessPanel 和结构化 FinalAnswer。

## 5. 前端按 finding 顺序第一次消费输出

前端先过滤出已验证且有 `content_url` 的 Artifact，并按 ID 建立查找表。随后从第一条 finding 开始读取 `artifact_ids`：

1. 第一次遇到某个有效 ID，就在当前 finding 正文后立即渲染对应 ArtifactCard。
2. 同一 finding 引用多个 ID 时，局部 Gallery 按 ID 列表顺序展示。
3. 后续 finding 再次引用已经展示的 ID 时，不复制大型图片或 iframe，只显示“查看上方已展示的输出”定位链接。
4. 所有 findings 完成后，仍未被消费的可见 Artifact 按 `created_at`、`id` 排序进入“其他输出”。两个以内直接展开，三个以上使用可折叠区域。

因此，多次工具调用产生的图表不会全部无条件堆在答案最下方：有结构化引用的输出跟随支撑它的结论；没有引用、旧 Run 或模型漏关联的输出才进入底部降级区，而且不会丢失。

## 6. ProcessPanel 再从调用顺序提供反向定位

答案区按照结论顺序展示，ProcessPanel 仍按照 AgentTurn 的真实执行顺序展示。每个工具步骤用 `tool_call_id` 查找自己的可见 Artifact；找到后显示输出数量和“查看输出”链接，链接目标是 `artifact-output-{artifact_id}` 锚点。输出可能位于某条 finding 下，也可能位于“其他输出”，入口都指向实际首次渲染位置。

失败调用、没有 Artifact 的调用、未验证或没有内容 URL 的 Artifact 不显示入口。界面只使用 Artifact ID 和受控内容 URL，不读取或展示 storage key、宿主文件路径与 Sandbox 内部路径。

## 7. 不同输出继续使用固定安全渲染器

渲染位置虽然改变，安全边界不变。PNG/SVG 使用带替代文本的图片；HTML 使用 `sandbox="allow-scripts"` 且禁止 referrer 的隔离 iframe；其他类型显示受控下载/打开链接。ArtifactCard 在移动端切换为单列，标题与元数据纵向排列，避免多输出溢出或重叠。

当前协议只表达“哪条 finding 引用哪个 Artifact”，不允许模型控制尺寸、坐标、组件类型或任意 UI schema。Artifact 的可信类型始终来自服务端记录。
