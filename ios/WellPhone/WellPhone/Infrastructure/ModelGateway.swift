import Foundation

struct ChatPromptMessage: Codable, Equatable, Sendable {
    let role: MessageRole
    let content: String
}

struct AgentToolRequest: Equatable, Sendable {
    let id: String
    let capability: String
    let arguments: String
}

enum ModelGatewayEvent: Equatable, Sendable {
    case textDelta(String)
    case toolRequest(AgentToolRequest)
    case done
}

protocol ModelGateway: Sendable {
    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error>
}

/// A deterministic local gateway that exercises the same client-side streaming,
/// cancellation, retry, and persistence paths without contacting the AI backend.
struct DemoModelGateway: ModelGateway {
    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        let latestInput = messages.last(where: { $0.role == .user })?.content ?? ""
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
