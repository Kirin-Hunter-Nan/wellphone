# WellPhone 源码架构

## 目标

目录按稳定职责和业务能力组织。视图、Agent 调度、系统副作用、网络协议和持久化拥有明确边界；增加新的工具任务时，应优先添加原子 Tool 和 Profile，而不是向入口文件继续堆叠业务代码。

## iOS

- `App/`：应用组合入口和根视图，只负责装配依赖。
- `Core/Domain/`：会话、消息、任务、步骤与产物等本地领域模型。
- `Core/Networking/`：WellPhone 网络协议、SSE 解码和服务端客户端。
- `Core/Configuration/`：运行配置读取。
- `Agent/Runtime/`：设备端 Tool 注册、确认、执行、验证和恢复外壳。
- `Agent/Tools/`：可以在设备上确定性执行的原子工具。
- `Features/Chat/`：聊天状态与视图；页面容器只协调导航和侧边栏，输入器、消息气泡、任务卡片与完成提示位于独立组件；图片处理、附件文件存储、SwiftData 附件关联、流式生成和会话持久化拥有独立边界。
- `Features/Tasks/`：任务中心状态与视图；列表容器、任务卡片、详情页、进度、产物和展示规则分别维护。
- `Platform/`：EventKit、通知等 Apple 平台适配器。
- `Resources/`：资产目录和非敏感运行配置。

依赖方向为 `App → Features → Core/Agent`，平台副作用通过协议注入。视图不得直接访问模型供应商或数据库，Agent Runtime 不依赖具体 SwiftUI 视图。

## 服务端

- `bootstrap/`：API 与 Worker 的可执行组合入口。
- `api/`：FastAPI 应用和面向客户端的 wire protocol；`application.py` 只装配依赖和路由，消息流、Tool Result、任务与检查点由独立路由处理。
- `agent/`：LangGraph 循环及其稳定边界；`engine.py` 只负责编排，模型、Tool、Profile、Journal 和任务适配器分别位于对应模块，`loop.py` 仅保留兼容导出。
- `tasks/`：长任务模型、存储协议、内存/PostgreSQL 实现、步骤投影和 Worker Runner 分模块维护；设备任务检查点在 `checkpoints/` 内按模型、协议、幂等序列化和具体存储拆分；`jobs.py` 与 `checkpoint_store.py` 仅保留兼容导出。
- `conversations/`：会话请求的幂等模型与存储协议、内存/PostgreSQL 实现，以及独立的历史重建和上下文裁剪策略；`store.py` 仅保留兼容导出。
- `tool_results/`：设备端 Tool Result 与模型续接状态的领域模型、存储协议、稳定序列化，以及内存/PostgreSQL 实现；`store.py` 仅保留兼容导出。
- `tools/`：模型可调用的 capability catalog 与原子工具实现；旅行能力按输入模型、Apple 地图适配、原子 Tool、校验、产物渲染和 Profile 组织，共用全局 Agent Loop。
- `providers/`：Qwen 等模型供应商适配器；传输、消息/请求体构造和流式响应解码保持独立。
- `core/`：跨入口共享的配置。

API 和 Worker 只在组合入口创建具体 PostgreSQL、Qwen 与 Apple Maps 实现。业务模块通过 Protocol 依赖抽象；`tools/travel/` 可以组合地点检索和行程提交，但不能创建第二套 Agent Loop。

## 测试

服务端测试目录镜像 `app/` 的模块结构。iOS 单元测试、UI 流程测试和启动测试分目录保存。涉及真实 Qwen 调用或 EventKit 写入的 UI 测试必须显式开启安全开关，默认回归不得产生外部副作用。

## 拆分规则

单文件接近 500 行时应检查是否同时承担模型、存储、编排或展示职责。优先沿职责拆分，不以创建大量只有转发作用的文件为目标。当前规划的架构拆分已经完成；后续只有在新功能让现有边界重新承担多种职责时再拆分，不再以文件行数本身作为重构目标。
