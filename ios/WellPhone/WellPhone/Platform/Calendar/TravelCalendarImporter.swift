import EventKit
import Foundation

private struct TravelCalendarPayload: Decodable {
    struct Event: Decodable {
        let title: String
        let startLocal: String
        let endLocal: String
        let location: String?
        let url: String?
        let notes: String?
    }

    let timeZone: String?
    let events: [Event]
}

@MainActor
protocol TravelCalendarImporting: Sendable {
    func importEvents(payload: Data) async throws -> [String]
}

@MainActor
final class EventKitTravelCalendarImporter: TravelCalendarImporting {
    private let store = EKEventStore()

    func importEvents(payload: Data) async throws -> [String] {
        let plan = try JSONDecoder().decode(TravelCalendarPayload.self, from: payload)
        guard !plan.events.isEmpty else { throw TravelCalendarImportError.noEvents }
        guard try await store.requestFullAccessToEvents() else {
            throw TravelCalendarImportError.accessDenied
        }
        guard let calendar = store.defaultCalendarForNewEvents else {
            throw TravelCalendarImportError.noWritableCalendar
        }
        let timeZone = plan.timeZone.flatMap(TimeZone.init(identifier:)) ?? .current
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"

        var identifiers: [String] = []
        for item in plan.events {
            guard let start = formatter.date(from: item.startLocal),
                  let end = formatter.date(from: item.endLocal) else {
                throw TravelCalendarImportError.invalidDate
            }
            let event = EKEvent(eventStore: store)
            event.calendar = calendar
            event.title = item.title
            event.startDate = start
            event.endDate = end
            event.timeZone = timeZone
            event.location = item.location
            event.url = item.url.flatMap(URL.init(string:))
            event.notes = item.notes
            try store.save(event, span: .thisEvent, commit: false)
            if let identifier = event.eventIdentifier {
                identifiers.append(identifier)
            }
        }
        try store.commit()
        return identifiers
    }
}

private enum TravelCalendarImportError: LocalizedError {
    case noEvents
    case accessDenied
    case noWritableCalendar
    case invalidDate

    var errorDescription: String? {
        switch self {
        case .noEvents: "行程中没有可添加的日历事件。"
        case .accessDenied: "没有获得日历访问权限。"
        case .noWritableCalendar: "没有可写入的系统日历。"
        case .invalidDate: "行程中包含无法识别的日期。"
        }
    }
}
