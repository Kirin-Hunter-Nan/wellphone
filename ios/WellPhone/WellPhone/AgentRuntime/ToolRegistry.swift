import Foundation

@MainActor
final class ToolRegistry {
    private var toolsByModelName: [String: any AgentTool] = [:]
    private var toolsByCapability: [String: any AgentTool] = [:]

    init(tools: [any AgentTool]) {
        for tool in tools {
            register(tool)
        }
    }

    func tool(modelName: String) -> (any AgentTool)? {
        toolsByModelName[modelName]
    }

    func tool(capability: String) -> (any AgentTool)? {
        toolsByCapability[capability]
    }

    private func register(_ tool: any AgentTool) {
        let descriptor = tool.descriptor
        precondition(
            toolsByModelName[descriptor.modelName] == nil,
            "Duplicate model tool name: \(descriptor.modelName)"
        )
        precondition(
            toolsByCapability[descriptor.capability] == nil,
            "Duplicate tool capability: \(descriptor.capability)"
        )
        toolsByModelName[descriptor.modelName] = tool
        toolsByCapability[descriptor.capability] = tool
    }
}
