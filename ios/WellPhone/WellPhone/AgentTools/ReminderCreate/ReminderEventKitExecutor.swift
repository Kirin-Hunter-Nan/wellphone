import CryptoKit
import EventKit
import Foundation

@MainActor
protocol ReminderCreating: AnyObject {
    func create(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder
    func recover(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder?
}

@MainActor
protocol ReminderVerifying: AnyObject {
    func verify(_ created: CreatedReminder, matches draft: ReminderDraft) throws -> VerifiedReminder
}

@MainActor
protocol ReminderExecuting: ReminderCreating, ReminderVerifying {}

@MainActor
final class ReminderEventKitExecutor: ReminderExecuting {
    private let eventStore = EKEventStore()

    func create(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder {
        try await ensureAccess()
        if let existing = await recoveredReminder(idempotencyKey: idempotencyKey) {
            return existing
        }

        let calendar = try resolveCalendar(for: draft)

        let reminder = EKReminder(eventStore: eventStore)
        reminder.title = draft.title
        reminder.notes = draft.notes
        reminder.url = idempotencyURL(for: idempotencyKey)
        reminder.calendar = calendar
        reminder.dueDateComponents = Calendar.current.dateComponents(
            in: TimeZone.current,
            from: draft.dueAt
        )
        reminder.addAlarm(EKAlarm(absoluteDate: draft.dueAt))
        try eventStore.save(reminder, commit: true)

        let identifier = reminder.calendarItemIdentifier
        guard !identifier.isEmpty else { throw ReminderToolError.missingIdentifier }
        return CreatedReminder(identifier: identifier, listTitle: calendar.title)
    }

    func recover(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder? {
        _ = draft
        try await ensureAccess()
        return await recoveredReminder(idempotencyKey: idempotencyKey)
    }

    func verify(_ created: CreatedReminder, matches draft: ReminderDraft) throws -> VerifiedReminder {
        guard let reminder = eventStore.calendarItem(withIdentifier: created.identifier) as? EKReminder,
              reminder.title == draft.title,
              reminder.calendar.title == created.listTitle,
              let readBackDueAt = reminder.dueDateComponents.flatMap({ Calendar.current.date(from: $0) }),
              abs(readBackDueAt.timeIntervalSince(draft.dueAt)) < 60 else {
            throw ReminderToolError.verificationFailed
        }
        let digest = SHA256.hash(data: Data(created.identifier.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return VerifiedReminder(
            listTitle: created.listTitle,
            identifierDigest: String(digest.prefix(12))
        )
    }

    private func ensureAccess() async throws {
        let granted = try await eventStore.requestFullAccessToReminders()
        guard granted else { throw ReminderToolError.accessDenied }
    }

    private func resolveCalendar(for draft: ReminderDraft) throws -> EKCalendar {
        if let listName = draft.listName {
            guard let selected = eventStore.calendars(for: .reminder).first(where: {
                $0.title.compare(
                    listName,
                    options: [.caseInsensitive, .diacriticInsensitive]
                ) == .orderedSame
            }) else {
                throw ReminderToolError.listNotFound(listName)
            }
            return selected
        }
        guard let defaultCalendar = eventStore.defaultCalendarForNewReminders() else {
            throw ReminderToolError.noDefaultList
        }
        return defaultCalendar
    }

    private func recoveredReminder(idempotencyKey: String) async -> CreatedReminder? {
        let markerURL = idempotencyURL(for: idempotencyKey)
        let predicate = eventStore.predicateForReminders(in: nil)
        let identity: (identifier: String, listTitle: String)? = await withCheckedContinuation { continuation in
            // EventKit invokes this callback on its own search queue, so it must not inherit MainActor isolation.
            let completion: @Sendable ([EKReminder]?) -> Void = { reminders in
                guard let existing = reminders?.first(where: { reminder in
                    reminder.url == markerURL
                }) else {
                    continuation.resume(returning: nil)
                    return
                }
                continuation.resume(returning: (
                    identifier: existing.calendarItemIdentifier,
                    listTitle: existing.calendar.title
                ))
            }
            eventStore.fetchReminders(matching: predicate, completion: completion)
        }
        guard let identity, !identity.identifier.isEmpty else { return nil }
        return CreatedReminder(
            identifier: identity.identifier,
            listTitle: identity.listTitle
        )
    }

    private func idempotencyURL(for idempotencyKey: String) -> URL {
        var components = URLComponents()
        components.scheme = "wellphone"
        components.host = "tool"
        components.path = "/reminder.create/\(idempotencyKey)"
        return components.url!
    }
}
