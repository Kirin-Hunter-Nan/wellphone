import Testing
import UIKit
@testable import WellPhone

struct ChatImageProcessorTests {
    @Test @MainActor
    func normalizesASelectedImageForMultimodalUpload() throws {
        let source = UIGraphicsImageRenderer(size: CGSize(width: 32, height: 24)).image {
            context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 32, height: 24))
        }
        let input = try #require(source.pngData())

        let pending = try DefaultChatImageProcessor().prepareImage(
            from: input,
            currentImageCount: 0,
            currentTotalBytes: 0
        )

        #expect(pending.mimeType == "image/jpeg")
        #expect(!pending.data.isEmpty)
        #expect(UIImage(data: pending.data) != nil)
    }

    @Test @MainActor
    func rejectsMoreThanFourImages() throws {
        #expect(throws: ChatAttachmentError.self) {
            try DefaultChatImageProcessor().prepareImage(
                from: Data(),
                currentImageCount: 4,
                currentTotalBytes: 0
            )
        }
    }
}
