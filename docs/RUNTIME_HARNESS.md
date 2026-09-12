# WellPhone Runtime Harness

## 边界

聊天模型只请求业务能力；短任务由客户端 Runtime Harness 执行，长任务则由服务端统一 Agent Loop 在确认后自主调用白名单原子 Tool。两条链路都由确定性边界负责确认、参数校验、真实执行和结果验证。

`reminder_create` 只存在于 Qwen Provider 内部，并在 Python 服务端被转换为平台无关 capability `reminder.create`。Swift 客户端不识别任何模型厂商的 Tool 名称。参数解析、用户确认、EventKit 写入和回读验证不是独立的模型 Tool。

`travel_plan` 同样只是聊天阶段的意图入口，并映射为 `travel.plan` 长任务。旅行规划本身没有专属 Loop：worker 注册唯一的通用 `AgentLoopTaskHandler`，由 `travel.plan` Profile 提供系统提示词、允许使用的 Tool、循环预算、进度步骤和最终结果构建器。目前的原子 Tool 是 `places_search` 与 `itinerary_submit`；后续能力通过新增或复用 Tool、再增加轻量 Profile 接入，不需要复制一套业务循环。

服务端每轮模型决策、Tool Observation、状态更新与最终输出都写入 PostgreSQL `agent_loop_events`。worker 租约过期或进程重启后，会恢复消息与工具状态；若中断发生在模型 Tool Call 已落库而结果尚未落库之间，恢复流程会继续执行该待处理调用，而不是重新开始整项规划。

```text
Chat intent Tool -> capability -> user confirmation -> server task queue
  -> Generic Agent Loop
       -> Task Profile (prompt / allowed tools / budgets / finalizer)
       -> Model decision
       -> Atomic Tool
       -> validated Observation
       -> next decision or terminal result
  -> deterministic artifacts -> task detail + chat completion reply
```

## 客户端与服务端边界

```text
Qwen / future provider
  -> Python Provider Adapter
  -> WellPhone SSE protocol (tool.requested + capability)
  -> Swift Runtime Harness
  -> iOS system capability
```

Python 后端负责模型鉴权、模型提示词、厂商 Tool Schema、流式响应解析，以及厂商 Tool 名称到 capability 的映射。Swift 客户端负责权限、用户确认、本地任务状态、系统 API 调用与结果验证。模型供应商变化不应要求修改 Runtime Harness。

每轮聊天请求也受运行外壳保护。iOS 将稳定的 `requestId` 保存到发起该轮回复的用户消息中，重试时复用；Python 服务端以 PostgreSQL 租约领取请求并保存完整 WellPhone SSE 事件。断流清理独立于 HTTP 请求的取消域，短暂并发由客户端按 `Retry-After` 自动重试。完成后的重复请求只回放原始事件，不重复访问模型。回放中的 Tool Call 在客户端按 `(conversationID, toolCallID)` 去重，因此不会产生第二张任务卡片。

PostgreSQL 中的 chat request 同时是服务端权威会话历史。服务端只在 conversation 第一次迁移时导入客户端携带的旧消息；后续轮次使用已保存的 user 输入、已生成的 assistant 内容以及 Tool continuation 最终回复重建上下文，并在 Provider 调用前执行消息数和字符数双重裁剪。SwiftData 继续负责本机 UI 与离线展示，但不再决定模型看到的旧历史。

模型发起 Tool Call 时，Provider Adapter 会先把厂商专属的续接上下文作为 opaque JSON 保存到 PostgreSQL，再向 Swift 客户端发送平台无关的 `tool.requested`。设备端完成最终状态后，通过 `tool-results` 将 `verified / declined / failed` 回传服务端；服务端以原始 Tool Call、真实执行结果续接模型，并将最终回复返回聊天窗口。

结果先在 SwiftData 标记为待同步，再由 PostgreSQL 使用 `(conversation_id, tool_call_id)` 幂等接收。最终模型回复也绑定到同一个键：网络重试复用已保存的回复，不重复调用模型，也不在聊天窗口重复插入消息。回传或续接失败不得改变设备端已经验证的真实执行结果。

任务运行状态使用独立的 checkpoint 通道同步。SwiftData 为每个任务维护单调递增的 `revision`；等待确认、每次执行尝试、计划重试、取消请求、验证以及完成、失败或取消等关键转换都会生成检查点。检查点包含执行尝试次数、下次重试时间、执行 Deadline、最近一次执行错误和取消请求时间。Python 服务端将每个版本写入 PostgreSQL 事件日志，并维护一份只接受更高版本的最新快照。旧版本重放不会覆盖新状态，同一版本或 `requestId` 携带不同内容会被拒绝。

checkpoint 上报不属于 EventKit 写入事务，网络失败不得阻塞或回滚设备端执行。客户端只持久化最新待上报快照，App 下次启动时继续补报；服务端允许版本跳号并记录 `gap`，因此即使中间状态未能送达，也能恢复到设备已确认的最新状态。用户确认和 iOS 权限仍只在客户端完成，服务端检查点不获得代替用户执行系统写入的权限。

