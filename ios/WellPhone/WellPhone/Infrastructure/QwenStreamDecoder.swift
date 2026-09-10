import Foundation

enum QwenStreamEvent: Equatable {
    case delta(String)
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
        guard let text = chunk.choices.first?.delta.content, !text.isEmpty else {
            return .ignored
        }
        return .delta(text)
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
}
