import Foundation

struct PreparedToolRequest: Sendable {
    let descriptor: AgentToolDescriptor
    let task: PreparedAgentToolTask
}

@MainActor
final class AgentRuntime {
    private let registry: ToolRegistry

    init(registry: ToolRegistry) {
        self.registry = registry
    }

    static func live() -> AgentRuntime {
        let reminderExecutor = ReminderEventKitExecutor()
        return AgentRuntime(
            registry: ToolRegistry(tools: [
                ReminderCreateTool(
                    creator: reminderExecutor,
                    verifier: reminderExecutor
                ),
            ])
        )
    }

    static func testing(reminderExecutor: any ReminderExecuting) -> AgentRuntime {
        AgentRuntime(
            registry: ToolRegistry(tools: [
                ReminderCreateTool(
                    creator: reminderExecutor,
                    verifier: reminderExecutor
                ),
            ])
        )
    }

    func prepare(request: AgentToolRequest) throws -> PreparedToolRequest {
        guard let tool = registry.tool(capability: request.capability) else {
            throw AgentRuntimeError.unsupportedCapability(request.capability)
        }
        return PreparedToolRequest(
            descriptor: tool.descriptor,
            task: try tool.prepare(argumentsJSON: request.arguments)
        )
    }

    func execute(task: AgentTask) async throws -> ToolExecutionReceipt {
        let resolved = try resolve(task: task)
        return try await resolved.tool.execute(
            argumentsJSON: resolved.argumentsJSON,
            idempotencyKey: task.id.uuidString.lowercased()
        )
    }

    func recoverExecution(task: AgentTask) async throws -> ToolExecutionReceipt? {
        let resolved = try resolve(task: task)
        return try await resolved.tool.recoverExecution(
            argumentsJSON: resolved.argumentsJSON,
            idempotencyKey: task.id.uuidString.lowercased()
        )
    }

    func supportsExecutionRetry(task: AgentTask) throws -> Bool {
        try resolve(task: task).tool.descriptor.supportsRetry
    }

    func executionErrorDisposition(
        _ error: any Error,
        for task: AgentTask
    ) throws -> ToolExecutionErrorDisposition {
        try resolve(task: task).tool.executionErrorDisposition(error)
    }

    func verify(
        receipt: ToolExecutionReceipt,
        for task: AgentTask
    ) throws -> VerifiedToolResult {
        let resolved = try resolve(task: task)
        return try resolved.tool.verify(
            receipt: receipt,
            argumentsJSON: resolved.argumentsJSON
        )
    }

    private func resolve(
        task: AgentTask
    ) throws -> (tool: any AgentTool, argumentsJSON: String) {
        guard let capability = task.capability else {
            throw AgentRuntimeError.missingCapability
        }
        guard let tool = registry.tool(capability: capability) else {
            throw AgentRuntimeError.unsupportedCapability(capability)
        }
        guard let argumentsJSON = task.argumentsJSON else {
            throw AgentRuntimeError.missingArguments
        }
        return (tool, argumentsJSON)
    }
}

enum AgentRuntimeError: LocalizedError {
    case unsupportedCapability(String)
    case conflictingToolRequest(String)
    case missingCapability
    case missingArguments

    var errorDescription: String? {
        switch self {
        case .unsupportedCapability(let capability):
            "任务使用了尚未注册的能力：\(capability)"
        case .conflictingToolRequest(let toolCallID):
            "工具请求 \(toolCallID) 与已保存任务不一致。"
        case .missingCapability:
            "任务缺少工具能力标识。"
        case .missingArguments:
            "任务缺少工具参数。"
        }
    }
}
