import Foundation
import Observation
import SwiftData
import UIKit

struct PendingChatImage: Identifiable {
    let id: UUID
    let data: Data
    let mimeType: String
}

@MainActor
@Observable
final class ConversationController {
    var draft = ""
    private(set) var conversations: [Conversation] = []
    private(set) var messages: [ChatMessage] = []
    private(set) var isGenerating = false
    private(set) var errorMessage: String?
    private(set) var pendingImages: [PendingChatImage] = []

    private let modelContext: ModelContext
    private let gateway: any ModelGateway
    private let taskController: TaskController
    private var conversation: Conversation?
    private var generationTask: Task<Void, Never>?

    var activeConversationID: UUID? {
        conversation?.id
    }

    init(
        modelContext: ModelContext,
        gateway: any ModelGateway,
        taskController: TaskController
    ) {
        self.modelContext = modelContext
        self.gateway = gateway
        self.taskController = taskController
        restoreMostRecentConversation()
        taskController.onAssistantFollowUp = { [weak self] followUp in
            self?.appendAssistantFollowUp(followUp)
        }
    }

    func sendDraft() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!text.isEmpty || !pendingImages.isEmpty), !isGenerating else { return }

        draft = ""
        errorMessage = nil

        let activeConversation = conversation ?? makeConversation(
            from: text.isEmpty ? "图片对话" : text
        )
        let message = ChatMessage(
            conversationID: activeConversation.id,
            role: .user,
            text: text,
            deliveryState: .sent,
            responseRequestID: UUID().uuidString.lowercased()
        )
        modelContext.insert(message)
        for image in pendingImages {
            do {
                let path = try persistAttachmentData(image.data, id: image.id)
                modelContext.insert(ChatAttachment(
                    id: image.id,
                    messageID: message.id,
                    conversationID: activeConversation.id,
                    kind: .image,
                    mimeType: image.mimeType,
                    localPath: path,
                    uploadState: .uploaded,
                    byteCount: image.data.count
                ))
            } catch {
                errorMessage = "图片无法保存，请重新选择。"
                return
            }
        }
        pendingImages = []
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
        pendingImages = []
    }

    func selectConversation(_ conversation: Conversation) {
        guard !isGenerating, conversation.id != activeConversationID else { return }
        errorMessage = nil
        draft = ""
        pendingImages = []
        self.conversation = conversation
        loadMessages(for: conversation)
    }

    private func startReply(in activeConversation: Conversation) {
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

    private func finishGeneration(in activeConversation: Conversation) {
        touch(activeConversation)
        isGenerating = false
        generationTask = nil
        save()
    }

    private func appendAssistantFollowUp(_ followUp: TaskController.AssistantFollowUp) {
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

    private func makeConversation(from firstMessage: String) -> Conversation {
        let title = String(firstMessage.prefix(24))
        let conversation = Conversation(title: title)
        modelContext.insert(conversation)
        self.conversation = conversation
        conversations.insert(conversation, at: 0)
        return conversation
    }

    private func touch(_ conversation: Conversation) {
        conversation.updatedAt = Date()
        conversations.sort { $0.updatedAt > $1.updatedAt }
    }

    private func restoreMostRecentConversation() {
        let conversationDescriptor = FetchDescriptor<Conversation>(
            sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]
        )

        do {
            conversations = try modelContext.fetch(conversationDescriptor)
            guard let conversation = conversations.first else {
                return
            }

            self.conversation = conversation
            loadMessages(for: conversation)

            if let interruptedMessage = messages.last,
               interruptedMessage.deliveryState == .streaming {
                interruptedMessage.deliveryState = .stopped
                save()
            }
        } catch {
            errorMessage = "无法读取本地聊天记录。"
        }
    }

    private func loadMessages(for conversation: Conversation) {
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

    private func save() {
        do {
            try modelContext.save()
        } catch {
            errorMessage = "无法保存本地聊天记录。"
        }
    }

    func addImage(data: Data) throws {
        guard pendingImages.count < 4 else {
            throw ChatAttachmentError.tooManyImages
        }
        guard let image = UIImage(data: data) else {
            throw ChatAttachmentError.invalidImage
        }
        let normalized = try normalizedJPEG(image)
        guard normalized.count <= 4 * 1_024 * 1_024,
              pendingImages.reduce(0, { $0 + $1.data.count }) + normalized.count
                <= 12 * 1_024 * 1_024 else {
            throw ChatAttachmentError.imageTooLarge
        }
        pendingImages.append(PendingChatImage(
            id: UUID(), data: normalized, mimeType: "image/jpeg"
        ))
    }

    func removePendingImage(id: UUID) {
        pendingImages.removeAll { $0.id == id }
    }

    func showAttachmentError(_ error: any Error) {
        errorMessage = error.localizedDescription
    }

    func attachments(for message: ChatMessage) -> [ChatAttachment] {
        let messageID = message.id
        let descriptor = FetchDescriptor<ChatAttachment>(
            predicate: #Predicate { $0.messageID == messageID },
            sortBy: [SortDescriptor(\.createdAt)]
        )
        return (try? modelContext.fetch(descriptor)) ?? []
    }

    private func makePromptMessage(_ message: ChatMessage) -> ChatPromptMessage {
        let imageParts = attachments(for: message).compactMap { attachment -> ChatPromptContentPart? in
            guard attachment.kind == .image,
                  let path = attachment.localPath,
                  let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return nil }
            return .imageURL("data:\(attachment.mimeType);base64,\(data.base64EncodedString())")
        }
        guard !imageParts.isEmpty else {
            return ChatPromptMessage(role: message.role, content: message.text)
        }
        var parts: [ChatPromptContentPart] = []
        if !message.text.isEmpty {
            parts.append(.text(message.text))
        }
        parts.append(contentsOf: imageParts)
        return ChatPromptMessage(role: message.role, parts: parts)
    }

    private func normalizedJPEG(_ image: UIImage) throws -> Data {
        let maximumDimension: CGFloat = 1_600
        let largest = max(image.size.width, image.size.height)
        let scale = largest > maximumDimension ? maximumDimension / largest : 1
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let rendered = UIGraphicsImageRenderer(size: size).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        guard let data = rendered.jpegData(compressionQuality: 0.72) else {
            throw ChatAttachmentError.invalidImage
        }
        return data
    }

    private func persistAttachmentData(_ data: Data, id: UUID) throws -> String {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("WellPhoneAttachments", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        let url = base.appendingPathComponent("\(id.uuidString.lowercased()).jpg")
        try data.write(to: url, options: .atomic)
        return url.path
    }
}

private enum ChatAttachmentError: LocalizedError {
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
