import Foundation

@MainActor
final class ToolRegistry {
    private var toolsByCapability: [String: any AgentTool] = [:]

    init(tools: [any AgentTool]) {
        for tool in tools {
            register(tool)
        }
    }

    func tool(capability: String) -> (any AgentTool)? {
        toolsByCapability[capability]
    }

    private func register(_ tool: any AgentTool) {
        let descriptor = tool.descriptor
        precondition(
            toolsByCapability[descriptor.capability] == nil,
            "Duplicate tool capability: \(descriptor.capability)"
        )
        toolsByCapability[descriptor.capability] = tool
    }
}
