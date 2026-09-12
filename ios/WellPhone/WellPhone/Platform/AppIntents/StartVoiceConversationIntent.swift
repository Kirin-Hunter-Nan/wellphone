import AppIntents

struct StartVoiceConversationIntent: AppIntent {
    static let title: LocalizedStringResource = "开始语音对话"
    static let description = IntentDescription("打开 WellPhone 并立即开始接收语音指令。")
    static let supportedModes: IntentModes = .foreground(.immediate)
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed

    @MainActor
    func perform() async throws -> some IntentResult {
        VoiceActivationStore.shared.requestActivation()
        return .result()
    }
}

struct WellPhoneAppShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: StartVoiceConversationIntent(),
            phrases: [
                "与 \(.applicationName) 语音对话",
                "打开 \(.applicationName) 语音助手",
                "让 \(.applicationName) 听我说",
            ],
            shortTitle: "开始语音对话",
            systemImageName: "waveform.circle.fill"
        )
    }

    static let shortcutTileColor: ShortcutTileColor = .blue
}
