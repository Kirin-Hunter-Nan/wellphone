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
    @State private var taskController: TaskController

    init() {
        let schema = Schema([
            Conversation.self,
            ChatMessage.self,
            AgentTask.self,
            AgentTaskStep.self,
        ])
        let isRunningForPreview = ProcessInfo.processInfo.environment["XCODE_RUNNING_FOR_PREVIEWS"] == "1"
        let modelConfiguration = ModelConfiguration(
            schema: schema,
            isStoredInMemoryOnly: isRunningForPreview
        )

        do {
            let container = try ModelContainer(for: schema, configurations: [modelConfiguration])
            let taskController = TaskController(modelContext: container.mainContext)
            sharedModelContainer = container
            _taskController = State(initialValue: taskController)
            _conversationController = State(
                initialValue: ConversationController(
                    modelContext: container.mainContext,
                    gateway: ModelGatewayFactory.make(),
                    taskController: taskController
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
                .environment(taskController)
        }
        .modelContainer(sharedModelContainer)
    }
}