设备端 Tool 返回后，Runtime Harness 会先将 opaque execution receipt 写入 SwiftData，再进入验证阶段。App 若在验证或结果同步期间终止，重启后使用该凭证继续回读验证，不再次执行系统写入。执行凭证可能包含系统对象标识，因此留在设备端，不随 checkpoint 上传服务端。

若 App 在执行阶段终止且还没有持久化 receipt，Runtime 会先调用 Tool 级恢复入口。`reminder.create` 使用任务 UUID 生成稳定幂等键，并把专用 URL 标记写入 EventKit 提醒元数据；重启后先按标记查找已有提醒，找到后回读验证，找不到时才执行创建，而创建入口本身也会再次查重。因此即使 App 在 EventKit 保存成功、receipt 持久化之前终止，也不会创建第二条提醒。对于尚未实现幂等查找且 `supportsRetry == false` 的副作用 Tool，仍然标记为结果不确定的失败，不自动重放。

执行重试由 Runtime Harness 统一限制，Tool 负责对具体错误进行分类。只有同时满足 `supportsRetry == true` 和 `retryable` 的错误才会按退避策略重试，默认最多执行三次；权限拒绝、非法参数、目标列表不存在、用户取消以及验证失败都属于终止错误。尝试次数和计划时间先写入 SwiftData 与 checkpoint，再等待下一次执行；App 在等待期间终止时，重启后会遵守已保存的上限和计划时间继续。

运行中的取消采用两阶段安全停止：Runtime 先持久化 `cancellationRequestedAt`，立即阻止下一次执行尝试；若写入尚未开始，任务进入 `cancelled`，若写入已经交给系统并返回 receipt，则继续回读验证并进入 `completed`，同时在结果中说明取消请求晚于系统写入。App 在请求停止后被终止时，重启会先通过 Tool 幂等标记恢复结果，再决定取消或完成，不能把未知结果伪装成已取消。

每次执行尝试在调用 Tool 前持久化 `executionDeadlineAt`。Runtime 使用结构化并发限制本次调用；Deadline 到达后取消协作式执行并立即调用 Tool 恢复入口。恢复到 receipt 时继续验证，未找到结果时明确失败且不消耗新的执行尝试。App 若在执行中终止，重启后发现 Deadline 已过期，也遵循相同的“先恢复、找不到则失败”规则。

`verified` 结果应携带设备回读后确认的结构化字段。例如 `reminder.create` 返回标题、本地 ISO 8601 时间和 IANA 时区。模型最终回复只能复述 Tool Result 明确提供的事实，不能自行换算时间或补充未经验证的结果。

模型续接由 PostgreSQL 状态机协调：新上下文为 `pending`，工作实例通过原子更新领取为 `processing` 并获得有限租约；成功后进入 `completed`，上游失败进入 `failed`。并发请求不能重复领取，进程崩溃后过期租约允许其他实例恢复，`continuation_attempts` 和 `last_error_code` 保留恢复审计信息。

```text
Model Tool Call
  -> PostgreSQL opaque provider context
  -> WellPhone tool.requested
  -> Swift confirm / execute / verify
  -> PostgreSQL idempotent Tool Result
  -> Provider continuation
  -> verified assistant reply in chat
```

## 执行链

```text
AgentToolRequest
  -> ToolRegistry
  -> AgentTool.prepare
  -> confirmation gate
  -> AgentTool.execute
  -> AgentTool.verify
  -> verified task result
```

必须遵守以下不变量：

1. 未注册的 Tool 不得执行。
2. 参数必须在创建任务前完成校验。
3. Tool 声明需要确认时，未确认不得进入执行阶段。
4. 模型不能直接调用内部 executor 或 verifier。
5. 只有验证成功后，任务才能标记为 completed。
6. 执行失败或验证失败必须保留 failed 状态，不得由模型宣称成功。
7. 具有写入副作用的 Tool 只有在稳定幂等键、执行前查重和中断恢复均已实现后，才能声明支持自动重试。

## 目录职责

```text
AgentRuntime/
  AgentTool.swift       通用 Tool 契约、元数据与恢复入口
  ToolRegistry.swift    设备 capability 白名单
  AgentRuntime.swift    prepare / execute / recover / verify 调度入口

AgentTools/
  ReminderCreate/
    ReminderCreateModels.swift        参数、执行凭证和错误
    ReminderCreateTool.swift          Tool 级编排
    ReminderEventKitExecutor.swift    iOS 系统写入与回读
```

`TaskController` 负责持久化任务和步骤状态，不直接依赖 EventKit。视图只调用 `prepareTool`、`confirmTask` 和 `cancelTask`，不直接持有具体 Tool。

## 新增 Tool

新增能力时：

1. 在 `AgentTools/<Capability>/` 中实现 `AgentTool`。
2. 在客户端声明稳定的 capability、风险等级和确认策略。
3. 将内部系统操作封装在 executor 中，将结果核验封装在 verifier 中。
4. 只在 `ToolRegistry` 注册设备允许调用的 capability。
5. 在 Python Provider Adapter 中声明厂商 Tool Schema，并映射到相同 capability。
6. 使用 Fake executor 测试未确认不执行、执行后必验证、验证失败不完成。

除非某个步骤本身对用户有独立业务意义并且可以安全、幂等地单独执行，否则不要把内部步骤拆成新的模型 Tool。
