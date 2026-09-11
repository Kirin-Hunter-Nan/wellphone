import Foundation
import SwiftData

enum AgentTaskStepStatus: String, Codable, Sendable {
    case pending
    case running
    case completed
    case failed
    case cancelled
}

@Model
final class AgentTaskStep {
    @Attribute(.unique) var id: UUID
    var taskID: UUID
    var sequence: Int
    var title: String
    var statusRawValue: String
    var startedAt: Date?
    var completedAt: Date?

    var status: AgentTaskStepStatus {
        get { AgentTaskStepStatus(rawValue: statusRawValue) ?? .failed }
        set { statusRawValue = newValue.rawValue }
    }

    init(
        id: UUID = UUID(),
        taskID: UUID,
        sequence: Int,
        title: String,
        status: AgentTaskStepStatus = .pending,
        startedAt: Date? = nil,
        completedAt: Date? = nil
    ) {
        self.id = id
        self.taskID = taskID
        self.sequence = sequence
        self.title = title
        self.statusRawValue = status.rawValue
        self.startedAt = startedAt
        self.completedAt = completedAt
    }
}
