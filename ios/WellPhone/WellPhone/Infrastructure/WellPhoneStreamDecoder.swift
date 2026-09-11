import Foundation

enum WellPhoneStreamEvent: Equatable {
    case responseStarted(String)
    case textDelta(String)
    case toolRequested(AgentToolRequest)
    case completed
    case failed(message: String)
    case ignored
}

enum WellPhoneStreamDecoder {
    static let protocolVersion = "1.0"

    static func decode(line: String) throws -> WellPhoneStreamEvent {
        guard line.hasPrefix("data:") else { return .ignored }

        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        guard !payload.isEmpty else { return .ignored }
        let data = Data(payload.utf8)
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = object["type"] as? String else {
            throw WellPhoneProtocolError.invalidEvent
        }
        guard object["protocolVersion"] as? String == protocolVersion else {
            throw WellPhoneProtocolError.unsupportedVersion
        }

        switch type {
        case "response.started":
            guard let responseID = object["responseId"] as? String else {
                throw WellPhoneProtocolError.invalidEvent
            }
            return .responseStarted(responseID)

        case "assistant.delta":
            guard let text = object["text"] as? String else {
                throw WellPhoneProtocolError.invalidEvent
            }
            return text.isEmpty ? .ignored : .textDelta(text)

        case "tool.requested":
            guard let toolCallID = object["toolCallId"] as? String,
                  !toolCallID.isEmpty,
                  let capability = object["capability"] as? String,
                  !capability.isEmpty,
                  let arguments = object["arguments"] as? [String: Any],
                  JSONSerialization.isValidJSONObject(arguments),
                  let argumentsJSON = String(
                    data: try JSONSerialization.data(
                        withJSONObject: arguments,
                        options: [.sortedKeys]
                    ),
                    encoding: .utf8
                  ) else {
                throw WellPhoneProtocolError.invalidEvent
            }
            return .toolRequested(AgentToolRequest(
                id: toolCallID,
                capability: capability,
                arguments: argumentsJSON
            ))

        case "response.completed":
            return .completed

        case "response.failed":
            guard let error = object["error"] as? [String: Any],
                  let message = error["message"] as? String else {
                throw WellPhoneProtocolError.invalidEvent
            }
            return .failed(message: message)

        default:
            return .ignored
        }
    }
}

enum WellPhoneProtocolError: LocalizedError {
    case invalidEvent
    case unsupportedVersion

    var errorDescription: String? {
        switch self {
        case .invalidEvent:
            "AI 服务返回了无效的协议事件。"
        case .unsupportedVersion:
            "AI 服务使用了客户端不支持的协议版本。"
        }
    }
}
