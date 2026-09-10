import Foundation
@preconcurrency import UserNotifications

enum AgentTaskNotificationKind: String, Sendable {
    case authorizationRequired
    case completed
}

struct AgentTaskNotification: Equatable, Sendable {
    let taskID: UUID
    let kind: AgentTaskNotificationKind
    let title: String
    let body: String
}

@MainActor
protocol TaskNotifying: AnyObject {
    func post(_ notification: AgentTaskNotification) async
}

extension Notification.Name {
    static let agentTaskNotificationOpened = Notification.Name("agentTaskNotificationOpened")
}

@MainActor
final class LocalTaskNotificationCenter: NSObject, TaskNotifying, UNUserNotificationCenterDelegate {
    static let shared = LocalTaskNotificationCenter()

    private let center: UNUserNotificationCenter

    private override init() {
        center = .current()
        super.init()
        center.delegate = self
    }

    func post(_ notification: AgentTaskNotification) async {
        await deliver(notification)
    }

    private func deliver(_ notification: AgentTaskNotification) async {
        do {
            guard try await notificationsAreAuthorized() else { return }

            let content = UNMutableNotificationContent()
            content.title = notification.title
            content.body = notification.body
            content.sound = .default
            content.userInfo = ["taskID": notification.taskID.uuidString]

            let request = UNNotificationRequest(
                identifier: "agent-task.\(notification.taskID.uuidString).\(notification.kind.rawValue)",
                content: content,
                trigger: nil
            )
            try await center.add(request)
        } catch {
            // Notifications are an optional delivery channel. The persisted task remains
            // available in the task center if authorization or scheduling fails.
        }
    }

    private func notificationsAreAuthorized() async throws -> Bool {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return true
        case .notDetermined:
            return try await requestAuthorizationOnMainThread()
        case .denied:
            return false
        @unknown default:
            return false
        }
    }

    private func requestAuthorizationOnMainThread() async throws -> Bool {
        try await withCheckedThrowingContinuation { continuation in
            // The first authorization request presents system UI. Deferring it to the
            // main queue avoids presenting while SwiftUI is committing a keyboard or
            // view transaction, even when this method was reached through an async hop.
            DispatchQueue.main.async { [center] in
                center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else {
                        continuation.resume(returning: granted)
                    }
                }
            }
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping @Sendable (UNNotificationPresentationOptions) -> Void
    ) {
        DispatchQueue.main.async {
            completionHandler([.banner, .list, .sound])
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping @Sendable () -> Void
    ) {
        let taskID = response.notification.request.content.userInfo["taskID"] as? String

        DispatchQueue.main.async {
            // Finish UIKit's notification-response transaction on the main thread first.
            completionHandler()

            guard let taskID else { return }
            // Navigation must wait until the app activation transaction has committed.
            DispatchQueue.main.async {
                NotificationCenter.default.post(
                    name: .agentTaskNotificationOpened,
                    object: nil,
                    userInfo: ["taskID": taskID]
                )
            }
        }
    }
}

@MainActor
final class DisabledTaskNotifier: TaskNotifying {
    func post(_ notification: AgentTaskNotification) async {}
}
