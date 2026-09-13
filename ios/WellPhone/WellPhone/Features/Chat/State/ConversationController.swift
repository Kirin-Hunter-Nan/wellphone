import Foundation
import Observation
import SwiftData

@MainActor
@Observable
final class ConversationController {
    var draft = ""
    var conversations: [Conversation] = []
    var messages: [ChatMessage] = []
    var isGenerating = false
    var errorMessage: String?
    var pendingImages: [PendingChatImage] = []
    var pendingDocuments: [PendingChatDocument] = []

    let modelContext: ModelContext
    let gateway: any ModelGateway
    let taskController: TaskController
    let imageProcessor: any ChatImageProcessing
    let documentProcessor: any ChatDocumentProcessing
    let attachmentFileStore: any ChatAttachmentFileStoring
    var conversation: Conversation?
    var generationTask: Task<Void, Never>?

    var activeConversationID: UUID? {
        conversation?.id
    }

    init(
        modelContext: ModelContext,
        gateway: any ModelGateway,
        taskController: TaskController,
        imageProcessor: any ChatImageProcessing = DefaultChatImageProcessor(),
        documentProcessor: any ChatDocumentProcessing = DefaultChatDocumentProcessor(),
        attachmentFileStore: any ChatAttachmentFileStoring = LocalChatAttachmentFileStore()
    ) {
        self.modelContext = modelContext
        self.gateway = gateway
        self.taskController = taskController
        self.imageProcessor = imageProcessor
        self.documentProcessor = documentProcessor
        self.attachmentFileStore = attachmentFileStore
        loadConversationHistory()
        taskController.onAssistantFollowUp = { [weak self] followUp in
            self?.appendAssistantFollowUp(followUp)
        }
    }

    func sendDraft() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!text.isEmpty || !pendingImages.isEmpty || !pendingDocuments.isEmpty),
              !isGenerating else { return }

        draft = ""
        errorMessage = nil

        let activeConversation = conversation ?? makeConversation(
            from: text.isEmpty ? "附件对话" : text
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
                let path = try attachmentFileStore.persist(
                    image.data,
                    id: image.id,
                    fileExtension: "jpg"
                )
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
        for document in pendingDocuments {
            do {
                let path = try attachmentFileStore.persist(
                    document.data,
                    id: document.id,
                    fileExtension: document.fileExtension
                )
                modelContext.insert(ChatAttachment(
                    id: document.id,
                    messageID: message.id,
                    conversationID: activeConversation.id,
                    kind: document.mimeType == "application/pdf" ? .pdf : .file,
                    mimeType: document.mimeType,
                    originalFilename: document.filename,
                    localPath: path,
                    extractedText: document.extractedText,
                    uploadState: .uploaded,
                    byteCount: document.data.count
                ))
            } catch {
                errorMessage = "文件无法保存，请重新选择。"
                return
            }
        }
        pendingImages = []
        pendingDocuments = []
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
        pendingDocuments = []
    }

    func selectConversation(_ conversation: Conversation) {
        guard !isGenerating, conversation.id != activeConversationID else { return }
        errorMessage = nil
        draft = ""
        pendingImages = []
        pendingDocuments = []
        self.conversation = conversation
        loadMessages(for: conversation)
    }
}
