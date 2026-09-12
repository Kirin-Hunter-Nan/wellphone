import Foundation

struct PendingChatImage: Identifiable {
    let id: UUID
    let data: Data
    let mimeType: String
}

enum ChatAttachmentError: LocalizedError {
    case tooManyImages
    case invalidImage
    case imageTooLarge

    var errorDescription: String? {
        switch self {
        case .tooManyImages: "每条消息最多选择 4 张图片。"
        case .invalidImage: "无法读取这张图片。"
        case .imageTooLarge: "图片处理后仍然过大，请选择另一张。"
        }
    }
}
