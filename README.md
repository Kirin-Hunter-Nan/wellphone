# WellPhone Silent Agent

一个面向 iPhone 的无界面多模态 Agent。用户通过文字、语音、图片或文件下达任务后，可以继续使用当前 App；Agent 在 iOS 允许的后台执行窗口内完成推理、文件处理、系统能力调用和服务 API 操作，全程不抢占屏幕、键盘或输入焦点。

> 当前阶段：V0 聊天开发中。iPhone 12（iOS 26.6.2）的签名、安装、启动和 Xcode 调试已验证；聊天 UI、本地历史、停止/重试以及 Qwen 流式代理链路已接通并完成真实 API 联调。

## 核心原则

- **屏幕属于用户**：任务启动后不主动创建前台 Scene、拉起其他 App 或显示键盘。
- **模型只做决策**：模型生成结构化计划，确定性工具负责真实执行。
- **结果必须验证**：工具执行成功后读取系统或服务端状态进行二次校验。
- **任务可以恢复**：每一步持久化，App 被暂停或终止后能够幂等恢复。
- **能力严格受限**：仅执行白名单工具；付款、删除及未授权外发默认禁止。

## 架构

```mermaid
flowchart LR
    A["文字 / 语音 / 图片 / 文件"] --> B["Chat & App Intent"]
    B --> C["Agent Runtime"]
    C --> D["Model Gateway"]
    D --> E["Plan Validator"]
    E --> F["Tool Registry"]
    C <--> G["Task Store / Checkpoint"]
    C <--> H["Background Coordinator"]
    F --> I["Vision / Photos / Files"]
    F --> J["Calendar / Reminders"]
    F --> K["Network & Service APIs"]
    I --> L["Verified Result"]
    J --> L
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
- 服务端：轻量 API 服务、模型网关、结构化输出校验、幂等记录
- 模型：支持多模态输入、JSON Schema/结构化输出与工具调用的模型

## 启动 Qwen 模型代理

需要 Node.js 20 或更高版本。先复制服务端环境变量示例：

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

`QWEN_BASE_URL` 必须与 API Key 所在地域一致，并以 `/compatible-mode/v1` 结尾。启动服务：

```bash
npm start
```

iOS 客户端从 `ios/WellPhone/WellPhone/Config/AppConfig.json` 读取代理地址。真机联调时，将服务端 `HOST` 改为 `0.0.0.0`，并把 `modelProxyBaseURL` 改成运行代理的 Mac 局域网地址，例如 `http://192.168.1.20:8787`。该模式仅用于受信任的开发网络；正式部署应使用 HTTPS 和服务端认证。

API Key 只存在于 `server/.env`，不会进入客户端或 Git。

## 项目边界

本项目不是 iOS 跨 App UI 自动化工具。未越狱 iPhone 不支持在后台创建第二套交互式 UI 会话，也不能读取或操纵任意第三方 App。WellPhone 通过系统 Framework、App Intent 和业务 API 完成任务。

详细设计、数据模型、接口约定、测试和实施计划参见 [开发文档](docs/DEVELOPMENT.md)。
