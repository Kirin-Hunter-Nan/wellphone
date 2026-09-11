import Foundation
import SwiftData

enum AttachmentKind: String, Codable, Sendable {
    case image
    case pdf
    case audio
    case file
}

enum AttachmentUploadState: String, Codable, Sendable {
    case selected
    case uploading
    case uploaded
    case failed
}

/// Persistent metadata for a chat attachment. Binary data remains in a file or
/// object store and is deliberately not written into SwiftData.
@Model
final class ChatAttachment {
    @Attribute(.unique) var id: UUID
    var messageID: UUID?
    var conversationID: UUID
    var kindRawValue: String
    var mimeType: String
    var originalFilename: String?
    var localPath: String?
    var remoteURL: String?
    var uploadStateRawValue: String
    var byteCount: Int?
    var pixelWidth: Int?
    var pixelHeight: Int?
    var createdAt: Date

    var kind: AttachmentKind {
        get { AttachmentKind(rawValue: kindRawValue) ?? .file }
        set { kindRawValue = newValue.rawValue }
    }

    var uploadState: AttachmentUploadState {
        get { AttachmentUploadState(rawValue: uploadStateRawValue) ?? .failed }
        set { uploadStateRawValue = newValue.rawValue }
    }

    init(
        id: UUID = UUID(),
        messageID: UUID? = nil,
        conversationID: UUID,
        kind: AttachmentKind,
        mimeType: String,
        originalFilename: String? = nil,
        localPath: String? = nil,
        remoteURL: String? = nil,
        uploadState: AttachmentUploadState = .selected,
        byteCount: Int? = nil,
        pixelWidth: Int? = nil,
        pixelHeight: Int? = nil,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.messageID = messageID
        self.conversationID = conversationID
        self.kindRawValue = kind.rawValue
        self.mimeType = mimeType
        self.originalFilename = originalFilename
        self.localPath = localPath
        self.remoteURL = remoteURL
        self.uploadStateRawValue = uploadState.rawValue
        self.byteCount = byteCount
        self.pixelWidth = pixelWidth
        self.pixelHeight = pixelHeight
        self.createdAt = createdAt
    }
}
