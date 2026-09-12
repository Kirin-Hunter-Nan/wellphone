import Foundation
import SwiftData

extension ConversationController {
    func appendAssistantFollowUp(_ followUp: TaskController.AssistantFollowUp) {
        let conversationID = followUp.conversationID
        let descriptor = FetchDescriptor<ChatMessage>(
            predicate: #Predicate { $0.conversationID == conversationID }
        )
        let alreadyExists = (try? modelContext.fetch(descriptor))?
            .contains { $0.sourceToolCallID == followUp.toolCallID } == true
        guard !alreadyExists else { return }

        let message = ChatMessage(
            conversationID: conversationID,
            role: .assistant,
            text: followUp.text,
            deliveryState: .sent,
            sourceToolCallID: followUp.toolCallID
        )
        modelContext.insert(message)
        if activeConversationID == conversationID {
            messages.append(message)
        }
        if let targetConversation = conversations.first(where: { $0.id == conversationID }) {
            touch(targetConversation)
        }
        save()
    }

    func makeConversation(from firstMessage: String) -> Conversation {
        let title = String(firstMessage.prefix(24))
        let conversation = Conversation(title: title)
        modelContext.insert(conversation)
        self.conversation = conversation
        conversations.insert(conversation, at: 0)
        return conversation
    }

    func touch(_ conversation: Conversation) {
        conversation.updatedAt = Date()
        conversations.sort { $0.updatedAt > $1.updatedAt }
    }

    func loadConversationHistory() {
        let conversationDescriptor = FetchDescriptor<Conversation>(
            sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]
        )

        do {
            conversations = try modelContext.fetch(conversationDescriptor)
            conversation = nil
            messages = []

            let streamingState = DeliveryState.streaming.rawValue
            let interruptedDescriptor = FetchDescriptor<ChatMessage>(
                predicate: #Predicate { $0.deliveryStateRawValue == streamingState }
            )
            let interruptedMessages = try modelContext.fetch(interruptedDescriptor)
            if !interruptedMessages.isEmpty {
                for message in interruptedMessages {
                    message.deliveryState = .stopped
                }
                save()
            }
        } catch {
            errorMessage = "无法读取本地聊天记录。"
        }
    }

    func loadMessages(for conversation: Conversation) {
        let conversationID = conversation.id
        let messageDescriptor = FetchDescriptor<ChatMessage>(
            predicate: #Predicate { $0.conversationID == conversationID },
            sortBy: [SortDescriptor(\.createdAt)]
        )

        do {
            messages = try modelContext.fetch(messageDescriptor)
        } catch {
            messages = []
            errorMessage = "无法读取这段会话。"
        }
    }

    func save() {
        do {
            try modelContext.save()
        } catch {
            errorMessage = "无法保存本地聊天记录。"
        }
    }
}
