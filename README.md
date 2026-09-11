# WellPhone Silent Agent

一个面向 iPhone 的无界面多模态 Agent。用户通过文字、语音、图片或文件下达任务后，可以继续使用当前 App；Agent 在 iOS 允许的后台执行窗口内完成推理、文件处理、系统能力调用和服务 API 操作，全程不抢占屏幕、键盘或输入焦点。

> 当前阶段：V0.1 最小 Agent 已接通，并已进入 V0.2 可恢复运行时建设。iPhone 12（iOS 26.6.2）的签名、安装、启动和 Xcode 调试已验证；Python AI 后端把 Qwen 工具调用转换为平台无关的 `reminder.create` capability，App 会校验参数、请求用户确认、写入系统提醒事项并回读验证。聊天请求、权威对话历史、真实 Tool Result、模型续接上下文与任务检查点均由 PostgreSQL 协调；设备端持久化系统执行凭证，并为 `reminder.create` 写入稳定幂等标记。Runtime 只对已声明幂等且被 Tool 判定为瞬时错误的执行进行最多三次重试；执行中的安全停止会阻止后续重试，若系统写入已经完成则仍会回读并展示真实结果。App 重启后可以继续恢复，不会重复写入。

## 核心原则

- **屏幕属于用户**：任务启动后不主动创建前台 Scene、拉起其他 App 或显示键盘。
- **模型只做决策**：模型生成结构化计划，确定性工具负责真实执行。
- **结果必须验证**：工具执行成功后读取系统或服务端状态进行二次校验。
- **任务可以恢复**：每一步持久化，App 被暂停或终止后能够幂等恢复。
- **能力严格受限**：仅执行白名单工具；付款、删除及未授权外发默认禁止。

## 架构

```mermaid
flowchart LR
    A["文字 / 语音 / 图片 / 文件"] --> B["Swift iOS Client"]
    B --> C["Python AI Backend"]
    C --> D["Provider Adapter"]
    D --> E["Qwen / Future Model"]
    C -->|"WellPhone SSE"| B
    B --> F["Runtime Harness"]
    F --> G["Capability Registry"]
    F <--> H["SwiftData Task Store"]
    F <--> C
    C <--> M["PostgreSQL Checkpoint Journal"]
    F <--> I["Background Coordinator"]
    G --> J["iOS System APIs"]
    G --> K["Service APIs"]
    J --> L["Verified Result"]
    K --> L
```

## 开发路线

1. **V0 聊天**：SwiftUI 多轮聊天、流式响应、本地历史、停止与重试。
2. **V0.1 最小 Agent**：实现 `reminder.create`，完成计划、授权、执行和验证闭环。
3. **V0.2 任务系统**：任务状态机、检查点、幂等、失败重试和恢复。
4. **V0.3 多模态**：语音转写、图片/PDF、Vision OCR 和结构化提取。
5. **V0.4 后台执行**：App Intent、长任务协调、Background URLSession 和完成通知。
6. **V1 票据 Agent**：票据收集、OCR、去重、报告生成、上传/发送和结果验证。

## 技术栈

- iOS：Swift 6、SwiftUI、Swift Concurrency、SwiftData、App Intents、BackgroundTasks
- 系统能力：PhotoKit、Vision、PDFKit/Core Graphics、EventKit、Keychain、OSLog
- 网络：URLSession、Background URLSession、OAuth 2.0
- 服务端：Python 3.12+、FastAPI、Provider Adapter、结构化输出校验、请求租约、权威会话历史与响应回放
- 数据库：PostgreSQL 18、Psycopg 3 异步连接池
- 模型：支持多模态输入、JSON Schema/结构化输出与工具调用的模型

## 启动 Python AI 后端

先复制服务端环境变量示例：

```bash
cd server
cp .env.example .env
```

然后在 `server/.env` 中填写以下三项：

```dotenv
DASHSCOPE_API_KEY=
QWEN_BASE_URL=
QWEN_MODEL=
```

`QWEN_BASE_URL` 必须与 API Key 所在地域一致，并以 `/compatible-mode/v1` 结尾。

本地开发需要 Python 3.12 或更高版本以及 [uv](https://docs.astral.sh/uv/)：

```bash
docker compose up -d postgres
uv sync
uv run python -m app
```

真机联调或本机常驻部署推荐使用 Docker：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f ai-backend
```

停止服务时运行 `docker compose down`。Compose 会同时管理 FastAPI 和 PostgreSQL，并等待数据库健康后再启动 API。模型密钥在运行时从 `server/.env` 注入，不会复制进镜像；API 容器以非 root 用户和只读文件系统运行，PostgreSQL 数据保存在 `wellphone-postgres` Docker Volume 中。

iOS 客户端从 `ios/WellPhone/WellPhone/Config/AppConfig.json` 读取代理地址。真机联调时，将服务端 `HOST` 改为 `0.0.0.0`，并把 `modelProxyBaseURL` 改成运行代理的 Mac 局域网地址，例如 `http://192.168.1.20:8787`。该模式仅用于受信任的开发网络；正式部署应使用 HTTPS 和服务端认证。

API Key 只存在于 `server/.env`，不会进入客户端或 Git。

测试提醒链路时，可以发送“请提醒我明天上午九点带伞”。后端返回的操作会先显示为确认卡片；点击“确认创建”后，App 才会请求提醒事项权限并执行。修改服务端代码后，本地开发模式需要重新启动 `uv run python -m app`；Docker 模式需要重新运行 `docker compose up -d --build`。

首次产生需要确认的任务时，系统会请求通知权限。允许后，WellPhone 会在任务等待确认以及任务完成并通过验证时发送本地通知；点击通知会进入任务中心。拒绝通知权限不会阻止任务执行，任务状态仍会保存在 App 内。

## 项目边界

本项目不是 iOS 跨 App UI 自动化工具。未越狱 iPhone 不支持在后台创建第二套交互式 UI 会话，也不能读取或操纵任意第三方 App。WellPhone 通过系统 Framework、App Intent 和业务 API 完成任务。

详细设计、数据模型、接口约定、测试和实施计划参见 [开发文档](docs/DEVELOPMENT.md)，Tool 与执行外壳的边界参见 [Runtime Harness](docs/RUNTIME_HARNESS.md)。
