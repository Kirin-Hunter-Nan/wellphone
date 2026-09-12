# WellPhone 测试策略

## 目标

测试套件按“确定性逻辑优先、真实副作用显式开启”的原则组织。默认回归不得调用真实 Qwen、写入提醒事项或日历；这些行为只在人工确认的真机 E2E 中开启。

## 测试分层

| 层级 | 范围 | 主要验证内容 | 默认执行 |
|---|---|---|---|
| 服务端单元测试 | `server/tests/agent`、`tools`、`providers` | Agent 轮次、Tool 预算、调用计数、白名单、参数校验、Qwen 流解码 | 是 |
| 服务端状态测试 | `server/tests/tasks`、`conversations`、`tool_results` | 任务确认、租约、重试、断点恢复、幂等、乱序检查点、旧 Worker 隔离 | 是 |
| API 集成测试 | `server/tests/api` | HTTP 校验、SSE、历史上下文、请求回放、Tool continuation、任务生命周期 | 是 |
| PostgreSQL 契约测试 | `server/tests/postgres` | 生产存储重开恢复、数据库约束、任务与 Journal、会话与 Tool Result | 配置隔离数据库后执行 |
| iOS 单元测试 | `WellPhoneTests` | SwiftData 恢复、提醒执行/验证、重试上限、取消竞态、Checkpoint 与 Tool Result 重传、多模态编码 | 是 |
| iOS UI 冒烟测试 | `WellPhoneUITests` | 启动、侧边栏、键盘焦点和基础导航 | 建议提交前执行 |
| 真机旅行 E2E | `testLiveTravelTaskEndToEndOnDevice` | 真实 Qwen、后台切换、12 轮上限展示、完成回复、一次性日历弹窗、任务详情导入 | 手动显式开启 |

## 关键不变量

- Agent 的模型轮次不得超过 Profile 的 `max_iterations`，进度文本也不得出现额外轮次。
- 一轮返回的 Tool 数量超过剩余预算时，不执行部分调用，避免产生不可重放的半批次副作用。
- Journal 已保存的 Tool Result 和最终产物在重启后不得重复执行。
- 相同 Tool 错误达到阈值后提前停止；中间出现成功 Observation 时连续失败计数必须清零。
- 任务租约过期后允许新 Worker 接管，但旧 Worker 不得提交完成结果。
- 可重试错误只能执行到配置上限；永久错误、未知能力和已取消任务不得重试。
- Tool Call、聊天请求、Checkpoint 和 Tool Result 的幂等键不得被不同内容复用。
- Checkpoint 可以乱序到达，但旧 revision 不得回滚当前快照。
- 模型返回未知 Tool、非法参数或畸形流时，客户端收到标准协议错误，不得执行副作用。
- iOS 系统写入必须“执行后回读验证”；恢复时优先查找已有写入，再决定是否使用相同幂等键重试。

## 执行命令

### 服务端完整回归

```bash
cd server
.venv/bin/pytest -q
```

### PostgreSQL 生产存储契约

必须使用可清空的独立测试数据库，不能指向开发或生产数据。未设置变量时，这组测试会自动跳过。

```bash
cd server
WELLPHONE_TEST_DATABASE_URL='postgresql://user:password@127.0.0.1:55432/wellphone_test' \
  .venv/bin/pytest -q tests/postgres
```

### iOS 无签名编译

```bash
cd ios/WellPhone
xcodebuild \
  -project WellPhone.xcodeproj \
  -scheme WellPhone \
  -destination 'generic/platform=iOS' \
  -configuration Debug \
  build CODE_SIGNING_ALLOWED=NO
```

### iPhone 真机单元测试

```bash
cd ios/WellPhone
xcodebuild \
  -project WellPhone.xcodeproj \
  -scheme WellPhone \
  -destination 'id=<DEVICE_UDID>' \
  test -only-testing:WellPhoneTests
```

### iPhone UI 冒烟测试

```bash
cd ios/WellPhone
xcodebuild \
  -project WellPhone.xcodeproj \
  -scheme WellPhone \
  -destination 'id=<DEVICE_UDID>' \
  test \
  -only-testing:WellPhoneUITests/WellPhoneUITests/testSwipeRightRevealsSidebar \
  -only-testing:WellPhoneUITests/WellPhoneUITests/testOpeningSidebarDismissesComposerKeyboard
```

### 真实旅行 E2E

运行前确认测试服务端、Qwen Key、模型地址、iPhone 网络和日历权限均可用。该测试会产生真实模型请求，并可能向 Apple 日历写入事件。

```bash
cd ios/WellPhone
WELLPHONE_LIVE_TRAVEL_E2E=1 xcodebuild \
  -project WellPhone.xcodeproj \
  -scheme WellPhone \
  -destination 'id=<DEVICE_UDID>' \
  test -only-testing:WellPhoneUITests/WellPhoneUITests/testLiveTravelTaskEndToEndOnDevice
```

## 提交前验收

1. 服务端完整回归全部通过。
2. iOS 无签名编译通过。
3. iPhone 真机单元测试全部通过。
4. 修改导航或任务页面时补跑 UI 冒烟测试。
5. 修改 Qwen、旅行 Agent、后台任务或 EventKit 时，在安全环境补跑真实旅行 E2E。
6. `git diff --check` 无格式错误，测试过程中没有把密钥、截图或结果包加入 Git。

UI Runner 如果在任何断言开始前因 CoreDevice、DTX 或测试 Runner 安装失败而退出，应记录为测试基础设施失败，不能记作产品测试通过；修复环境后必须重新执行。
