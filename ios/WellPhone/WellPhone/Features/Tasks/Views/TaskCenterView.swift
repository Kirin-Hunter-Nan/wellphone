import SwiftUI

struct TaskCenterView: View {
    @Environment(TaskController.self) private var controller

    var body: some View {
        List {
            taskSection("进行中", tasks: controller.activeTasks)
            taskSection("已完成", tasks: controller.completedTasks)
            taskSection("其他", tasks: controller.inactiveTasks)
        }
        .listStyle(.insetGrouped)
        .overlay {
            if controller.tasks.isEmpty {
                ContentUnavailableView(
                    "暂无任务",
                    systemImage: "checklist",
                    description: Text("通过聊天创建的 Agent 任务会显示在这里。")
                )
            }
        }
        .navigationTitle("任务")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { controller.refresh() }
    }

    @ViewBuilder
    private func taskSection(_ title: String, tasks: [AgentTask]) -> some View {
        if !tasks.isEmpty {
            Section(title) {
                ForEach(tasks) { task in
                    NavigationLink {
                        TaskDetailView(task: task)
                    } label: {
                        TaskCard(task: task)
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(
                        EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16)
                    )
                }
            }
        }
    }
}
