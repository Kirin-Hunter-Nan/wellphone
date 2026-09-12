//
//  ContentView.swift
//  WellPhone
//
//  Created by 南佳琪 on 2026/9/10.
//

import SwiftUI
import SwiftData

struct ContentView: View {
    var body: some View {
        ChatView()
    }
}

#Preview {
    PreviewRoot()
}

@MainActor
private struct PreviewRoot: View {
    private let previewContainer: ModelContainer
    @State private var controller: ConversationController
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
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        let container = try! ModelContainer(for: schema, configurations: [configuration])
        let taskController = TaskController(modelContext: container.mainContext)
        previewContainer = container
        _taskController = State(initialValue: taskController)
        _controller = State(
            initialValue: ConversationController(
                modelContext: container.mainContext,
                gateway: DemoModelGateway(),
                taskController: taskController
            )
        )
    }

    var body: some View {
        ContentView()
            .environment(controller)
            .environment(taskController)
            .environment(VoiceActivationStore.shared)
            .modelContainer(previewContainer)
    }
}
