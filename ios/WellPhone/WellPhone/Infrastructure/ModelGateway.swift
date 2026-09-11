import Foundation

enum ChatPromptContentPart: Codable, Equatable, Sendable {
    case text(String)
    case imageURL(String)

    private enum CodingKeys: String, CodingKey {
        case type
        case text
        case imageURL = "image_url"
    }

    private struct ImageURLBody: Codable, Equatable, Sendable {
        let url: String
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        switch try container.decode(String.self, forKey: .type) {
        case "text":
            self = .text(try container.decode(String.self, forKey: .text))
        case "image_url":
            let body = try container.decode(ImageURLBody.self, forKey: .imageURL)
            self = .imageURL(body.url)
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unsupported chat content part."
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .text(let text):
            try container.encode("text", forKey: .type)
            try container.encode(text, forKey: .text)
        case .imageURL(let url):
            try container.encode("image_url", forKey: .type)
            try container.encode(ImageURLBody(url: url), forKey: .imageURL)
        }
    }
}

enum ChatPromptContent: Codable, Equatable, Sendable {
    case text(String)
    case parts([ChatPromptContentPart])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let text = try? container.decode(String.self) {
            self = .text(text)
        } else {
            self = .parts(try container.decode([ChatPromptContentPart].self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .text(let text):
            try container.encode(text)
        case .parts(let parts):
            try container.encode(parts)
        }
    }

    var plainText: String {
        switch self {
        case .text(let text):
            text
        case .parts(let parts):
            parts.compactMap {
                if case .text(let text) = $0 { return text }
                return nil
            }.joined(separator: "\n")
        }
    }
}

struct ChatPromptMessage: Codable, Equatable, Sendable {
    let role: MessageRole
    let content: ChatPromptContent

    init(role: MessageRole, content: String) {
        self.role = role
        self.content = .text(content)
    }

    init(role: MessageRole, parts: [ChatPromptContentPart]) {
        self.role = role
        self.content = .parts(parts)
    }
}

struct AgentToolRequest: Equatable, Sendable {
    let id: String
    let capability: String
    let arguments: String
    let executionLocation: TaskExecutionLocation

    init(
        id: String,
        capability: String,
        arguments: String,
        executionLocation: TaskExecutionLocation = .device
    ) {
        self.id = id
        self.capability = capability
        self.arguments = arguments
        self.executionLocation = executionLocation
    }
}

enum ModelGatewayEvent: Equatable, Sendable {
    case textDelta(String)
    case toolRequest(AgentToolRequest)
    case done
}

protocol ModelGateway: Sendable {
    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID,
        requestID: String
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error>
}

/// A deterministic local gateway that exercises the same client-side streaming,
/// cancellation, retry, and persistence paths without contacting the AI backend.
struct DemoModelGateway: ModelGateway {
    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID,
        requestID: String
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        _ = requestID
        let latestInput = messages.last(where: { $0.role == .user })?.content.plainText ?? ""
        let response = responseText(for: latestInput)

        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for character in response {
                        try Task.checkCancellation()
                        try await Task.sleep(for: .milliseconds(24))
                        continuation.yield(.textDelta(String(character)))
                    }
                    continuation.yield(.done)
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: CancellationError())
                } catch {
                    continuation.finish(throwing: error)
                }
            }

            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    private func responseText(for input: String) -> String {
        if input.isEmpty {
            return "我已准备好。告诉我你想完成什么。"
        }

        return "收到：\u{201c}\(input)\u{201d}\n\n当前是本地演示模式，聊天的流式输出、停止、重试与历史记录已经接通。下一步接入服务端模型代理后，我会在相同界面里给出真实回答。"
    }
}
