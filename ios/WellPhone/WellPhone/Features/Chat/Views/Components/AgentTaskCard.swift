import SwiftUI

struct AgentTaskCard: View {
    @Environment(TaskController.self) private var controller
    @Bindable var task: AgentTask

    var body: some View {
        if task.status == .completed {
            completedCard
        } else {
            activeCard
        }
    }

    private var completedCard: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
            Text(task.title)
                .font(.subheadline.weight(.medium))
                .lineLimit(1)
            Spacer(minLength: 8)
            Text("已完成")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(Color.green.opacity(0.09), in: RoundedRectangle(cornerRadius: 14))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.trailing, 92)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("任务已完成：\(task.title)")
    }

    private var activeCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: statusIcon)
                    .foregroundStyle(statusColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text(task.executionLocation == .server ? "后台任务" : "提醒事项")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(task.title)
                        .font(.headline)
                }
                Spacer()
            }

            if let scheduledAt = task.scheduledAt {
                Label {
                    Text(scheduledAt, format: .dateTime.year().month().day().weekday().hour().minute())
                } icon: {
                    Image(systemName: "calendar.badge.clock")
                }
                .font(.subheadline)
            }

            if let detail = task.detail {
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            if task.status == .waitingForConfirmation {
                Text(task.executionLocation == .server
                     ? "确认后任务会在服务端持续运行。"
                     : "确认后才会请求系统权限并写入提醒事项。")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack {
                    Button("取消", role: .cancel) {
                        Task { await controller.cancelTask(taskID: task.id) }
                    }
                    .buttonStyle(.bordered)

                    Spacer()

                    Button(task.executionLocation == .server ? "确认开始" : "确认创建") {
                        Task { await controller.confirmTask(taskID: task.id) }
                    }
                    .buttonStyle(.borderedProminent)
                }
            } else if task.status.isActive {
                ProgressView(value: task.displayedProgress) {
                    Text(task.phase.title)
                } currentValueLabel: {
                    Text(task.progressLabel)
                }
                .font(.subheadline)
                if task.phase == .executing {
                    if task.cancellationRequestedAt == nil {
                        Button("安全停止", role: .destructive) {
                            Task { await controller.cancelTask(taskID: task.id) }
                        }
                        .buttonStyle(.bordered)
                    } else {
                        Text(task.executionLocation == .server
                             ? "正在停止后台任务。"
                             : "正在安全停止，不会开始新的系统写入。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } else if let result = task.resultSummary {
                Text(result)
                    .font(.footnote)
                    .foregroundStyle(task.status == .completed ? .green : .secondary)
            } else if let error = task.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .padding(16)
        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.trailing, 48)
    }

    private var statusIcon: String {
        switch task.status {
        case .waitingForConfirmation: "bell.badge"
        case .created, .running: "clock.arrow.trianglehead.counterclockwise.rotate.90"
        case .completed: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        case .cancelled: "xmark.circle"
        }
    }

    private var statusColor: Color {
        switch task.status {
        case .waitingForConfirmation: .orange
        case .created, .running: .accentColor
        case .completed: .green
        case .failed: .red
        case .cancelled: .secondary
        }
    }
}
