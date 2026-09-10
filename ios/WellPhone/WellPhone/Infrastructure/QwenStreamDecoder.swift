import Foundation

enum QwenStreamEvent: Equatable {
    case textDelta(String)
    case toolCallDelta(index: Int, id: String?, name: String?, arguments: String?)
    case done
    case ignored
}

enum QwenStreamDecoder {
    static func decode(line: String) throws -> QwenStreamEvent {
        guard line.hasPrefix("data:") else { return .ignored }

        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        guard !payload.isEmpty else { return .ignored }
        if payload == "[DONE]" { return .done }

        let data = Data(payload.utf8)
        let chunk = try JSONDecoder().decode(StreamChunk.self, from: data)
        guard let delta = chunk.choices.first?.delta else { return .ignored }
        if let text = delta.content, !text.isEmpty {
            return .textDelta(text)
        }
        if let toolCall = delta.toolCalls?.first {
            return .toolCallDelta(
                index: toolCall.index,
                id: toolCall.id,
                name: toolCall.function?.name,
                arguments: toolCall.function?.arguments
            )
        }
        return .ignored
    }
}

private struct StreamChunk: Decodable {
    let choices: [Choice]
}

private struct Choice: Decodable {
    let delta: Delta
}

private struct Delta: Decodable {
    let content: String?
    let toolCalls: [ToolCallDelta]?

    enum CodingKeys: String, CodingKey {
        case content
        case toolCalls = "tool_calls"
    }
}

private struct ToolCallDelta: Decodable {
    let index: Int
    let id: String?
    let function: FunctionDelta?
}

private struct FunctionDelta: Decodable {
    let name: String?
    let arguments: String?
}
