import Foundation

enum AgentToolResultStatus: String, Codable, Equatable, Sendable {
    case verified
    case declined
    case failed
}

struct AgentToolResultReport: Encodable, Equatable, Sendable {
    struct ResultBody: Encodable, Equatable, Sendable {
        let summary: String
        let title: String?
        let dueAt: String?
        let timeZone: String?

        init(
            summary: String,
            title: String? = nil,
            dueAt: String? = nil,
            timeZone: String? = nil
        ) {
            self.summary = summary
            self.title = title
            self.dueAt = dueAt
            self.timeZone = timeZone
        }
    }

    struct ErrorBody: Encodable, Equatable, Sendable {
        let code: String
        let message: String
    }

    let requestID: String
    let protocolVersion: String
    let toolCallID: String
    let taskID: UUID
    let capability: String
    let status: AgentToolResultStatus
    let result: ResultBody?
    let error: ErrorBody?

    enum CodingKeys: String, CodingKey {
        case requestID = "requestId"
        case protocolVersion
        case toolCallID = "toolCallId"
        case taskID = "taskId"
        case capability
        case status
        case result
        case error
    }
}

protocol ToolResultReporting: Sendable {
    func report(
        _ result: AgentToolResultReport,
        conversationID: UUID
    ) async throws -> ToolResultAcknowledgement
}

struct ToolResultAcknowledgement: Decodable, Equatable, Sendable {
    enum ContinuationStatus: String, Decodable, Equatable, Sendable {
        case completed
        case unavailable
    }

    let accepted: Bool
    let duplicate: Bool
    let continuationStatus: ContinuationStatus
    let assistantMessage: String?
    let protocolVersion: String
}

struct URLSessionToolResultReporter: ToolResultReporting {
    let baseURL: URL
    var session: URLSession = .shared

    func report(
        _ result: AgentToolResultReport,
        conversationID: UUID
    ) async throws -> ToolResultAcknowledgement {
        let endpoint = baseURL
            .appendingPathComponent("v1")
            .appendingPathComponent("conversations")
            .appendingPathComponent(conversationID.uuidString.lowercased())
            .appendingPathComponent("tool-results")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(result)

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw ToolResultReporterError.invalidResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let message = (try? JSONDecoder().decode(ToolResultServerErrorEnvelope.self, from: data))?
                .error.message ?? "Tool 结果同步失败。"
            throw ToolResultReporterError.server(
                statusCode: httpResponse.statusCode,
                message: message
            )
        }

        let acknowledgement = try JSONDecoder().decode(
            ToolResultAcknowledgement.self,
            from: data
        )
        guard acknowledgement.accepted,
              acknowledgement.protocolVersion == WellPhoneStreamDecoder.protocolVersion else {
            throw ToolResultReporterError.invalidResponse
        }
        return acknowledgement
    }
}

struct DisabledToolResultReporter: ToolResultReporting {
    func report(
        _ result: AgentToolResultReport,
        conversationID: UUID
    ) async throws -> ToolResultAcknowledgement {
        ToolResultAcknowledgement(
            accepted: true,
            duplicate: false,
            continuationStatus: .unavailable,
            assistantMessage: nil,
            protocolVersion: WellPhoneStreamDecoder.protocolVersion
        )
    }
}

private struct ToolResultServerErrorEnvelope: Decodable {
    struct ErrorBody: Decodable {
        let message: String
    }

    let error: ErrorBody
}

enum ToolResultReporterError: LocalizedError {
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
            "AI 服务返回了无效的 Tool 结果确认。"
        case .server(_, let message):
            message
        }
    }
}
