import Foundation
import SwiftData

enum TaskArtifactKind: String, Codable, Sendable {
    case itinerary
    case json
    case text
    case image
    case pdf
    case map
}

/// A durable output produced by a task, separate from progress checkpoints and
/// the short summary sent back to the model.
@Model
final class TaskArtifact {
    @Attribute(.unique) var id: UUID
    var taskID: UUID
    var kindRawValue: String
    var title: String
    var contentType: String
    var payloadJSON: String?
    var storageReference: String?
    var createdAt: Date

    var kind: TaskArtifactKind {
        get { TaskArtifactKind(rawValue: kindRawValue) ?? .json }
        set { kindRawValue = newValue.rawValue }
    }

    init(
        id: UUID = UUID(),
        taskID: UUID,
        kind: TaskArtifactKind,
        title: String,
        contentType: String,
        payloadJSON: String? = nil,
        storageReference: String? = nil,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.taskID = taskID
        self.kindRawValue = kind.rawValue
        self.title = title
        self.contentType = contentType
        self.payloadJSON = payloadJSON
        self.storageReference = storageReference
        self.createdAt = createdAt
    }
}
