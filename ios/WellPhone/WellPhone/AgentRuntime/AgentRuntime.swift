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

    func prepare(call: ModelToolCall) throws -> PreparedToolRequest {
        guard let tool = registry.tool(modelName: call.name) else {
            throw AgentRuntimeError.unsupportedModelTool(call.name)
        }
        return PreparedToolRequest(
            descriptor: tool.descriptor,
            task: try tool.prepare(argumentsJSON: call.arguments)
        )
    }

    func execute(task: AgentTask) async throws -> ToolExecutionReceipt {
        let resolved = try resolve(task: task)
        return try await resolved.tool.execute(argumentsJSON: resolved.argumentsJSON)
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
    case unsupportedModelTool(String)
    case unsupportedCapability(String)
    case missingCapability
    case missingArguments

    var errorDescription: String? {
        switch self {
        case .unsupportedModelTool(let name):
            "模型请求了尚未支持的工具：\(name)"
        case .unsupportedCapability(let capability):
            "任务使用了尚未注册的能力：\(capability)"
        case .missingCapability:
            "任务缺少工具能力标识。"
        case .missingArguments:
            "任务缺少工具参数。"
        }
    }
}
