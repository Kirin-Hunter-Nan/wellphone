import Foundation

struct TaskCheckpointReport: Encodable, Equatable, Sendable {
    let requestID: String
    let protocolVersion: String
    let taskID: UUID
    let revision: Int
    let toolCallID: String
    let capability: String
    let status: AgentTaskStatus
    let phase: AgentTaskPhase
    let progress: Double?
    let detail: String?
    let resultSummary: String?
    let errorMessage: String?
    let executionAttemptCount: Int
    let nextExecutionRetryAt: Date?
    let lastExecutionErrorMessage: String?
    let occurredAt: Date

    enum CodingKeys: String, CodingKey {
        case requestID = "requestId"
        case protocolVersion
        case taskID = "taskId"
        case revision
        case toolCallID = "toolCallId"
        case capability
        case status
        case phase
        case progress
        case detail
        case resultSummary
        case errorMessage
        case executionAttemptCount
        case nextExecutionRetryAt
        case lastExecutionErrorMessage
        case occurredAt
    }
}

struct TaskCheckpointAcknowledgement: Decodable, Equatable, Sendable {
    let accepted: Bool
    let duplicate: Bool
    let applied: Bool
    let currentRevision: Int
    let gap: Bool
    let protocolVersion: String
}

protocol TaskCheckpointReporting: Sendable {
    func report(
        _ checkpoint: TaskCheckpointReport,
        conversationID: UUID
    ) async throws -> TaskCheckpointAcknowledgement
}

struct URLSessionTaskCheckpointReporter: TaskCheckpointReporting {
    let baseURL: URL
    var session: URLSession = .shared

    func report(
        _ checkpoint: TaskCheckpointReport,
        conversationID: UUID
    ) async throws -> TaskCheckpointAcknowledgement {
        let endpoint = baseURL
            .appendingPathComponent("v1")
            .appendingPathComponent("conversations")
            .appendingPathComponent(conversationID.uuidString.lowercased())
            .appendingPathComponent("task-checkpoints")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        request.httpBody = try encoder.encode(checkpoint)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw TaskCheckpointReporterError.invalidResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let message = (try? JSONDecoder().decode(
                TaskCheckpointServerErrorEnvelope.self,
                from: data
            ))?.error.message ?? "任务状态同步失败。"
            throw TaskCheckpointReporterError.server(
                statusCode: httpResponse.statusCode,
                message: message
            )
        }

        let acknowledgement = try JSONDecoder().decode(
            TaskCheckpointAcknowledgement.self,
            from: data
        )
        guard acknowledgement.accepted,
              acknowledgement.protocolVersion == WellPhoneStreamDecoder.protocolVersion else {
            throw TaskCheckpointReporterError.invalidResponse
        }
        return acknowledgement
    }
}

struct DisabledTaskCheckpointReporter: TaskCheckpointReporting {
    func report(
        _ checkpoint: TaskCheckpointReport,
        conversationID: UUID
    ) async throws -> TaskCheckpointAcknowledgement {
        TaskCheckpointAcknowledgement(
            accepted: true,
            duplicate: false,
            applied: true,
            currentRevision: checkpoint.revision,
            gap: false,
            protocolVersion: WellPhoneStreamDecoder.protocolVersion
        )
    }
}

private struct TaskCheckpointServerErrorEnvelope: Decodable {
    struct ErrorBody: Decodable {
        let message: String
    }

    let error: ErrorBody
}

enum TaskCheckpointReporterError: LocalizedError {
    case invalidResponse
    case server(statusCode: Int, message: String)

    var isConflict: Bool {
        if case .server(let statusCode, _) = self {
            return statusCode == 409
        }
        return false
    }

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            "AI 服务返回了无效的任务状态确认。"
        case .server(_, let message):
            message
        }
    }
}
