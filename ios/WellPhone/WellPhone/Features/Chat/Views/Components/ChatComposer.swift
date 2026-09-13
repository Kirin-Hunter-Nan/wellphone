import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct ChatComposer: View {
    @Environment(ConversationController.self) private var controller
    @FocusState.Binding var isFocused: Bool
    let startVoiceConversation: () -> Void
    @State private var selectedPhotoItems: [PhotosPickerItem] = []
    @State private var isSelectingDocuments = false

    var body: some View {
        @Bindable var controller = controller

        let canSend = !controller.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !controller.pendingImages.isEmpty
            || !controller.pendingDocuments.isEmpty

        return VStack(spacing: 6) {
            if !controller.pendingImages.isEmpty || !controller.pendingDocuments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(controller.pendingImages) { image in
                            pendingImagePreview(image)
                        }
                        ForEach(controller.pendingDocuments) { document in
                            pendingDocumentPreview(document)
                        }
                    }
                    .padding(.horizontal, 4)
                    .padding(.top, 6)
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                Menu {
                    PhotosPicker(
                        selection: $selectedPhotoItems,
                        maxSelectionCount: max(1, 4 - controller.pendingImages.count),
                        matching: .images
                    ) {
                        Label("选择图片", systemImage: "photo.on.rectangle")
                    }
                    .disabled(controller.pendingImages.count >= 4)

                    Button {
                        isSelectingDocuments = true
                    } label: {
                        Label("选择文件", systemImage: "doc")
                    }
                    .disabled(controller.pendingDocuments.count >= 3)
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 19, weight: .medium))
                        .frame(width: 34, height: 36)
                }
                .disabled(
                    controller.isGenerating
                    || (controller.pendingImages.count >= 4
                        && controller.pendingDocuments.count >= 3)
                )
                .accessibilityLabel("添加图片或文件")
                .accessibilityIdentifier("chat.add-attachment")

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
        .fileImporter(
            isPresented: $isSelectingDocuments,
            allowedContentTypes: supportedDocumentTypes,
            allowsMultipleSelection: true
        ) { result in
            do {
                for url in try result.get() {
                    let didAccess = url.startAccessingSecurityScopedResource()
                    defer {
                        if didAccess { url.stopAccessingSecurityScopedResource() }
                    }
                    let values = try? url.resourceValues(forKeys: [.contentTypeKey])
                    try controller.addDocument(
                        data: Data(contentsOf: url),
                        filename: url.lastPathComponent,
                        mimeType: values?.contentType?.preferredMIMEType
                    )
                }
            } catch {
                controller.showAttachmentError(error)
            }
        }
    }

    private var supportedDocumentTypes: [UTType] {
        var types: [UTType] = [.pdf, .plainText]
        if let markdown = UTType(filenameExtension: "md") {
            types.append(markdown)
        }
        return types
    }

    @ViewBuilder
    private func pendingImagePreview(_ image: PendingChatImage) -> some View {
        ZStack(alignment: .topTrailing) {
            if let preview = UIImage(data: image.data) {
                Image(uiImage: preview)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 72, height: 72)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            removeButton {
                controller.removePendingImage(id: image.id)
            }
        }
    }

    private func pendingDocumentPreview(_ document: PendingChatDocument) -> some View {
        ZStack(alignment: .topTrailing) {
            VStack(spacing: 5) {
                Image(systemName: document.mimeType == "application/pdf"
                      ? "doc.richtext.fill"
                      : "doc.text.fill")
                    .font(.title2)
                    .foregroundStyle(Color.accentColor)
                Text(document.filename)
                    .font(.caption2)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
            .frame(width: 92, height: 72)
            .background(
                Color.secondary.opacity(0.1),
                in: RoundedRectangle(cornerRadius: 12)
            )
            removeButton {
                controller.removePendingDocument(id: document.id)
            }
        }
    }

    private func removeButton(action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: "xmark.circle.fill")
                .symbolRenderingMode(.palette)
                .foregroundStyle(.white, .black.opacity(0.65))
        }
        .offset(x: 5, y: -5)
    }
}
