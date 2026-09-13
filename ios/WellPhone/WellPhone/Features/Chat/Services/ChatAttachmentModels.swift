import Foundation

struct PendingChatImage: Identifiable {
    let id: UUID
    let data: Data
    let mimeType: String
}

struct PendingChatDocument: Identifiable {
    let id: UUID
    let data: Data
    let mimeType: String
    let filename: String
    let fileExtension: String
    let extractedText: String
}

enum ChatAttachmentError: LocalizedError {
    case tooManyImages
    case invalidImage
    case imageTooLarge
    case tooManyDocuments
    case unsupportedDocument
    case invalidDocument
    case documentTooLarge
    case documentTextTooLong
    case documentHasNoReadableText

    var errorDescription: String? {
        switch self {
        case .tooManyImages: "每条消息最多选择 4 张图片。"
        case .invalidImage: "无法读取这张图片。"
        case .imageTooLarge: "图片处理后仍然过大，请选择另一张。"
        case .tooManyDocuments: "每条消息最多选择 3 个文件。"
        case .unsupportedDocument: "目前仅支持 PDF、TXT 和 Markdown 文件。"
        case .invalidDocument: "无法读取这个文件。"
        case .documentTooLarge: "单个文件不能超过 10 MB。"
        case .documentTextTooLong: "文件文字过多，请选择不超过 20000 字的文件。"
        case .documentHasNoReadableText:
            "文件中没有可提取的文字；扫描版 PDF 请先以图片形式发送。"
        }
    }
}
