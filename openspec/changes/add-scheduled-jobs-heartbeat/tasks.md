## 1. 持久化基础

- [x] 1.1 增加调度器配置、cron 计算依赖和安全默认值
- [x] 1.2 新增 scheduled_jobs 与 scheduled_job_runs ORM 模型、约束和索引
- [x] 1.3 新增 Alembic migration、保留普通任务结果对话绑定，并验证 SQLite/PostgreSQL upgrade 与 downgrade 定义
- [x] 1.4 增加 schedule/heartbeat Pydantic schema、枚举和边界校验

## 2. 计划计算与 Repository

- [x] 2.1 实现 once、interval、cron 的 aware UTC 下次触发计算及 IANA 时区/DST 测试
- [x] 2.2 实现 schedule CRUD、版本化更新、暂停/恢复和 system-managed 保护
- [x] 2.3 实现到期任务 CAS 领取、租约、唯一 fire record 和下一触发推进
- [ ] 2.4 实现 misfire skip/fire_once、overlap skip 和重启 claimed 记录恢复
- [ ] 2.5 实现幂等手动触发、运行历史查询和历史保留清理

## 3. Run 派发与生命周期

- [ ] 3.1 从 HTTP 创建流程提取可复用 Run 创建应用服务
- [ ] 3.2 将 schedule trigger 元数据和内部 principal 持久化到 Run 审计链路
- [x] 3.3 触发时重新校验权限包，并将无效权限收敛为 blocked schedule run
- [x] 3.4 实现有并发上限的 SchedulerService scanner/dispatcher/reconciler 与优雅关闭
- [ ] 3.5 在 FastAPI lifespan 注册服务，并暴露扫描健康/就绪状态

## 4. 定时任务 API

- [x] 4.1 实现 schedule 创建、列表、详情、版本化更新和受保护删除 API
- [x] 4.2 实现 pause、resume、manual-run API 及幂等键
- [x] 4.3 实现 schedule run 历史与关联 Astra Run 查询 API
- [ ] 4.4 补充本机 API 边界、结构化错误和审计日志

## 5. Heartbeat

- [x] 5.1 实现每主会话唯一的 heartbeat desired-state upsert 与稳定 system-managed schedule
- [ ] 5.2 实现活动时间窗、最小周期和会话 busy defer 判定
- [ ] 5.3 实现 heartbeat prompt Run、`HEARTBEAT_OK` 静默收敛和关注事项投递
- [ ] 5.4 实现 heartbeat 配置/状态 API 与权限失效处理

## 6. Chat UI 自动化管理

- [x] 6.0 将 schedule 与 heartbeat 控制面改为全局、保留结果对话投递，并兼容收敛旧 heartbeat 稳定键

- [x] 6.1 增加前端 schedule/heartbeat 类型与 API client
- [x] 6.2 增加 heartbeat/定时任务独立分区与计数、状态摘要、下一触发和运行历史界面
- [x] 6.3 增加统一“新建”入口与类型选择、已有/新建结果对话绑定、可视化重复计划轮盘、工作区无人值守执行配置、时区/策略校验、暂停恢复与手动运行交互
- [x] 6.4 增加 heartbeat 周期、活动时间窗、prompt 和静默语义设置
- [x] 6.5 从 schedule run 历史导航到绑定的结果对话、生成文件或 heartbeat 目标对话及审计 timeline
- [x] 6.6 增加按 schedule run 聚合的制品接口与界面，覆盖结果文本和安全可交付文件

## 7. 命令系统集成

- [x] 7.1 扩展系统命令目录 schema，声明 argument mode、usage 和 effect
- [x] 7.2 实现 `/schedule` 封闭 subcommand/flag 解析器与应用服务路由
- [x] 7.3 实现 `/heartbeat` status/on/off/run 命令与 desired-state 路由
- [x] 7.4 让 Composer 支持参数命令插入、完整命令提交、错误保留和无参数命令兼容
- [x] 7.5 增加命令目录、解析、权限拒绝、生命周期操作和前端键盘交互测试

## 8. 验证与运维

- [ ] 8.1 增加 Repository 并发领取、重启恢复、misfire、overlap 和幂等测试
- [ ] 8.2 增加 API、Run 派发、权限过期和 heartbeat 静默/繁忙测试
- [x] 8.3 增加前端自动化与 heartbeat 交互测试、类型检查和生产构建
- [ ] 8.4 更新部署配置、健康检查、运维文档和自动化安全说明
- [ ] 8.5 运行后端完整测试、ruff、前端测试与 build，并记录验证结果
