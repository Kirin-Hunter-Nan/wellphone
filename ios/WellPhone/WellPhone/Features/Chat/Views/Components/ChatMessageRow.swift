import SwiftUI

struct ChatMessageRow: View {
    @Environment(TaskController.self) private var taskController
    @Bindable var message: ChatMessage
    let retry: () -> Void

    var body: some View {
        if let taskID = message.relatedTaskID,
           let task = taskController.task(id: taskID) {
            if task.status == .completed,
               task.resultReportState == .delivered {
                EmptyView()
            } else {
                AgentTaskCard(task: task)
            }
        } else {
            MessageBubble(message: message, retry: retry)
        }
    }
}
