import Foundation
import SwiftData

extension ConversationController {
    func startReply(in activeConversation: Conversation) {
        guard let sourceMessage = messages.last(where: { $0.role == .user }) else { return }
        let sourceMessageID = sourceMessage.id
        let requestID = sourceMessage.responseRequestID ?? UUID().uuidString.lowercased()
        sourceMessage.responseRequestID = requestID
        let prompt = messages.map(makePromptMessage)
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
                for try await event in gateway.streamReply(
                    to: prompt,
                    conversationID: activeConversation.id,
                    requestID: requestID
                ) {
                    guard self != nil else { return }
                    switch event {
                    case .textDelta(let text):
                        response.text += text
                    case .toolRequest(let request):
                        guard response.relatedTaskID == nil else { continue }
                        let task = try await self?.taskController.prepareTool(
                            from: request,
                            conversationID: activeConversation.id,
                            sourceMessageID: sourceMessageID
                        )
                        response.relatedTaskID = task?.id
                        response.text = "请确认这项操作"
                    case .done:
                        break
                    }
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

    func finishGeneration(in activeConversation: Conversation) {
        touch(activeConversation)
        isGenerating = false
        generationTask = nil
        save()
    }
}
