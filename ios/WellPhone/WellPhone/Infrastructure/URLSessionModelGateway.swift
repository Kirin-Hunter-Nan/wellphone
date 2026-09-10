import Foundation

struct URLSessionModelGateway: ModelGateway {
    let baseURL: URL
    var session: URLSession = .shared

    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var toolCalls: [Int: ToolCallAccumulator] = [:]
                    let request = try makeRequest(
                        messages: messages,
                        conversationID: conversationID
                    )
                    let (bytes, response) = try await session.bytes(for: request)
                    guard let httpResponse = response as? HTTPURLResponse else {
                        throw ModelGatewayError.invalidResponse
                    }

                    guard (200..<300).contains(httpResponse.statusCode) else {
                        throw await serverError(from: bytes, statusCode: httpResponse.statusCode)
                    }

                    for try await line in bytes.lines {
                        try Task.checkCancellation()
                        switch try QwenStreamDecoder.decode(line: line) {
                        case .textDelta(let text):
                            continuation.yield(.textDelta(text))
                        case .toolCallDelta(let index, let id, let name, let arguments):
                            var accumulator = toolCalls[index] ?? ToolCallAccumulator()
                            if let id, !id.isEmpty { accumulator.id = id }
                            if let name, !name.isEmpty { accumulator.name = name }
                            if let arguments { accumulator.arguments += arguments }
                            toolCalls[index] = accumulator
                        case .done:
                            for index in toolCalls.keys.sorted() {
                                guard let call = toolCalls[index], !call.name.isEmpty else { continue }
                                continuation.yield(.toolCall(ModelToolCall(
                                    id: call.id,
                                    name: call.name,
                                    arguments: call.arguments
                                )))
                            }
                            continuation.yield(.done)
                            continuation.finish()
                            return
                        case .ignored:
                            continue
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }

            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    private func makeRequest(
        messages: [ChatPromptMessage],
        conversationID: UUID
    ) throws -> URLRequest {
        let endpoint = baseURL
            .appendingPathComponent("v1")
            .appendingPathComponent("conversations")
            .appendingPathComponent(conversationID.uuidString.lowercased())
            .appendingPathComponent("messages")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(ChatRequest(messages: messages))
        return request
    }

    private func serverError(
        from bytes: URLSession.AsyncBytes,
        statusCode: Int
    ) async -> ModelGatewayError {
        do {
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
                if data.count >= 32_768 { break }
            }
            let envelope = try JSONDecoder().decode(ServerErrorEnvelope.self, from: data)
            return .server(statusCode: statusCode, message: envelope.error.message)
        } catch {
            return .server(statusCode: statusCode, message: "模型代理请求失败。")
        }
    }
}

private struct ToolCallAccumulator {
    var id = ""
    var name = ""
    var arguments = ""
}

private struct ChatRequest: Encodable {
    let messages: [ChatPromptMessage]
}

private struct ServerErrorEnvelope: Decodable {
    struct ErrorBody: Decodable {
        let message: String
    }

    let error: ErrorBody
}

enum ModelGatewayError: LocalizedError {
    case invalidResponse
    case server(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            "模型代理返回了无效响应。"
        case .server(_, let message):
            message
        }
    }
}
