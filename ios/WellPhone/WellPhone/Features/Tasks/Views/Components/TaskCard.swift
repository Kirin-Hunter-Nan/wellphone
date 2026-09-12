import SwiftUI

struct TaskCard: View {
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
                    .foregroundStyle(task.status.tint)
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

            ProgressView(value: task.displayedProgress)
                .tint(task.status.tint)

            HStack {
                Text(task.progressLabel)
                Spacer()
                Text(task.updatedAt, format: .relative(presentation: .named))
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding(14)
        .background(
            Color.secondary.opacity(0.1),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}
