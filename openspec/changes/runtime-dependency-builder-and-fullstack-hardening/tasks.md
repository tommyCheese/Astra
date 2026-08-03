## 1. 基线检视与清理

- [x] 1.1 审查 Docker Provider、Runtime renderer、配置、API 和设置页结构，修复已知错误与不一致
- [x] 1.2 增加模型 mock、本地启动、错误映射和 Sandbox 清理回归测试

## 2. Runtime Profile 数据与 API

- [x] 2.1 新增 RuntimeProfile/RuntimeBuild 模型、迁移、Repository 和状态机
- [x] 2.2 实现严格 Python dependency 解析、规范化、核心包保护与单元测试
- [x] 2.3 实现 profile 查询、更新、build 创建/查询 API 和稳定错误契约

## 3. Docker Runtime Builder

- [x] 3.1 实现内容寻址 Docker build context、异步 worker、超时、并发锁和脱敏日志
- [x] 3.2 实现派生 image import 与 Matplotlib/Seaborn/ECharts smoke test，成功后原子激活
- [x] 3.3 将 chart.render 改为读取 active profile 并记录 image/dependency digest provenance
- [x] 3.4 实现 staging/custom 镜像清单、数量与时间老化、终态清理、安全保护和回归测试

## 4. Runtime 设置界面

- [x] 4.1 增加设置页与 Runtime tab 导航、数据 hooks 和 API client
- [x] 4.2 实现 dependency 行编辑、校验、构建按钮、进度、错误、active image 与刷新持久化
- [x] 4.3 增加桌面/移动布局、无障碍和组件测试

## 5. 全栈容器与联调

- [x] 5.1 创建 backend/frontend Dockerfile、nginx proxy、Compose、healthcheck 与持久化配置
- [x] 5.2 打包启动全栈并验证 migration、健康检查、Run/Artifact/Runtime API 与三种 renderer
- [x] 5.3 使用真实浏览器逐项验证主要界面、Runtime build 工作流、响应式布局和控制台错误并修复

## 6. 收尾验证

- [x] 6.1 运行后端、前端、Docker integration、lint、build、OpenSpec 和安全验收
- [x] 6.2 更新中文 README 与运维文档，记录构建权限、离线 Job、回滚和清理流程
