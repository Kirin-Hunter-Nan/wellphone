import Foundation

enum ServerTaskStateMapper {
    static func status(_ value: String) -> AgentTaskStatus {
        switch value {
        case "queued": .created
        case "running": .running
        case "completed": .completed
        case "failed": .failed
        case "cancelled": .cancelled
        default: .waitingForConfirmation
        }
    }

    static func phase(_ value: String, status: AgentTaskStatus) -> AgentTaskPhase {
        switch status {
        case .waitingForConfirmation: .waitingForConfirmation
        case .completed: .completed
        case .failed: .failed
        case .cancelled: .cancelled
        case .created: .planning
        case .running:
            value == "understanding" || value == "planning" ? .planning : .executing
        }
    }

    static func title(capability: String, input: [String: JSONValue]) -> String {
        if capability == "travel.plan",
           case .string(let destination) = input["destination"] {
            return "规划 \(destination) 旅行"
        }
        return "后台任务"
    }
}
