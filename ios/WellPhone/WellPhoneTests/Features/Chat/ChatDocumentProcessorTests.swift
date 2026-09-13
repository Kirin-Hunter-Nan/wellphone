import Foundation
import Testing
import UIKit
@testable import WellPhone

struct ChatDocumentProcessorTests {
    @Test @MainActor
    func extractsTextFromASelectedPDF() throws {
        let renderer = UIGraphicsPDFRenderer(
            bounds: CGRect(x: 0, y: 0, width: 595, height: 842)
        )
        let data = renderer.pdfData { context in
            context.beginPage()
            ("上海航班 MU5101，10 月 1 日 08:00 起飞" as NSString).draw(
                at: CGPoint(x: 48, y: 60),
                withAttributes: [.font: UIFont.systemFont(ofSize: 16)]
            )
        }

        let document = try DefaultChatDocumentProcessor().prepareDocument(
            from: data,
            filename: "机票确认单.pdf",
            mimeType: "application/pdf",
            currentDocumentCount: 0,
            currentExtractedCharacterCount: 0
        )

        #expect(document.mimeType == "application/pdf")
        #expect(document.filename == "机票确认单.pdf")
        #expect(document.extractedText.contains("MU5101"))
        #expect(document.extractedText.contains("08:00"))
    }

    @Test @MainActor
    func acceptsUTF8TextEvidence() throws {
        let document = try DefaultChatDocumentProcessor().prepareDocument(
            from: Data("酒店：上海浦东嘉里大酒店".utf8),
            filename: "酒店.txt",
            mimeType: "text/plain",
            currentDocumentCount: 0,
            currentExtractedCharacterCount: 0
        )

        #expect(document.extractedText == "酒店：上海浦东嘉里大酒店")
        #expect(document.fileExtension == "txt")
    }

    @Test @MainActor
    func rejectsAPDFWithoutExtractableText() {
        let emptyPDF = UIGraphicsPDFRenderer(
            bounds: CGRect(x: 0, y: 0, width: 100, height: 100)
        ).pdfData { $0.beginPage() }

        #expect(throws: ChatAttachmentError.self) {
            try DefaultChatDocumentProcessor().prepareDocument(
                from: emptyPDF,
                filename: "扫描件.pdf",
                mimeType: "application/pdf",
                currentDocumentCount: 0,
                currentExtractedCharacterCount: 0
            )
        }
    }

    @Test @MainActor
    func rejectsUnsupportedAndOversizedTextDocuments() {
        let processor = DefaultChatDocumentProcessor()

        #expect(throws: ChatAttachmentError.self) {
            try processor.prepareDocument(
                from: Data("calendar".utf8),
                filename: "meeting.ics",
                mimeType: "text/calendar",
                currentDocumentCount: 0,
                currentExtractedCharacterCount: 0
            )
        }
        #expect(throws: ChatAttachmentError.self) {
            try processor.prepareDocument(
                from: Data(String(repeating: "行", count: 20_001).utf8),
                filename: "过长.txt",
                mimeType: "text/plain",
                currentDocumentCount: 0,
                currentExtractedCharacterCount: 0
            )
        }
    }
}
