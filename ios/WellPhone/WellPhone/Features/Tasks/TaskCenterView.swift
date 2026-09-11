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
                    .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                }
            }
        }
    }
}

private struct TaskCard: View {
    let task: AgentTask

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(task.title)
                    .font(.headline)
                    .lineLimit(2)
                Spacer(minLength: 12)
                Text(task.phase.title)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(statusColor)
            }

            if let scheduledAt = task.scheduledAt {
                Label {
                    Text(scheduledAt, format: .dateTime.month().day().weekday().hour().minute())
                } icon: {
                    Image(systemName: "calendar.badge.clock")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            ProgressView(value: displayedProgress)
                .tint(statusColor)

            HStack {
                Text(progressLabel)
                Spacer()
                Text(task.updatedAt, format: .relative(presentation: .named))
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding(14)
        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var displayedProgress: Double {
        task.status == .completed ? 1 : task.progress ?? 0
    }

    private var progressLabel: String {
        switch task.status {
        case .cancelled: "已取消"
        case .failed: "执行失败"
        default: "进度 \(Int(displayedProgress * 100))%"
        }
    }

    private var statusColor: Color {
        switch task.status {
        case .created, .running: .accentColor
        case .waitingForConfirmation: .orange
        case .completed: .green
        case .failed: .red
        case .cancelled: .secondary
        }
    }
}

private struct TaskDetailView: View {
    @Environment(TaskController.self) private var controller
    let task: AgentTask

    var body: some View {
        List {
            if let sourceMessage = controller.sourceMessage(for: task) {
                Section("原始请求") {
                    Text(sourceMessage.text)
                        .textSelection(.enabled)
                }
            }

            Section("状态") {
                LabeledContent("任务状态", value: statusTitle)
                LabeledContent("当前阶段", value: task.phase.title)
                LabeledContent("更新时间") {
                    Text(task.updatedAt, format: .dateTime)
                }
                ProgressView(value: displayedProgress) {
                    Text("总进度")
                } currentValueLabel: {
                    Text(displayedProgress, format: .percent)
                }
            }

            Section("任务内容") {
                LabeledContent("标题", value: task.title)
                if let scheduledAt = task.scheduledAt {
                    LabeledContent("提醒时间") {
                        Text(scheduledAt, format: .dateTime.year().month().day().weekday().hour().minute())
                    }
                }
                LabeledContent("提醒列表", value: task.targetName ?? "系统默认列表")
                if let detail = task.detail {
                    LabeledContent("备注") {
                        Text(detail)
                            .multilineTextAlignment(.trailing)
                    }
                }
            }

            if let capability = task.capability {
                Section("能力") {
                    Text(capability)
                        .fontDesign(.monospaced)
                }
            }

            let steps = controller.steps(for: task)
            if !steps.isEmpty {
                Section("执行进度") {
                    ForEach(steps) { step in
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: icon(for: step.status))
                                .foregroundStyle(color(for: step.status))
                            VStack(alignment: .leading, spacing: 3) {
                                Text(step.title)
                                if let completedAt = step.completedAt {
                                    Text(completedAt, format: .dateTime.hour().minute().second())
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                } else if let startedAt = step.startedAt {
                                    Text("开始于 \(startedAt.formatted(date: .omitted, time: .standard))")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                        }
                    }
                }
            }

            if let resultSummary = task.resultSummary {
                Section("结果") { Text(resultSummary) }
            }

            if let errorMessage = task.errorMessage {
                Section("错误") {
                    Text(errorMessage)
                        .foregroundStyle(.red)
                }
            }

            if task.status == .waitingForConfirmation {
                Section("需要确认") {
                    Text("确认后才会请求系统权限并写入提醒事项。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)

                    Button("确认创建提醒") {
                        Task { await controller.confirmTask(taskID: task.id) }
                    }

                    Button("取消任务", role: .destructive) {
                        Task { await controller.cancelTask(taskID: task.id) }
                    }
                }
            }

            if let reportState = task.resultReportState {
                Section("服务同步") {
                    LabeledContent("Tool 结果", value: reportState.title)
                    if let reportError = task.resultReportError {
                        Text(reportError)
                            .font(.footnote)
                            .foregroundStyle(reportState == .conflict ? .red : .secondary)
                    }
                    if reportState == .pending {
                        Button("重新同步") {
                            Task { await controller.retryResultReport(taskID: task.id) }
                        }
                    }
                }
            }
        }
        .navigationTitle(task.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var displayedProgress: Double {
        task.status == .completed ? 1 : task.progress ?? 0
    }

    private var statusTitle: String {
        switch task.status {
        case .created: "已创建"
        case .running: "运行中"
        case .waitingForConfirmation: "等待确认"
        case .completed: "已完成"
        case .failed: "失败"
        case .cancelled: "已取消"
        }
    }

    private func icon(for status: AgentTaskStepStatus) -> String {
        switch status {
        case .pending: "circle"
        case .running: "arrow.trianglehead.2.clockwise.rotate.90"
        case .completed: "checkmark.circle.fill"
        case .failed: "exclamationmark.circle.fill"
        }
    }

    private func color(for status: AgentTaskStepStatus) -> Color {
        switch status {
        case .pending: .secondary
        case .running: .accentColor
        case .completed: .green
        case .failed: .red
        }
    }
}

private extension ToolResultReportState {
    var title: String {
        switch self {
        case .pending: "等待同步"
        case .delivered: "已同步"
        case .conflict: "结果冲突"
        }
    }
}
