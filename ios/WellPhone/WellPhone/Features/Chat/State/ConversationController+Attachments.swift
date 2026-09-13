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

    func addDocument(data: Data, filename: String, mimeType: String?) throws {
        let document = try documentProcessor.prepareDocument(
            from: data,
            filename: filename,
            mimeType: mimeType,
            currentDocumentCount: pendingDocuments.count,
            currentExtractedCharacterCount: pendingDocuments.reduce(0) {
                $0 + $1.extractedText.count
            }
        )
        pendingDocuments.append(document)
    }

    func removePendingDocument(id: UUID) {
        pendingDocuments.removeAll { $0.id == id }
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
        let attachments = attachments(for: message)
        let documentParts = attachments.compactMap {
            attachment -> ChatPromptContentPart? in
            guard attachment.kind == .pdf || attachment.kind == .file,
                  let extractedText = attachment.extractedText,
                  !extractedText.isEmpty else {
                return nil
            }
            let filename = safeAttachmentFilename(
                attachment.originalFilename ?? "未命名文件"
            )
            let evidence = safeAttachmentEvidence(extractedText)
            return .text("""
                <wellphone_attachment filename="\(filename)">
                \(evidence)
                </wellphone_attachment>
                """)
        }
        let imageOCRParts = attachments.compactMap {
            attachment -> ChatPromptContentPart? in
            guard attachment.kind == .image,
                  let extractedText = attachment.extractedText,
                  !extractedText.isEmpty else {
                return nil
            }
            let filename = "selected-image-\(attachment.id.uuidString.lowercased()).jpg"
            let evidence = safeAttachmentEvidence(extractedText)
            return .text("""
                <wellphone_attachment filename="\(filename)" kind="image-ocr">
                \(evidence)
                </wellphone_attachment>
                """)
        }
        let imageParts = attachments.compactMap {
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
        guard !imageParts.isEmpty || !imageOCRParts.isEmpty || !documentParts.isEmpty else {
            return ChatPromptMessage(role: message.role, content: message.text)
        }
        var parts: [ChatPromptContentPart] = []
        if !message.text.isEmpty {
            parts.append(.text(message.text))
        }
        parts.append(contentsOf: documentParts)
        parts.append(contentsOf: imageOCRParts)
        parts.append(contentsOf: imageParts)
        return ChatPromptMessage(role: message.role, parts: parts)
    }

    private func safeAttachmentFilename(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\n", with: " ")
    }

    private func safeAttachmentEvidence(_ value: String) -> String {
        value
            .replacingOccurrences(
                of: "<wellphone_attachment",
                with: "&lt;wellphone_attachment",
                options: .caseInsensitive
            )
            .replacingOccurrences(
                of: "</wellphone_attachment",
                with: "&lt;/wellphone_attachment",
                options: .caseInsensitive
            )
    }
}
