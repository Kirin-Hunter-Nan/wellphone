import Foundation
import UIKit

protocol ChatImageProcessing {
    func prepareImage(
        from data: Data,
        currentImageCount: Int,
        currentTotalBytes: Int
    ) throws -> PendingChatImage
}

struct DefaultChatImageProcessor: ChatImageProcessing {
    private let maximumImageCount = 4
    private let maximumImageBytes = 4 * 1_024 * 1_024
    private let maximumTotalBytes = 12 * 1_024 * 1_024
    private let maximumDimension: CGFloat = 1_600

    func prepareImage(
        from data: Data,
        currentImageCount: Int,
        currentTotalBytes: Int
    ) throws -> PendingChatImage {
        guard currentImageCount < maximumImageCount else {
            throw ChatAttachmentError.tooManyImages
        }
        guard let image = UIImage(data: data) else {
            throw ChatAttachmentError.invalidImage
        }

        let normalized = try normalizedJPEG(image)
        guard normalized.count <= maximumImageBytes,
              currentTotalBytes + normalized.count <= maximumTotalBytes else {
            throw ChatAttachmentError.imageTooLarge
        }
        return PendingChatImage(
            id: UUID(),
            data: normalized,
            mimeType: "image/jpeg"
        )
    }

    private func normalizedJPEG(_ image: UIImage) throws -> Data {
        let largest = max(image.size.width, image.size.height)
        let scale = largest > maximumDimension ? maximumDimension / largest : 1
        let size = CGSize(
            width: image.size.width * scale,
            height: image.size.height * scale
        )
        let rendered = UIGraphicsImageRenderer(size: size).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        guard let data = rendered.jpegData(compressionQuality: 0.72) else {
            throw ChatAttachmentError.invalidImage
        }
        return data
    }
}
