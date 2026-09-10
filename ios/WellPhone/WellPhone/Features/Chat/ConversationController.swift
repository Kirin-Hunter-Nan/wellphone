import Foundation
import Observation
import SwiftData

@MainActor
@Observable
final class ConversationController {
    var draft = ""
    private(set) var messages: [ChatMessage] = []
    private(set) var isGenerating = false
    private(set) var errorMessage: String?

    private let modelContext: ModelContext
    private let gateway: any ModelGateway
    private var conversation: Conversation?
    private var generationTask: Task<Void, Never>?

    init(modelContext: ModelContext, gateway: any ModelGateway) {
        self.modelContext = modelContext
        self.gateway = gateway
        restoreMostRecentConversation()
    }

    func sendDraft() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isGenerating else { return }

        draft = ""
        errorMessage = nil

        let activeConversation = conversation ?? makeConversation(from: text)
        let message = ChatMessage(
            conversationID: activeConversation.id,
            role: .user,
            text: text,
            deliveryState: .sent
        )
        modelContext.insert(message)
        messages.append(message)
        touch(activeConversation)
        save()

        startReply(in: activeConversation)
    }

    func stopGenerating() {
        generationTask?.cancel()
    }

    func retryLastResponse() {
        guard !isGenerating, let activeConversation = conversation else { return }

        if let lastMessage = messages.last, lastMessage.role == .assistant {
            messages.removeLast()
            modelContext.delete(lastMessage)
        }

        errorMessage = nil
        save()
        startReply(in: activeConversation)
    }

    func startNewConversation() {
        generationTask?.cancel()
        generationTask = nil
        isGenerating = false
        errorMessage = nil
        conversation = nil
        messages = []
        draft = ""
    }

    private func startReply(in activeConversation: Conversation) {
        let prompt = messages.map {
            ChatPromptMessage(role: $0.role, content: $0.text)
        }
        let response = ChatMessage(
            conversationID: activeConversation.id,
            role: .assistant,
            text: "",
            deliveryState: .streaming
        )
        modelContext.insert(response)
        messages.append(response)
        isGenerating = true
        save()

        generationTask = Task { [weak self, gateway] in
            do {
                for try await chunk in gateway.streamReply(
                    to: prompt,
                    conversationID: activeConversation.id
                ) {
                    guard self != nil else { return }
                    response.text += chunk
                }

                try Task.checkCancellation()
                guard let self else { return }
                response.deliveryState = .sent
                self.finishGeneration(in: activeConversation)
            } catch is CancellationError {
                guard let self else { return }
                response.deliveryState = .stopped
                self.finishGeneration(in: activeConversation)
            } catch {
                guard let self else { return }
                response.deliveryState = .failed
                self.errorMessage = error.localizedDescription
                self.finishGeneration(in: activeConversation)
            }
        }
    }

    private func finishGeneration(in activeConversation: Conversation) {
        touch(activeConversation)
        isGenerating = false
        generationTask = nil
        save()
    }

    private func makeConversation(from firstMessage: String) -> Conversation {
        let title = String(firstMessage.prefix(24))
        let conversation = Conversation(title: title)
        modelContext.insert(conversation)
        self.conversation = conversation
        return conversation
    }

    private func touch(_ conversation: Conversation) {
        conversation.updatedAt = Date()
    }

    private func restoreMostRecentConversation() {
        var conversationDescriptor = FetchDescriptor<Conversation>(
            sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]
        )
        conversationDescriptor.fetchLimit = 1

        do {
            guard let conversation = try modelContext.fetch(conversationDescriptor).first else {
                return
            }

            self.conversation = conversation
            let conversationID = conversation.id
            let messageDescriptor = FetchDescriptor<ChatMessage>(
                predicate: #Predicate { $0.conversationID == conversationID },
                sortBy: [SortDescriptor(\.createdAt)]
            )
            messages = try modelContext.fetch(messageDescriptor)

            if let interruptedMessage = messages.last,
               interruptedMessage.deliveryState == .streaming {
                interruptedMessage.deliveryState = .stopped
                save()
            }
        } catch {
            errorMessage = "无法读取本地聊天记录。"
        }
    }

    private func save() {
        do {
            try modelContext.save()
        } catch {
            errorMessage = "无法保存本地聊天记录。"
        }
    }
}
