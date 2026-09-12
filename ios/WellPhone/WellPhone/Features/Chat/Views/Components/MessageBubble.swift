import SwiftUI

struct MessageBubble: View {
    @Environment(ConversationController.self) private var controller
    @Bindable var message: ChatMessage
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .bottom) {
            if message.role == .user {
                Spacer(minLength: 48)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                let attachments = controller.attachments(for: message)
                if !attachments.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(attachments) { attachment in
                                if let path = attachment.localPath,
                                   let image = UIImage(contentsOfFile: path) {
                                    Image(uiImage: image)
                                        .resizable()
                                        .scaledToFill()
                                        .frame(width: 180, height: 150)
                                        .clipShape(RoundedRectangle(cornerRadius: 16))
                                }
                            }
                        }
                    }
                }
                if !message.text.isEmpty || attachments.isEmpty {
                    renderedText
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

    private var renderedText: Text {
        let content = message.text.isEmpty ? " " : message.text
        guard message.role == .assistant,
              let attributed = try? AttributedString(
                  markdown: content,
                  options: .init(interpretedSyntax: .full)
              ) else {
            return Text(content)
        }
        return Text(attributed)
    }
}
