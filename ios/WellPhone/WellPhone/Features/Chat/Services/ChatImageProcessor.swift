import Foundation
import UIKit
import Vision

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
            mimeType: "image/jpeg",
            extractedText: recognizedText(from: renderedCGImage(normalized))
        )
    }

    private func renderedCGImage(_ data: Data) -> CGImage? {
        UIImage(data: data)?.cgImage
    }

    private func recognizedText(from image: CGImage?) -> String? {
        guard let image else { return nil }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        do {
            try VNImageRequestHandler(cgImage: image).perform([request])
        } catch {
            // The original image remains available to the multimodal model.
            return nil
        }
        guard let results = request.results else { return nil }
        let lines: [String] = results.compactMap { observation -> String? in
            guard let candidate = observation.topCandidates(1).first,
                  candidate.confidence >= 0.35 else { return nil }
            return candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { !$0.isEmpty }
        guard !lines.isEmpty else { return nil }
        return String(lines.joined(separator: "\n").prefix(12_000))
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
