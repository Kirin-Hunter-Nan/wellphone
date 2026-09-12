import Foundation
import SwiftData

extension ConversationController {
    func addImage(data: Data) throws {
        let image = try imageProcessor.prepareImage(
            from: data,
            currentImageCount: pendingImages.count,
            currentTotalBytes: pendingImages.reduce(0) { $0 + $1.data.count }
        )
        pendingImages.append(image)
    }

    func removePendingImage(id: UUID) {
        pendingImages.removeAll { $0.id == id }
    }

    func showAttachmentError(_ error: any Error) {
        errorMessage = error.localizedDescription
    }

    func attachments(for message: ChatMessage) -> [ChatAttachment] {
        let messageID = message.id
        let descriptor = FetchDescriptor<ChatAttachment>(
            predicate: #Predicate { $0.messageID == messageID },
            sortBy: [SortDescriptor(\.createdAt)]
        )
        return (try? modelContext.fetch(descriptor)) ?? []
    }

    func makePromptMessage(_ message: ChatMessage) -> ChatPromptMessage {
        let imageParts = attachments(for: message).compactMap {
            attachment -> ChatPromptContentPart? in
            guard attachment.kind == .image,
                  let path = attachment.localPath,
                  let data = try? attachmentFileStore.read(path: path) else {
                return nil
            }
            return .imageURL(
                "data:\(attachment.mimeType);base64,\(data.base64EncodedString())"
            )
        }
        guard !imageParts.isEmpty else {
            return ChatPromptMessage(role: message.role, content: message.text)
        }
        var parts: [ChatPromptContentPart] = []
        if !message.text.isEmpty {
            parts.append(.text(message.text))
        }
        parts.append(contentsOf: imageParts)
        return ChatPromptMessage(role: message.role, parts: parts)
    }
}
