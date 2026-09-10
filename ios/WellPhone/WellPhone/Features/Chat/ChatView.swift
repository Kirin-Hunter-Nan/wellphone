import SwiftUI

struct ChatView: View {
    @Environment(ConversationController.self) private var controller
    @FocusState private var isComposerFocused: Bool

    var body: some View {
        @Bindable var controller = controller

        NavigationStack {
            VStack(spacing: 0) {
                conversation
                Divider()
                composer(draft: $controller.draft)
            }
            .navigationTitle("WellPhone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Label("Qwen", systemImage: "sparkles")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button("新对话", systemImage: "square.and.pencil") {
                        controller.startNewConversation()
                        isComposerFocused = true
                    }
                    .disabled(controller.isGenerating)
                }
            }
        }
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 16) {
                    if controller.messages.isEmpty {
                        EmptyConversationView()
                            .padding(.top, 72)
                    }

                    ForEach(controller.messages) { message in
                        MessageBubble(message: message) {
                            controller.retryLastResponse()
                        }
                        .id(message.id)
                    }

                    if let errorMessage = controller.errorMessage {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(.red)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 20)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: controller.messages.last?.text) {
                guard let lastMessageID = controller.messages.last?.id else { return }
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(lastMessageID, anchor: .bottom)
                }
            }
        }
    }

    private func composer(draft: Binding<String>) -> some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("给 WellPhone 发消息", text: draft, axis: .vertical)
                .lineLimit(1...6)
                .textFieldStyle(.plain)
                .focused($isComposerFocused)
                .submitLabel(.send)
                .onSubmit {
                    controller.sendDraft()
                }

            if controller.isGenerating {
                Button {
                    controller.stopGenerating()
                } label: {
                    Image(systemName: "stop.fill")
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.circle)
                .accessibilityLabel("停止生成")
            } else {
                Button {
                    controller.sendDraft()
                } label: {
                    Image(systemName: "arrow.up")
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.circle)
                .disabled(controller.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("发送")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(.bar)
    }
}

private struct EmptyConversationView: View {
    var body: some View {
        ContentUnavailableView {
            Label("开始一段对话", systemImage: "bubble.left.and.bubble.right")
        } description: {
            Text("消息会保存在这台设备上。你可以随时停止生成或重试回复。")
        }
    }
}

private struct MessageBubble: View {
    let message: ChatMessage
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .bottom) {
            if message.role == .user {
                Spacer(minLength: 48)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                Text(message.text.isEmpty ? " " : message.text)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .foregroundStyle(message.role == .user ? Color.white : Color.primary)
                    .background(
                        message.role == .user ? Color.accentColor : Color.secondary.opacity(0.12),
                        in: RoundedRectangle(cornerRadius: 18, style: .continuous)
                    )
                    .overlay(alignment: .leading) {
                        if message.deliveryState == .streaming && message.text.isEmpty {
                            ProgressView()
                                .controlSize(.small)
                                .padding(.horizontal, 14)
                        }
                    }

                if message.role == .assistant,
                   [.failed, .stopped].contains(message.deliveryState) {
                    Button(message.deliveryState == .stopped ? "已停止 · 重试" : "重试") {
                        retry()
                    }
                    .font(.caption)
                }
            }

            if message.role == .assistant {
                Spacer(minLength: 48)
            }
        }
        .frame(maxWidth: .infinity)
    }
}
