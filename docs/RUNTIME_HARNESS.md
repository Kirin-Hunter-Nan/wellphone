# WellPhone Runtime Harness

## 边界

模型只请求业务能力，不直接调用能力内部的原子操作。一个模型可见 Tool 可以由多个确定性步骤组成；确认、执行与验证的顺序由 Runtime Harness 保证。

`reminder_create` 是模型协议中的单一 Tool，对应平台无关 capability `reminder.create`。参数解析、用户确认、EventKit 写入和回读验证不是独立的模型 Tool。

## 执行链

```text
ModelToolCall
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
7. 在幂等机制落地前，具有写入副作用的 Tool 不得声明支持自动重试。

## 目录职责

```text
AgentRuntime/
  AgentTool.swift       通用 Tool 契约与元数据
  ToolRegistry.swift    模型名称和 capability 白名单
  AgentRuntime.swift    prepare / execute / verify 调度入口

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
2. 声明稳定的 model name、capability、风险等级和确认策略。
3. 将内部系统操作封装在 executor 中，将结果核验封装在 verifier 中。
4. 只在 `ToolRegistry` 注册模型允许调用的 Tool。
5. 使用 Fake executor 测试未确认不执行、执行后必验证、验证失败不完成。

除非某个步骤本身对用户有独立业务意义并且可以安全、幂等地单独执行，否则不要把内部步骤拆成新的模型 Tool。
