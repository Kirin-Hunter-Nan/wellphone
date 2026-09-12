import Foundation

struct URLSessionModelGateway: ModelGateway {
    let baseURL: URL
    var session: URLSession = .shared

    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID,
        requestID: String
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try makeRequest(
                        messages: messages,
                        conversationID: conversationID,
                        requestID: requestID
                    )
                    var inProgressRetryCount = 0

                    while true {
                        let (bytes, response) = try await session.bytes(for: request)
                        guard let httpResponse = response as? HTTPURLResponse else {
                            throw ModelGatewayError.invalidResponse
                        }

                        guard (200..<300).contains(httpResponse.statusCode) else {
                            let failure = await serverFailure(from: bytes)
                            if ModelGatewayRetryPolicy.shouldRetry(
                                statusCode: httpResponse.statusCode,
                                errorCode: failure.code,
                                retryCount: inProgressRetryCount
                            ) {
                                inProgressRetryCount += 1
                                let delay = ModelGatewayRetryPolicy.delaySeconds(
                                    retryAfter: httpResponse.value(
                                        forHTTPHeaderField: "Retry-After"
                                    )
                                )
                                try await Task.sleep(for: .seconds(delay))
                                continue
                            }
                            throw ModelGatewayError.server(
                                statusCode: httpResponse.statusCode,
                                message: failure.message
                            )
                        }
                        guard httpResponse.value(forHTTPHeaderField: "Content-Type")?
                            .lowercased()
                            .contains("text/event-stream") == true else {
                            throw ModelGatewayError.invalidResponse
                        }

                        for try await line in bytes.lines {
                            try Task.checkCancellation()
                            switch try WellPhoneStreamDecoder.decode(line: line) {
                            case .responseStarted:
                                continue
                            case .textDelta(let text):
                                continuation.yield(.textDelta(text))
                            case .toolRequested(let request):
                                continuation.yield(.toolRequest(request))
                            case .completed:
                                continuation.yield(.done)
                                continuation.finish()
                                return
                            case .failed(let message):
                                throw ModelGatewayError.server(statusCode: 200, message: message)
                            case .ignored:
                                continue
                            }
                        }
                        throw ModelGatewayError.invalidResponse
                    }
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
        conversationID: UUID,
        requestID: String
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
        request.httpBody = try JSONEncoder().encode(ChatRequest(
            requestID: requestID,
            protocolVersion: WellPhoneStreamDecoder.protocolVersion,
            messages: messages,
            deviceContext: DeviceContext(
                locale: Locale.current.identifier,
                timeZone: TimeZone.current.identifier,
                capabilitySetVersion: "ios-v1"
            )
        ))
        return request
    }

    private func serverFailure(from bytes: URLSession.AsyncBytes) async -> ServerFailure {
        do {
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
                if data.count >= 32_768 { break }
            }
            let envelope = try JSONDecoder().decode(ServerErrorEnvelope.self, from: data)
            return ServerFailure(
                code: envelope.error.code,
                message: envelope.error.message
            )
        } catch {
            return ServerFailure(code: nil, message: "模型代理请求失败。")
        }
    }
}

enum ModelGatewayRetryPolicy {
    static let maximumInProgressRetries = 90

    static func shouldRetry(
        statusCode: Int,
        errorCode: String?,
        retryCount: Int
    ) -> Bool {
        statusCode == 503
            && errorCode == "chat_request_in_progress"
            && retryCount < maximumInProgressRetries
    }

    static func delaySeconds(retryAfter: String?) -> Int {
        min(max(Int(retryAfter ?? "") ?? 2, 1), 5)
    }
}

private struct ChatRequest: Encodable {
    let requestID: String
    let protocolVersion: String
    let messages: [ChatPromptMessage]
    let deviceContext: DeviceContext

    enum CodingKeys: String, CodingKey {
        case requestID = "requestId"
        case protocolVersion
        case messages
        case deviceContext
    }
}

private struct DeviceContext: Encodable {
    let locale: String
    let timeZone: String
    let capabilitySetVersion: String
}

private struct ServerErrorEnvelope: Decodable {
    struct ErrorBody: Decodable {
        let code: String?
        let message: String
    }

    let error: ErrorBody
}

private struct ServerFailure {
    let code: String?
    let message: String
}

enum ModelGatewayError: LocalizedError {
    case invalidResponse
    case server(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            "AI 服务返回了无效响应。"
        case .server(_, let message):
            message
        }
    }
}
