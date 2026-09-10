//
//  WellPhoneApp.swift
//  WellPhone
//
//  Created by 南佳琪 on 2026/9/10.
//

import SwiftUI
import SwiftData

@main
struct WellPhoneApp: App {
    private let sharedModelContainer: ModelContainer
    @State private var conversationController: ConversationController

    init() {
        let schema = Schema([
            Conversation.self,
            ChatMessage.self,
        ])
        let isRunningForPreview = ProcessInfo.processInfo.environment["XCODE_RUNNING_FOR_PREVIEWS"] == "1"
        let modelConfiguration = ModelConfiguration(
            schema: schema,
            isStoredInMemoryOnly: isRunningForPreview
        )

        do {
            let container = try ModelContainer(for: schema, configurations: [modelConfiguration])
            sharedModelContainer = container
            _conversationController = State(
                initialValue: ConversationController(
                    modelContext: container.mainContext,
                    gateway: ModelGatewayFactory.make()
                )
            )
        } catch {
            fatalError("Could not create ModelContainer: \(error)")
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(conversationController)
        }
        .modelContainer(sharedModelContainer)
    }
}
