import Foundation
import PDFKit

protocol ChatDocumentProcessing {
    func prepareDocument(
        from data: Data,
        filename: String,
        mimeType: String?,
        currentDocumentCount: Int,
        currentExtractedCharacterCount: Int
    ) throws -> PendingChatDocument
}

struct DefaultChatDocumentProcessor: ChatDocumentProcessing {
    private let maximumDocumentCount = 3
    private let maximumDocumentBytes = 10 * 1_024 * 1_024
    private let maximumDocumentCharacters = 20_000
    private let maximumTotalCharacters = 24_000

    func prepareDocument(
        from data: Data,
        filename: String,
        mimeType: String?,
        currentDocumentCount: Int,
        currentExtractedCharacterCount: Int
    ) throws -> PendingChatDocument {
        guard currentDocumentCount < maximumDocumentCount else {
            throw ChatAttachmentError.tooManyDocuments
        }
        guard !data.isEmpty else {
            throw ChatAttachmentError.invalidDocument
        }
        guard data.count <= maximumDocumentBytes else {
            throw ChatAttachmentError.documentTooLarge
        }

        let fileExtension = URL(fileURLWithPath: filename).pathExtension.lowercased()
        let resolvedMimeType: String
        let text: String
        switch fileExtension {
        case "pdf":
            resolvedMimeType = "application/pdf"
            text = try extractPDFText(data)
        case "txt":
            resolvedMimeType = "text/plain"
            text = try extractPlainText(data)
        case "md", "markdown":
            resolvedMimeType = "text/markdown"
            text = try extractPlainText(data)
        default:
            if mimeType == "application/pdf" {
                resolvedMimeType = "application/pdf"
                text = try extractPDFText(data)
            } else {
                throw ChatAttachmentError.unsupportedDocument
            }
        }

        let normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            throw ChatAttachmentError.documentHasNoReadableText
        }
        guard normalized.count <= maximumDocumentCharacters,
              currentExtractedCharacterCount + normalized.count <= maximumTotalCharacters else {
            throw ChatAttachmentError.documentTextTooLong
        }

        return PendingChatDocument(
            id: UUID(),
            data: data,
            mimeType: resolvedMimeType,
            filename: filename,
            fileExtension: resolvedMimeType == "application/pdf" ? "pdf" : fileExtension,
            extractedText: normalized
        )
    }

    private func extractPDFText(_ data: Data) throws -> String {
        guard let document = PDFDocument(data: data), document.pageCount > 0 else {
            throw ChatAttachmentError.invalidDocument
        }
        return (0..<document.pageCount)
            .compactMap { document.page(at: $0)?.string }
            .joined(separator: "\n\n")
    }

    private func extractPlainText(_ data: Data) throws -> String {
        if let text = String(data: data, encoding: .utf8) {
            return text
        }
        if let text = String(data: data, encoding: .unicode) {
            return text
        }
        throw ChatAttachmentError.invalidDocument
    }
}
