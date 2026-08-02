## 1. Provider 路由与输出契约

- [x] 1.1 将宿主 Settings 和隔离 Web runtime 的默认搜索 provider 调整为 `auto`
- [x] 1.2 实现 `auto` 的 Google、Brave、无密钥链路选择，同时保持显式 provider 严格语义
- [x] 1.3 为搜索成功输出补充 `provider_mode`、`provider_attempts` 和 `degraded` 审计字段

## 2. 无密钥回退

- [x] 2.1 实现 Bing 空结果或可恢复搜索失败后回退 DuckDuckGo
- [x] 2.2 实现无密钥 degraded warning、回退 warning 和全部 provider 失败的脱敏聚合错误

## 3. 配置与文档

- [x] 3.1 将 `.env.example` 和本地 `.env` 切换为 `WEB_SEARCH_PROVIDER=auto`
- [x] 3.2 更新 README，说明自动选择顺序、显式 provider 语义和无密钥模式的适用边界

## 4. 验证

- [x] 4.1 补充自动 provider 选择、回退、空结果、聚合失败和显式 provider 不回退的单元测试
- [x] 4.2 补充 sandbox 配置默认值与敏感信息不进入输出或错误的测试
- [x] 4.3 运行相关测试套件，并在无搜索凭据条件下完成一次真实网络 smoke test
