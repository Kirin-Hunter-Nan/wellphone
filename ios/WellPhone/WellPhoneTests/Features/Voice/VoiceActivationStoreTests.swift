import Foundation
import Testing
@testable import WellPhone

struct VoiceActivationStoreTests {
    @Test @MainActor
    func pendingActivationSurvivesAColdLaunchUntilPresented() throws {
        let suiteName = "VoiceActivationStoreTests.cold-launch.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let requestDate = Date(timeIntervalSince1970: 1_800_000_000)

        let firstProcess = VoiceActivationStore(
            defaults: defaults,
            now: { requestDate }
        )
        let requestID = firstProcess.requestActivation()

        let relaunchedProcess = VoiceActivationStore(
            defaults: defaults,
            now: { requestDate.addingTimeInterval(10) }
        )
        #expect(relaunchedProcess.activationID == requestID)
        #expect(relaunchedProcess.isVoiceConversationPresented)

        relaunchedProcess.markPresented()
        #expect(relaunchedProcess.isVoiceConversationPresented)

        let nextLaunch = VoiceActivationStore(
            defaults: defaults,
            now: { requestDate.addingTimeInterval(20) }
        )
        #expect(nextLaunch.activationID == requestID)

        relaunchedProcess.dismissActivation()
        let launchAfterDismissal = VoiceActivationStore(
            defaults: defaults,
            now: { requestDate.addingTimeInterval(30) }
        )
        #expect(!launchAfterDismissal.isVoiceConversationPresented)
    }

    @Test @MainActor
    func expiredActivationIsDiscarded() throws {
        let suiteName = "VoiceActivationStoreTests.expired.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let requestDate = Date(timeIntervalSince1970: 1_800_000_000)

        VoiceActivationStore(
            defaults: defaults,
            now: { requestDate }
        ).requestActivation()

        let relaunchedProcess = VoiceActivationStore(
            defaults: defaults,
            now: { requestDate.addingTimeInterval(5 * 60 + 1) }
        )
        #expect(!relaunchedProcess.isVoiceConversationPresented)
    }

    @Test @MainActor
    func dismissalClearsTheActiveRequest() throws {
        let suiteName = "VoiceActivationStoreTests.dismissal.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = VoiceActivationStore(defaults: defaults)

        store.requestActivation()
        store.dismissActivation()

        #expect(store.activationID == nil)
        #expect(!store.isVoiceConversationPresented)
    }
}
