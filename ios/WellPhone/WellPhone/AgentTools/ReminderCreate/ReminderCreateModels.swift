import Foundation

struct ReminderDraft: Codable, Equatable, Sendable {
    let title: String
    let dueAt: Date
    let notes: String?
    let listName: String?

    static func decode(arguments: String, now: Date = Date()) throws -> ReminderDraft {
        guard let data = arguments.data(using: .utf8) else {
            throw ReminderToolError.invalidArguments
        }

        let raw: RawReminderArguments
        do {
            raw = try JSONDecoder().decode(RawReminderArguments.self, from: data)
        } catch {
            throw ReminderToolError.invalidArguments
        }

        let title = raw.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, title.count <= 200 else {
            throw ReminderToolError.invalidTitle
        }
        guard let dueAt = parseISO8601(raw.dueAt),
              dueAt > now.addingTimeInterval(-60) else {
            throw ReminderToolError.invalidDueDate
        }
        let notes = raw.notes?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (notes?.count ?? 0) <= 2_000 else {
            throw ReminderToolError.notesTooLong
        }
        let listName = raw.listName?.trimmingCharacters(in: .whitespacesAndNewlines)

        return ReminderDraft(
            title: title,
            dueAt: dueAt,
            notes: notes?.isEmpty == true ? nil : notes,
            listName: listName?.isEmpty == true ? nil : listName
        )
    }

    private static func parseISO8601(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }
}

private struct RawReminderArguments: Decodable {
    let title: String
    let dueAt: String
    let notes: String?
    let listName: String?
}

struct CreatedReminder: Codable, Sendable {
    let identifier: String
    let listTitle: String
}

struct VerifiedReminder: Sendable {
    let listTitle: String
    let identifierDigest: String
}

enum ReminderToolError: LocalizedError {
    case invalidArguments
    case invalidTitle
    case invalidDueDate
    case notesTooLong
    case accessDenied
    case listNotFound(String)
    case noDefaultList
    case transientSystemFailure(String)
    case missingIdentifier
    case invalidExecutionReceipt
    case verificationFailed

    var errorDescription: String? {
        switch self {
        case .invalidArguments: "模型返回的提醒参数无法解析。"
        case .invalidTitle: "提醒标题为空或过长。"
        case .invalidDueDate: "提醒时间无效或已经过去。"
        case .notesTooLong: "提醒备注过长。"
        case .accessDenied: "没有获得提醒事项的完整访问权限。"
        case .listNotFound(let name): "找不到名为“\(name)”的提醒列表。"
        case .noDefaultList: "系统没有可用的默认提醒列表。"
        case .transientSystemFailure(let message): message
        case .missingIdentifier: "系统没有返回提醒事项标识。"
        case .invalidExecutionReceipt: "提醒工具返回了无效的执行凭证。"
        case .verificationFailed: "提醒已写入，但回读验证失败。"
        }
    }
}
