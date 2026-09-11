import SwiftUI

struct AppSidebar: View {
    @Environment(ConversationController.self) private var conversationController
    @Environment(TaskController.self) private var taskController

    let openTasks: () -> Void
    let startNewConversation: () -> Void
    let selectConversation: (Conversation) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("WellPhone")
                .font(.title2.bold())
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 14)

            Button(action: openTasks) {
                SidebarActionLabel(
                    title: "任务",
                    systemImage: "checklist",
                    badge: taskController.activeTasks.count
                )
            }
            .buttonStyle(.plain)

            Button(action: startNewConversation) {
                SidebarActionLabel(
                    title: "新聊天",
                    systemImage: "square.and.pencil"
                )
            }
            .buttonStyle(.plain)
            .disabled(conversationController.isGenerating)

            Divider()
                .padding(.vertical, 12)

            Text("历史会话")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 20)
                .padding(.bottom, 6)

            if conversationController.conversations.isEmpty {
                Text("还没有历史会话")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 20)
                    .padding(.top, 10)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 3) {
                        ForEach(conversationController.conversations) { conversation in
                            Button {
                                selectConversation(conversation)
                            } label: {
                                ConversationRow(
                                    conversation: conversation,
                                    isSelected: conversation.id == conversationController.activeConversationID
                                )
                            }
                            .buttonStyle(.plain)
                            .disabled(conversationController.isGenerating)
                        }
                    }
                    .padding(.horizontal, 10)
                }
            }

            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                Text("WellPhone AI")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(20)
        }
        .frame(maxHeight: .infinity)
        .background(.regularMaterial)
        .accessibilityIdentifier("chat.sidebar")
    }
}

private struct SidebarActionLabel: View {
    let title: String
    let systemImage: String
    var badge: Int = 0

    var body: some View {
        HStack(spacing: 13) {
            Image(systemName: systemImage)
                .frame(width: 24)
            Text(title)
                .font(.body.weight(.medium))
            Spacer()
            if badge > 0 {
                Text(badge, format: .number)
                    .font(.caption.bold())
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Color.accentColor, in: Capsule())
            }
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .contentShape(Rectangle())
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }
}

private struct ConversationRow: View {
    let conversation: Conversation
    let isSelected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(conversation.title)
                .font(.subheadline.weight(isSelected ? .semibold : .regular))
                .lineLimit(1)
            Text(conversation.updatedAt, format: .relative(presentation: .named))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 9)
        .background(
            isSelected ? Color.accentColor.opacity(0.12) : Color.clear,
            in: RoundedRectangle(cornerRadius: 10, style: .continuous)
        )
        .contentShape(Rectangle())
    }
}
