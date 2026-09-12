import SwiftUI

struct TaskProgressSection: View {
    let steps: [AgentTaskStep]

    var body: some View {
        if !steps.isEmpty {
            Section("执行进度") {
                ForEach(steps) { step in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: step.status.icon)
                            .foregroundStyle(step.status.tint)
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
    }
}
