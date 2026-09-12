import SwiftUI

struct TaskDetailView: View {
    @Environment(TaskController.self) private var controller
    let task: AgentTask

    var body: some View {
        List {
            requestSection
            statusSection
            contentSection
            capabilitySection
            TaskProgressSection(steps: controller.steps(for: task))
            resultSection
            TaskArtifactsSection(task: task, artifacts: controller.artifacts(for: task))
            errorSection
            recoverySection
            executionControlSection
            confirmationSection
            reportingSection
        }
        .navigationTitle(task.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private var requestSection: some View {
        if let sourceMessage = controller.sourceMessage(for: task) {
            Section("原始请求") {
                Text(sourceMessage.text)
                    .textSelection(.enabled)
            }
        }
    }

    private var statusSection: some View {
        Section("状态") {
            LabeledContent("任务状态", value: task.status.title)
            LabeledContent("当前阶段", value: task.phase.title)
            LabeledContent("更新时间") {
                Text(task.updatedAt, format: .dateTime)
            }
            ProgressView(value: task.displayedProgress) {
                Text("总进度")
            } currentValueLabel: {
                Text(task.displayedProgress, format: .percent)
            }
        }
    }

    private var contentSection: some View {
        Section("任务内容") {
            LabeledContent("标题", value: task.title)
            if let scheduledAt = task.scheduledAt {
                LabeledContent("提醒时间") {
                    Text(
                        scheduledAt,
                        format: .dateTime.year().month().day().weekday().hour().minute()
                    )
                }
            }
            if task.executionLocation == .device {
                LabeledContent("提醒列表", value: task.targetName ?? "系统默认列表")
            }
            if let detail = task.detail {
                LabeledContent("备注") {
                    Text(detail).multilineTextAlignment(.trailing)
                }
            }
        }
    }

    @ViewBuilder
    private var capabilitySection: some View {
        if let capability = task.capability {
            Section("能力") {
                Text(capability).fontDesign(.monospaced)
            }
        }
    }

    @ViewBuilder
    private var resultSection: some View {
        if let resultSummary = task.resultSummary {
            Section("结果") { Text(resultSummary) }
        }
    }

    @ViewBuilder
    private var errorSection: some View {
        if let errorMessage = task.errorMessage {
            Section("错误") {
                Text(errorMessage).foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder
    private var recoverySection: some View {
        if task.executionAttemptCount > 0 {
            Section("执行恢复") {
                LabeledContent("执行尝试", value: "\(task.executionAttemptCount) 次")
                if let retryAt = task.nextExecutionRetryAt {
                    LabeledContent(
                        "下次重试",
                        value: retryAt.formatted(date: .omitted, time: .standard)
                    )
                }
                if let deadlineAt = task.executionDeadlineAt {
                    LabeledContent(
                        "本次截止",
                        value: deadlineAt.formatted(date: .omitted, time: .standard)
                    )
                }
                if let lastError = task.lastExecutionErrorMessage {
                    Text("上一次失败：\(lastError)")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private var executionControlSection: some View {
        if task.status == .running, task.phase == .executing {
            Section("执行控制") {
                if task.cancellationRequestedAt == nil {
                    Text("停止会阻止尚未开始的重试；已经交给系统的写入仍会回读确认。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Button("安全停止任务", role: .destructive) {
                        Task { await controller.cancelTask(taskID: task.id) }
                    }
                } else {
                    ProgressView("正在安全停止…")
                }
            }
        }
    }

    @ViewBuilder
    private var confirmationSection: some View {
        if task.status == .waitingForConfirmation {
            Section("需要确认") {
                Text(
                    task.executionLocation == .server
                        ? "确认后任务会在服务端持续运行，你可以离开当前页面。"
                        : "确认后才会请求系统权限并写入提醒事项。"
                )
                .font(.footnote)
                .foregroundStyle(.secondary)

                Button(task.executionLocation == .server ? "确认开始任务" : "确认创建提醒") {
                    Task { await controller.confirmTask(taskID: task.id) }
                }
                Button("取消任务", role: .destructive) {
                    Task { await controller.cancelTask(taskID: task.id) }
                }
            }
        }
    }

    @ViewBuilder
    private var reportingSection: some View {
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
}
