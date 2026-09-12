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
            let requiresConfirmation = true
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
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(ServerTaskSnapshot.self, from: data)
    }
}

private enum ServerTaskClientError: LocalizedError {
    case requestFailed

    var errorDescription: String? { "后台任务服务暂时不可用。" }
}
