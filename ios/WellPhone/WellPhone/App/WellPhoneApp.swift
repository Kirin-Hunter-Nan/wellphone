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
    @Environment(\.scenePhase) private var scenePhase
    private let sharedModelContainer: ModelContainer
    @State private var conversationController: ConversationController
    @State private var taskController: TaskController

    init() {
        let schema = Schema([
            Conversation.self,
            ChatMessage.self,
            ChatAttachment.self,
            AgentTask.self,
            AgentTaskStep.self,
            TaskArtifact.self,
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
                .task {
                    await taskController.recoverInterruptedTasks()
                    await taskController.flushPendingCheckpoints()
                    await taskController.flushPendingResultReports()
                }
                .onChange(of: scenePhase) { _, newPhase in
                    guard newPhase == .active else { return }
                    Task {
                        await taskController.recoverInterruptedTasks()
                        await taskController.flushPendingCheckpoints()
                        await taskController.flushPendingResultReports()
                    }
                }
        }
        .modelContainer(sharedModelContainer)
    }
}
