import Foundation

enum TaskExecutionLocation: String, Codable, Equatable, Sendable {
    case device
    case server
}

struct ServerTaskSnapshot: Decodable, Equatable, Sendable {
    struct Step: Decodable, Equatable, Sendable {
        let order: Int
        let title: String
        let status: String
        let detail: String?
    }

    struct Artifact: Decodable, Equatable, Sendable {
        let id: UUID
        let kind: String
        let title: String
        let contentType: String
        let payload: JSONValue?
        let storageReference: String?
        let createdAt: Date
    }

    let id: UUID
    let conversationId: UUID
    let capability: String
    let title: String
    let status: String
    let phase: String
    let progress: Double
    let detail: String?
    let resultSummary: String?
    let errorMessage: String?
    let attemptCount: Int
    let updatedAt: Date
    let steps: [Step]
    let artifacts: [Artifact]
}

struct DeviceToolRequest: Decodable, Equatable, Sendable {
    let taskId: UUID
    let toolCallId: String
    let toolName: String
    let arguments: [String: JSONValue]
}

struct DeviceToolExecutionResult: Equatable, Sendable {
    struct Failure: Equatable, Sendable {
        let code: String
        let message: String
    }

    let result: [String: JSONValue]?
    let failure: Failure?

    static func completed(_ result: [String: JSONValue]) -> Self {
        Self(result: result, failure: nil)
    }

    static func failed(code: String, message: String) -> Self {
        Self(result: nil, failure: Failure(code: code, message: message))
    }
}

protocol ServerTaskServing: Sendable {
    func create(
        conversationID: UUID,
        toolCallID: String,
        capability: String,
        title: String,
        input: [String: JSONValue]
    ) async throws -> ServerTaskSnapshot
    func confirm(taskID: UUID) async throws -> ServerTaskSnapshot
    func cancel(taskID: UUID) async throws -> ServerTaskSnapshot
    func get(taskID: UUID) async throws -> ServerTaskSnapshot
    func pendingDeviceTool(taskID: UUID) async throws -> DeviceToolRequest?
    func submitDeviceToolResult(
        taskID: UUID,
        toolCallID: String,
        result: DeviceToolExecutionResult
    ) async throws
}

extension ServerTaskServing {
    func pendingDeviceTool(taskID: UUID) async throws -> DeviceToolRequest? { nil }

    func submitDeviceToolResult(
        taskID: UUID,
        toolCallID: String,
        result: DeviceToolExecutionResult
    ) async throws {}
}

struct URLSessionServerTaskClient: ServerTaskServing {
    let baseURL: URL
    var session: URLSession = .shared

    func create(
        conversationID: UUID,
        toolCallID: String,
        capability: String,
        title: String,
        input: [String: JSONValue]
    ) async throws -> ServerTaskSnapshot {
        struct Body: Encodable {
            let capability: String
            let title: String
            let input: [String: JSONValue]
            let requiresConfirmation = false
            let toolCallId: String
        }
        return try await send(
            method: "POST",
            path: ["v1", "conversations", conversationID.uuidString.lowercased(), "tasks"],
            body: Body(
                capability: capability, title: title, input: input, toolCallId: toolCallID
            )
        )
    }

    func confirm(taskID: UUID) async throws -> ServerTaskSnapshot {
        try await send(method: "POST", path: ["v1", "tasks", taskID.uuidString.lowercased(), "confirm"])
    }

    func cancel(taskID: UUID) async throws -> ServerTaskSnapshot {
        try await send(method: "POST", path: ["v1", "tasks", taskID.uuidString.lowercased(), "cancel"])
    }

    func get(taskID: UUID) async throws -> ServerTaskSnapshot {
        try await send(method: "GET", path: ["v1", "tasks", taskID.uuidString.lowercased()])
    }

    func pendingDeviceTool(taskID: UUID) async throws -> DeviceToolRequest? {
        var request = URLRequest(url: endpoint([
            "v1", "tasks", taskID.uuidString.lowercased(), "device-tools", "pending",
        ]))
        request.httpMethod = "GET"
        request.timeoutInterval = 30
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ServerTaskClientError.requestFailed
        }
        if http.statusCode == 204 { return nil }
        guard (200..<300).contains(http.statusCode) else {
            throw ServerTaskClientError.requestFailed
        }
        return try decoder().decode(DeviceToolRequest.self, from: data)
    }

    func submitDeviceToolResult(
        taskID: UUID,
        toolCallID: String,
        result: DeviceToolExecutionResult
    ) async throws {
        struct ErrorBody: Encodable {
            let code: String
            let message: String
        }
        struct Body: Encodable {
            let requestId: String
            let protocolVersion = "1.0"
            let toolCallId: String
            let status: String
            let result: [String: JSONValue]?
            let error: ErrorBody?
        }
        let body = Body(
            requestId: "device-tool-result-\(taskID.uuidString.lowercased())-\(toolCallID.prefix(100))",
            toolCallId: toolCallID,
            status: result.failure == nil ? "completed" : "failed",
            result: result.result,
            error: result.failure.map { ErrorBody(code: $0.code, message: $0.message) }
        )
        var request = URLRequest(url: endpoint([
            "v1", "tasks", taskID.uuidString.lowercased(), "device-tools", "results",
        ]))
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            throw ServerTaskClientError.requestFailed
        }
    }

    private func send<Body: Encodable>(method: String, path: [String], body: Body) async throws -> ServerTaskSnapshot {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        return try await response(for: request)
    }

    private func send(method: String, path: [String]) async throws -> ServerTaskSnapshot {
        var request = URLRequest(url: endpoint(path))
        request.httpMethod = method
        request.timeoutInterval = 30
        return try await response(for: request)
    }

    private func endpoint(_ path: [String]) -> URL {
        path.reduce(baseURL) { $0.appendingPathComponent($1) }
    }

    private func response(for request: URLRequest) async throws -> ServerTaskSnapshot {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw ServerTaskClientError.requestFailed
        }
        return try decoder().decode(ServerTaskSnapshot.self, from: data)
    }

    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

private enum ServerTaskClientError: LocalizedError {
    case requestFailed

    var errorDescription: String? { "后台任务服务暂时不可用。" }
}
