import Foundation
import SwiftData

enum MessageRole: String, Codable, Sendable {
    case user
    case assistant
}

enum DeliveryState: String, Codable, Sendable {
    case sent
    case streaming
    case stopped
    case failed
}

@Model
final class ChatMessage {
    @Attribute(.unique) var id: UUID
    var conversationID: UUID
    var roleRawValue: String
    var text: String
    var createdAt: Date
    var deliveryStateRawValue: String
    var relatedTaskID: UUID?
    var sourceToolCallID: String?

    var role: MessageRole {
        get { MessageRole(rawValue: roleRawValue) ?? .assistant }
        set { roleRawValue = newValue.rawValue }
    }

    var deliveryState: DeliveryState {
        get { DeliveryState(rawValue: deliveryStateRawValue) ?? .failed }
        set { deliveryStateRawValue = newValue.rawValue }
    }

    init(
        id: UUID = UUID(),
        conversationID: UUID,
        role: MessageRole,
        text: String,
        createdAt: Date = Date(),
        deliveryState: DeliveryState,
        relatedTaskID: UUID? = nil,
        sourceToolCallID: String? = nil
    ) {
        self.id = id
        self.conversationID = conversationID
        self.roleRawValue = role.rawValue
        self.text = text
        self.createdAt = createdAt
        self.deliveryStateRawValue = deliveryState.rawValue
        self.relatedTaskID = relatedTaskID
        self.sourceToolCallID = sourceToolCallID
    }
}
