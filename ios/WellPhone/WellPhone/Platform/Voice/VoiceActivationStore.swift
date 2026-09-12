import Foundation
import Observation

@MainActor
@Observable
final class VoiceActivationStore {
    static let shared = VoiceActivationStore()

    private enum Keys {
        static let requestID = "voice.activation.request-id"
        static let requestedAt = "voice.activation.requested-at"
    }

    private static let maximumPendingAge: TimeInterval = 5 * 60

    private(set) var activationID: UUID?

    var isVoiceConversationPresented: Bool {
        activationID != nil
    }

    private let defaults: UserDefaults
    private let now: () -> Date

    init(
        defaults: UserDefaults = .standard,
        now: @escaping () -> Date = Date.init
    ) {
        self.defaults = defaults
        self.now = now
        activationID = Self.pendingActivation(
            in: defaults,
            now: now(),
            maximumAge: Self.maximumPendingAge
        )
        if activationID == nil {
            clearPersistedActivation()
        }
    }

    @discardableResult
    func requestActivation() -> UUID {
        let requestID = UUID()
        defaults.set(requestID.uuidString, forKey: Keys.requestID)
        defaults.set(now(), forKey: Keys.requestedAt)
        activationID = requestID
        return requestID
    }

    func markPresented() {
        // Keep the request persisted while the voice flow is active. SwiftUI can
        // temporarily tear down presentations during a scene transition, and the
        // app may also be terminated while a speech asset is downloading.
    }

    func dismissActivation() {
        activationID = nil
        clearPersistedActivation()
    }

    func recoverPendingActivation() {
        guard activationID == nil else { return }
        activationID = Self.pendingActivation(
            in: defaults,
            now: now(),
            maximumAge: Self.maximumPendingAge
        )
        if activationID == nil {
            clearPersistedActivation()
        }
    }

    private func clearPersistedActivation() {
        defaults.removeObject(forKey: Keys.requestID)
        defaults.removeObject(forKey: Keys.requestedAt)
    }

    private static func pendingActivation(
        in defaults: UserDefaults,
        now: Date,
        maximumAge: TimeInterval
    ) -> UUID? {
        guard let rawRequestID = defaults.string(forKey: Keys.requestID),
              let requestID = UUID(uuidString: rawRequestID),
              let requestedAt = defaults.object(forKey: Keys.requestedAt) as? Date,
              now.timeIntervalSince(requestedAt) >= 0,
              now.timeIntervalSince(requestedAt) <= maximumAge else {
            return nil
        }
        return requestID
    }
}
