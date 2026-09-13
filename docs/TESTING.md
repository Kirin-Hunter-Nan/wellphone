# WellPhone 测试策略

## 目标

测试套件按“确定性逻辑优先、真实副作用显式开启”的原则组织。默认回归不得调用真实 Qwen、写入提醒事项或日历；这些行为只在人工确认的真机 E2E 中开启。

## 测试分层

| 层级 | 范围 | 主要验证内容 | 默认执行 |
|---|---|---|---|
| 服务端单元测试 | `server/tests/agent`、`tools`、`providers` | Agent 轮次、Tool 预算、调用计数、白名单、参数校验、Qwen 流解码 | 是 |
| 服务端状态测试 | `server/tests/tasks`、`conversations`、`tool_results` | 自动入队、兼容确认、租约、重试、断点恢复、幂等、乱序检查点、旧 Worker 隔离 | 是 |
| API 集成测试 | `server/tests/api` | HTTP 校验、SSE、历史上下文、请求回放、Tool continuation、任务生命周期 | 是 |
| PostgreSQL 契约测试 | `server/tests/postgres` | 生产存储重开恢复、数据库约束、任务与 Journal、会话与 Tool Result | 配置隔离数据库后执行 |
| iOS 单元测试 | `WellPhoneTests` | 自动启动、SwiftData 恢复、提醒执行/验证、日历显式授权、重试上限、取消竞态、Checkpoint 与 Tool Result 重传、多模态编码 | 是 |
| iOS UI 冒烟测试 | `WellPhoneUITests` | 启动、侧边栏、键盘焦点和基础导航 | 建议提交前执行 |
| 真机旅行 E2E | `testLiveTravelTaskEndToEndOnDevice` | 真实 Qwen、无二次确认、后台切换、12 轮上限展示、精简完成卡、自然语言回复、一次性日历弹窗 | 手动显式开启 |

## 关键不变量

- Agent 的模型轮次不得超过 Profile 的 `max_iterations`，进度文本也不得出现额外轮次。
- 一轮返回的 Tool 数量超过剩余预算时，不执行部分调用，避免产生不可重放的半批次副作用。
- Journal 已保存的 Tool Result 和最终产物在重启后不得重复执行。
- 相同 Tool、相同语义参数和相同错误达到阈值后提前停止；不同地点的同类错误不得互相累计，中间出现成功 Observation 时连续失败计数必须清零。
- 任务租约过期后允许新 Worker 接管，但旧 Worker 不得提交完成结果。
- 可重试错误只能执行到配置上限；永久错误、未知能力和已取消任务不得重试。
- Tool Call、聊天请求、Checkpoint 和 Tool Result 的幂等键不得被不同内容复用。
- Checkpoint 可以乱序到达，但旧 revision 不得回滚当前快照。
- 模型返回未知 Tool、非法参数或畸形流时，客户端收到标准协议错误，不得执行副作用。
- 明确的执行指令必须自动开始；缺少必要参数时必须澄清，不能以普通任务卡替代澄清。
- 旅行请求只有在 `addToCalendar=true` 时才自动写入日历，否则完成后只提供一次选择提示。
- 商务出差任务必须保留每一项固定订单或会议的原始标题与时间；固定安排缺失、重复或被改时必须由提交 Tool 拒绝。
- 商务出差冲突由确定性代码计算；酒店入住区间不应与期间会议产生假冲突，新增交通或推荐活动不得覆盖固定安排。
- 固定订单或会议地点无法被 MapKit 唯一确认时，最终结果必须保留邮件中的原始 location、移除未验证的 placeName 和地图链接，且不得因此耗尽 Agent 轮次；推荐地点仍必须通过唯一性验证。
- Gmail 搜索只能在任务输入包含用户明确授权的 `searchGmail=true` 时执行；服务端必须根据目的地和日期生成不可扩大的 `gmailQuery`。邮件抽取的固定安排必须引用真实 message ID，未搜索、伪造来源或未锁定时不得提交。
- 用户测试输入必须使用自然语言；`after:`、`before:` 等 Gmail 操作符只能由服务端生成，不得成为用户或聊天模型必填语法。生成查询不得包含 `from:me`、`to:me`，邮件接收时间必须覆盖合理的预订提前期。
- 已取消或失去租约的运行中任务必须在短时间内中止 Handler，不得继续占用单 worker 阻塞后续队列。
- Google OAuth token 只能由官方 iOS SDK 保存在设备钥匙串；Device Tool 结果和服务端 Journal 不得包含 access/refresh token。
- Drive 上传只能在 `uploadToDrive=true` 时发生，文件以任务 ID 幂等定位，并在完成前下载回读验证；未经验证的链接不得进入产物。
- Drive 最终交付文件必须是手机端可直接预览的 PDF，不得上传 `.md`；聊天完成回复必须包含已验证的 Drive 链接。
- `addCalendarAlerts=true` 必须同时具备明确的 `addToCalendar=true`；EventKit 写入后需同时回读核对事件字段和相对提醒时间。
- 旅行地点必须由设备端原生 MapKit 返回 `verified=true` 后才能进入最终行程；只有搜索链接或歧义候选不得冒充回查成功。
- MapKit 可接受具备 Place ID、完整地址和高度一致名称的具体分店/场馆；泛化品牌名与多分店候选仍必须要求 Agent 细化查询。
- Device Tool 请求与结果以 `(task_id, tool_call_id)` 幂等持久化；重复上报不得重复推进 Agent Loop，不同内容复用同一标识必须被拒绝。
- 显式旅行日历请求只有在 EventKit 幂等写入并逐项回读验证后才完成；写入或验证失败必须令整体任务失败，且不得发送完成通知。
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
