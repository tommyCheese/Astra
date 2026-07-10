## 1. 配置与 Schema

- [x] 1.1 增加 Google 搜索配置项：API Key、Search Engine ID、结果数量、语言、地区和安全搜索
- [x] 1.2 更新 `.env.example` 和 README，说明 Google 搜索真实运行配置
- [x] 1.3 定义 Google 搜索结果、CrawlerPlan、ExtractedSource、EvidencePack 和总结输出 schema
- [x] 1.4 扩展 ToolSpec/Tool manifest，包含 description、timeout、retry_policy 和 error_categories
- [x] 1.5 增加配置缺失、secret 不外泄和工具 manifest 的测试

## 2. Google Web Search

- [x] 2.1 重构 `web_search` provider 接口，保留 mock provider
- [x] 2.2 将 `web_search` 作为工具注册到统一 tool registry，并通过 registry 执行
- [x] 2.3 实现 Google Programmable Search JSON API provider
- [x] 2.4 将 Google API 响应标准化为候选来源结构
- [x] 2.5 在 ToolCall input/output 中记录非敏感搜索参数、provider metadata、候选数量和 warning
- [x] 2.6 增加 mock Google API 响应测试、空结果测试、配置错误测试、API 错误测试和 registry 调用测试

## 3. Adaptive Web Crawler

- [x] 3.1 实现 HTML 抓取基础设施：超时、重定向、content type、状态码和错误分类
- [x] 3.2 将 `web_fetch` 作为工具注册到统一 tool registry，并通过 registry 执行
- [x] 3.3 实现 metadata 提取：title、meta description、OpenGraph、Twitter Card、schema.org 和发布时间
- [x] 3.4 实现正文抽取策略：readability、metadata_first、selector_extract、plain_text 和 fallback_snippet
- [x] 3.5 实现 CrawlerPlan 生成与验证，禁止执行模型生成代码
- [x] 3.6 实现抓取质量评分、内容长度统计、warnings 和 source_type
- [x] 3.7 增加 fixture HTML 测试，覆盖文章页、简单页面、低质量页面、抓取失败、selector 回退和 registry 调用

## 4. Summary Run Flow

- [x] 4.1 更新 run engine，让总结任务执行搜索、筛选、去重、抓取、证据包构造、总结和验证
- [x] 4.2 实现搜索候选来源筛选和 canonical URL 去重
- [x] 4.3 实现 Evidence Pack 构造，并存储为 Artifact
- [x] 4.4 确保 Evidence Pack 只使用已审计 ToolCall、Artifact 和验证结果
- [x] 4.5 更新模型综合提示和结构化输出，要求每个关键要点引用来源
- [x] 4.6 实现冲突、失败来源、低质量来源和证据不足的验证备注
- [x] 4.7 增加成功总结、证据不足、部分抓取失败、来源冲突和未审计状态不入包测试

## 5. Web App

- [x] 5.1 更新 Timeline 展示搜索、筛选、抓取、证据包、总结和验证阶段
- [x] 5.2 在结果视图展示来源列表、抓取策略、质量评分、失败来源和 warning
- [x] 5.3 保持液态玻璃界面风格，并确保新增内容在桌面和移动端不重叠
- [x] 5.4 增加前端测试，覆盖真实总结结果、失败来源和质量评分展示

## 6. Verification

- [x] 6.1 使用 mock provider 跑完整确定性端到端总结任务
- [x] 6.2 增加可选真实 Google 集成测试，并通过环境变量显式启用
- [x] 6.3 运行后端测试、前端测试、lint 和 build
- [x] 6.4 更新 OpenSpec tasks 状态，并记录真实网络运行限制

真实网络运行限制：Google 集成测试默认跳过；需要设置 `ASTRA_RUN_GOOGLE_INTEGRATION=1`、`GOOGLE_SEARCH_API_KEY` 和 `GOOGLE_SEARCH_ENGINE_ID` 后才会访问 Google Programmable Search JSON API。
