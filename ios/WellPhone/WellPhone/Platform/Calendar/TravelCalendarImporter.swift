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
    /// Imports the requested events exactly once for this task and only returns
    /// after every event can be read back from EventKit with matching fields.
    func importEvents(payload: Data, idempotencyKey: String) async throws -> [String]
}

@MainActor
final class EventKitTravelCalendarImporter: TravelCalendarImporting {
    private let store = EKEventStore()

    func importEvents(payload: Data, idempotencyKey: String) async throws -> [String] {
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

        let prepared = try plan.events.enumerated().map { index, item in
            guard let start = formatter.date(from: item.startLocal),
                  let end = formatter.date(from: item.endLocal) else {
                throw TravelCalendarImportError.invalidDate
            }
            guard end > start else { throw TravelCalendarImportError.invalidDate }
            let idempotencyURL = idempotencyURL(
                preserving: item.url.flatMap(URL.init(string:)),
                key: idempotencyKey,
                index: index
            )
            return PreparedTravelCalendarEvent(
                title: item.title,
                start: start,
                end: end,
                timeZone: timeZone,
                location: item.location,
                url: idempotencyURL,
                notes: item.notes,
                idempotencyKey: idempotencyURL.absoluteString
            )
        }

        guard let firstStart = prepared.map(\.start).min(),
              let lastEnd = prepared.map(\.end).max() else {
            throw TravelCalendarImportError.noEvents
        }
        let searchStart = firstStart.addingTimeInterval(-86_400)
        let searchEnd = lastEnd.addingTimeInterval(86_400)
        let existing = existingEvents(
            between: searchStart,
            and: searchEnd,
            calendar: calendar
        )

        var candidates: [String: EKEvent] = [:]
        for draft in prepared {
            let matches = existing.filter { $0.url == draft.url }
            guard matches.count <= 1 else {
                throw TravelCalendarImportError.duplicateIdempotencyMarker
            }
            if let match = matches.first {
                guard event(match, matches: draft, calendar: calendar) else {
                    throw TravelCalendarImportError.conflictingExistingEvent
                }
                candidates[draft.idempotencyKey] = match
                continue
            }

            let event = EKEvent(eventStore: store)
            event.calendar = calendar
            event.title = draft.title
            event.startDate = draft.start
            event.endDate = draft.end
            event.timeZone = draft.timeZone
            event.location = draft.location
            event.url = draft.url
            event.notes = draft.notes
            try store.save(event, span: .thisEvent, commit: false)
            candidates[draft.idempotencyKey] = event
        }
        try store.commit()

        // A successful commit isn't enough: read every event back from EventKit.
        // If the process died after commit but before this point, the stable marker
        // lets the next attempt recover the existing events instead of duplicating them.
        let committed = existingEvents(
            between: searchStart,
            and: searchEnd,
            calendar: calendar
        )
        var identifiers: [String] = []
        for draft in prepared {
            let identifier = candidates[draft.idempotencyKey]?.eventIdentifier
            let readBack = identifier
                .flatMap { store.calendarItem(withIdentifier: $0) as? EKEvent }
                ?? committed.first { $0.url == draft.url }
            guard let readBack,
                  event(readBack, matches: draft, calendar: calendar),
                  !readBack.eventIdentifier.isEmpty else {
                throw TravelCalendarImportError.verificationFailed
            }
            identifiers.append(readBack.eventIdentifier)
        }
        return identifiers
    }

    private func existingEvents(
        between start: Date,
        and end: Date,
        calendar: EKCalendar
    ) -> [EKEvent] {
        let predicate = store.predicateForEvents(
            withStart: start,
            end: end,
            calendars: [calendar]
        )
        return store.events(matching: predicate)
    }

    private func event(
        _ event: EKEvent,
        matches draft: PreparedTravelCalendarEvent,
        calendar: EKCalendar
    ) -> Bool {
        event.calendar.calendarIdentifier == calendar.calendarIdentifier
            && event.title == draft.title
            && abs(event.startDate.timeIntervalSince(draft.start)) < 1
            && abs(event.endDate.timeIntervalSince(draft.end)) < 1
            && event.timeZone?.identifier == draft.timeZone.identifier
            && event.location == draft.location
            && event.url == draft.url
            && event.notes == draft.notes
    }

    private func idempotencyURL(
        preserving originalURL: URL?,
        key: String,
        index: Int
    ) -> URL {
        if let originalURL,
           var components = URLComponents(
               url: originalURL,
               resolvingAgainstBaseURL: false
           ) {
            components.fragment = "wellphone-calendar-\(key)-\(index)"
            if let taggedURL = components.url {
                return taggedURL
            }
        }
        var components = URLComponents()
        components.scheme = "wellphone"
        components.host = "calendar"
        components.path = "/\(key)/\(index)"
        return components.url!
    }
}

private struct PreparedTravelCalendarEvent {
    let title: String
    let start: Date
    let end: Date
    let timeZone: TimeZone
    let location: String?
    let url: URL?
    let notes: String?
    let idempotencyKey: String
}

enum TravelCalendarImportError: LocalizedError {
    case noEvents
    case accessDenied
    case noWritableCalendar
    case invalidDate
    case duplicateIdempotencyMarker
    case conflictingExistingEvent
    case verificationFailed

    var errorDescription: String? {
        switch self {
        case .noEvents: "行程中没有可添加的日历事件。"
        case .accessDenied: "没有获得日历访问权限。"
        case .noWritableCalendar: "没有可写入的系统日历。"
        case .invalidDate: "行程中包含无法识别的日期。"
        case .duplicateIdempotencyMarker: "日历中存在重复的任务事件，无法安全确认结果。"
        case .conflictingExistingEvent: "日历中已存在同一任务的事件，但内容与本次行程不一致。"
        case .verificationFailed: "日历事件写入后无法回读验证。"
        }
    }
}
