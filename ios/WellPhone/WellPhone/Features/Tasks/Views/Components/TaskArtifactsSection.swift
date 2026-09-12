import SwiftUI

struct TaskArtifactsSection: View {
    @Environment(TaskController.self) private var controller
    let task: AgentTask
    let artifacts: [TaskArtifact]

    var body: some View {
        if !artifacts.isEmpty {
            Section("任务产物") {
                ForEach(artifacts) { artifact in
                    VStack(alignment: .leading, spacing: 6) {
                        Label(artifact.title, systemImage: artifact.kind.icon)
                            .font(.headline)
                        artifactContent(artifact)
                        artifactLink(artifact)
                        calendarAction(artifact)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    @ViewBuilder
    private func artifactContent(_ artifact: TaskArtifact) -> some View {
        if let text = artifact.textPayload, artifact.kind == .text {
            MarkdownArtifactText(source: text)
        } else {
            Text(artifact.contentType)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func artifactLink(_ artifact: TaskArtifact) -> some View {
        if let reference = artifact.storageReference,
           !reference.hasPrefix("eventkit:"),
           let url = URL(string: reference) {
            Link("打开产物", destination: url)
        }
    }

    @ViewBuilder
    private func calendarAction(_ artifact: TaskArtifact) -> some View {
        if artifact.contentType == "application/vnd.wellphone.calendar-events+json" {
            if artifact.storageReference?.hasPrefix("eventkit:") == true {
                Label("已添加到 Apple 日历", systemImage: "checkmark.circle.fill")
                    .font(.footnote)
                    .foregroundStyle(.green)
            } else {
                Button("添加到 Apple 日历") {
                    Task { await controller.importTravelCalendar(taskID: task.id) }
                }
            }
        }
    }
}

@MainActor
private extension TaskArtifact {
    var textPayload: String? {
        guard let payloadJSON,
              let data = payloadJSON.data(using: .utf8),
              let value = try? JSONDecoder().decode(JSONValue.self, from: data),
              case .string(let text) = value else { return nil }
        return text
    }
}

private struct MarkdownArtifactText: View {
    let source: String

    var body: some View {
        Group {
            if let attributed = try? AttributedString(
                markdown: source,
                options: .init(interpretedSyntax: .full)
            ) {
                Text(attributed)
            } else {
                Text(source)
            }
        }
        .font(.footnote)
        .textSelection(.enabled)
        .tint(.accentColor)
    }
}
