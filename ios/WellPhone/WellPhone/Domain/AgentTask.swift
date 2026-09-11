import Foundation
import SwiftData

enum AgentTaskStatus: String, Codable, Sendable {
    case created
    case running
    case waitingForConfirmation
    case completed
    case failed
    case cancelled

    var isActive: Bool {
        self == .created || self == .running || self == .waitingForConfirmation
    }
}

enum AgentTaskPhase: String, Codable, Sendable, CaseIterable {
    case understanding
    case planning
    case waitingForConfirmation
    case executing
    case verifying
    case completed
    case failed
    case cancelled

    var title: String {
        switch self {
        case .understanding: "正在理解"
        case .planning: "正在规划"
        case .waitingForConfirmation: "等待确认"
        case .executing: "正在执行"
        case .verifying: "正在验证"
        case .completed: "已完成"
        case .failed: "执行失败"
        case .cancelled: "已取消"
        }
    }
}

enum ToolResultReportState: String, Codable, Sendable {
    case pending
    case delivered
    case conflict
}

enum TaskCheckpointReportState: String, Codable, Sendable {
    case pending
    case delivered
    case conflict
}

@Model
final class AgentTask {
    @Attribute(.unique) var id: UUID
    var conversationID: UUID
    var sourceMessageID: UUID?
    var title: String
    var toolCallID: String?
    var capability: String?
    var createdAt: Date
    var updatedAt: Date
    var statusRawValue: String
    var phaseRawValue: String
    var progress: Double?
    var detail: String?
    var scheduledAt: Date?
    var targetName: String?
    var argumentsJSON: String?
    var resultSummary: String?
    var errorMessage: String?
    var executionReceiptData: Data?
    var executionAttemptCountValue: Int?
    var nextExecutionRetryAt: Date?
    var lastExecutionErrorMessage: String?
    var cancellationRequestedAt: Date?
    var resultReportStateRawValue: String?
    var resultReportError: String?
    var checkpointRevisionValue: Int?
    var checkpointReportStateRawValue: String?
    var checkpointReportError: String?

    var status: AgentTaskStatus {
        get { AgentTaskStatus(rawValue: statusRawValue) ?? .failed }
        set { statusRawValue = newValue.rawValue }
    }

    var phase: AgentTaskPhase {
        get { AgentTaskPhase(rawValue: phaseRawValue) ?? .failed }
        set { phaseRawValue = newValue.rawValue }
    }

    var resultReportState: ToolResultReportState? {
        get { resultReportStateRawValue.flatMap(ToolResultReportState.init(rawValue:)) }
        set { resultReportStateRawValue = newValue?.rawValue }
    }

    var executionAttemptCount: Int {
        get { executionAttemptCountValue ?? 0 }
        set { executionAttemptCountValue = newValue }
    }

    var checkpointRevision: Int {
        get { checkpointRevisionValue ?? 0 }
        set { checkpointRevisionValue = newValue }
    }

    var checkpointReportState: TaskCheckpointReportState? {
        get {
            checkpointReportStateRawValue.flatMap(TaskCheckpointReportState.init(rawValue:))
        }
        set { checkpointReportStateRawValue = newValue?.rawValue }
    }

    init(
        id: UUID = UUID(),
        conversationID: UUID,
        sourceMessageID: UUID? = nil,
        title: String,
        toolCallID: String? = nil,
        capability: String? = nil,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        status: AgentTaskStatus = .created,
        phase: AgentTaskPhase = .understanding,
        progress: Double? = nil,
        detail: String? = nil,
        scheduledAt: Date? = nil,
        targetName: String? = nil,
        argumentsJSON: String? = nil,
        resultSummary: String? = nil,
        errorMessage: String? = nil,
        executionReceiptData: Data? = nil,
        executionAttemptCount: Int? = nil,
        nextExecutionRetryAt: Date? = nil,
        lastExecutionErrorMessage: String? = nil,
        cancellationRequestedAt: Date? = nil,
        resultReportState: ToolResultReportState? = nil,
        resultReportError: String? = nil,
        checkpointRevision: Int? = nil,
        checkpointReportState: TaskCheckpointReportState? = nil,
        checkpointReportError: String? = nil
    ) {
        self.id = id
        self.conversationID = conversationID
        self.sourceMessageID = sourceMessageID
        self.title = title
        self.toolCallID = toolCallID
        self.capability = capability
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.statusRawValue = status.rawValue
        self.phaseRawValue = phase.rawValue
        self.progress = progress
        self.detail = detail
        self.scheduledAt = scheduledAt
        self.targetName = targetName
        self.argumentsJSON = argumentsJSON
        self.resultSummary = resultSummary
        self.errorMessage = errorMessage
        self.executionReceiptData = executionReceiptData
        self.executionAttemptCountValue = executionAttemptCount
        self.nextExecutionRetryAt = nextExecutionRetryAt
        self.lastExecutionErrorMessage = lastExecutionErrorMessage
        self.cancellationRequestedAt = cancellationRequestedAt
        self.resultReportStateRawValue = resultReportState?.rawValue
        self.resultReportError = resultReportError
        self.checkpointRevisionValue = checkpointRevision
        self.checkpointReportStateRawValue = checkpointReportState?.rawValue
        self.checkpointReportError = checkpointReportError
    }
}
