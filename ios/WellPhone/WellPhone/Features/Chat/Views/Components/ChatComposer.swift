import PhotosUI
import SwiftUI

struct ChatComposer: View {
    @Environment(ConversationController.self) private var controller
    @FocusState.Binding var isFocused: Bool
    let startVoiceConversation: () -> Void
    @State private var selectedPhotoItems: [PhotosPickerItem] = []

    var body: some View {
        @Bindable var controller = controller

        let canSend = !controller.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !controller.pendingImages.isEmpty

        return VStack(spacing: 6) {
            if !controller.pendingImages.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(controller.pendingImages) { image in
                            ZStack(alignment: .topTrailing) {
                                if let preview = UIImage(data: image.data) {
                                    Image(uiImage: preview)
                                        .resizable()
                                        .scaledToFill()
                                        .frame(width: 72, height: 72)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                                Button {
                                    controller.removePendingImage(id: image.id)
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .symbolRenderingMode(.palette)
                                        .foregroundStyle(.white, .black.opacity(0.65))
                                }
                                .offset(x: 5, y: -5)
                            }
                        }
                    }
                    .padding(.horizontal, 4)
                    .padding(.top, 6)
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                PhotosPicker(
                    selection: $selectedPhotoItems,
                    maxSelectionCount: max(1, 4 - controller.pendingImages.count),
                    matching: .images
                ) {
                    Image(systemName: "photo.on.rectangle")
                        .font(.system(size: 18))
                        .frame(width: 34, height: 36)
                }
                .disabled(controller.isGenerating || controller.pendingImages.count >= 4)
                .accessibilityLabel("选择图片")

                TextField("给 WellPhone 发消息…", text: $controller.draft, axis: .vertical)
                .lineLimit(1...6)
                .textFieldStyle(.plain)
                .accessibilityIdentifier("chat.composer")
                .font(.body)
                .padding(.leading, 10)
                .padding(.vertical, 9)
                .focused($isFocused)
                .submitLabel(.send)
                .onSubmit {
                    guard canSend, !controller.isGenerating else { return }
                    controller.sendDraft()
                }

                Button(action: startVoiceConversation) {
                    Image(systemName: "mic.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Color.accentColor)
                        .frame(width: 36, height: 36)
                        .background(Color.accentColor.opacity(0.12), in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(controller.isGenerating)
                .accessibilityLabel("开始语音输入")
                .accessibilityIdentifier("chat.voice-input")

                Group {
                    if controller.isGenerating {
                    Button {
                        controller.stopGenerating()
                    } label: {
                        Image(systemName: "stop.fill")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white)
                            .frame(width: 36, height: 36)
                            .background(Color.primary, in: Circle())
                    }
                    .accessibilityLabel("停止生成")
                    } else {
                    Button {
                        controller.sendDraft()
                    } label: {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(canSend ? Color.white : Color.secondary)
                            .frame(width: 36, height: 36)
                            .background(canSend ? Color.accentColor : Color.secondary.opacity(0.12), in: Circle())
                    }
                    .disabled(!canSend)
                    .accessibilityLabel("发送")
                    }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(6)
        .background(
            Color(uiColor: .secondarySystemBackground),
            in: RoundedRectangle(cornerRadius: 24, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 0.5)
        }
        .shadow(color: .black.opacity(0.06), radius: 10, y: 3)
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .background(.bar)
        .onChange(of: selectedPhotoItems) { _, items in
            guard !items.isEmpty else { return }
            Task {
                for item in items {
                    do {
                        if let data = try await item.loadTransferable(type: Data.self) {
                            try controller.addImage(data: data)
                        }
                    } catch {
                        controller.showAttachmentError(error)
                    }
                }
                selectedPhotoItems = []
            }
        }
    }
}
