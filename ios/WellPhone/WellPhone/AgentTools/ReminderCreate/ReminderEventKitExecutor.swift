import CryptoKit
import EventKit
import Foundation

@MainActor
protocol ReminderCreating: AnyObject {
    func create(_ draft: ReminderDraft) async throws -> CreatedReminder
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

    func create(_ draft: ReminderDraft) async throws -> CreatedReminder {
        let granted = try await eventStore.requestFullAccessToReminders()
        guard granted else { throw ReminderToolError.accessDenied }

        let calendar: EKCalendar
        if let listName = draft.listName {
            guard let selected = eventStore.calendars(for: .reminder).first(where: {
                $0.title.compare(listName, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame
            }) else {
                throw ReminderToolError.listNotFound(listName)
            }
            calendar = selected
        } else {
            guard let defaultCalendar = eventStore.defaultCalendarForNewReminders() else {
                throw ReminderToolError.noDefaultList
            }
            calendar = defaultCalendar
        }

        let reminder = EKReminder(eventStore: eventStore)
        reminder.title = draft.title
        reminder.notes = draft.notes
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
}
