import Foundation
import SwiftData
import Testing
@testable import WellPhone

struct BackgroundCoordinatorTests {
    @Test @MainActor
    func serverTaskUsesContinuedProcessingLifecycle() async throws {
        let container = try makeContainer()
        let backgroundCoordinator = RecordingBackgroundCoordinator()
        let serverClient = CompletingBackgroundServerClient()
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: BackgroundFakeReminderExecutor()),
            notifier: DisabledTaskNotifier(),
            resultReporter: DisabledToolResultReporter(),
            checkpointReporter: DisabledTaskCheckpointReporter(),
            serverTaskClient: serverClient,
            backgroundCoordinator: backgroundCoordinator
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_background",
                capability: "travel.plan",
                arguments: #"{"destination":"上海","startDate":"2026-10-01","endDate":"2026-10-01"}"#,
                executionLocation: .server
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.startTask(taskID: task.id)
        for _ in 0..<100 where task.status.isActive {
            try await Task<Never, Never>.sleep(for: .milliseconds(10))
        }

        #expect(task.status == .completed)
        #expect(backgroundCoordinator.submittedTaskIDs == [task.id])
        #expect(backgroundCoordinator.updatedTaskIDs.contains(task.id))
        #expect(backgroundCoordinator.finishedTasks[task.id] == true)
    }

    @Test @MainActor
    func restoredActiveServerTaskRegistersForBackgroundLaunch() throws {
        let container = try makeContainer()
        let task = AgentTask(
            conversationID: UUID(),
            title: "继续规划旅行",
            capability: "travel.plan",
            executionLocation: .server,
            status: .running,
            phase: .executing
        )
        container.mainContext.insert(task)
        try container.mainContext.save()
        let backgroundCoordinator = RecordingBackgroundCoordinator()

        _ = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: BackgroundFakeReminderExecutor()),
            notifier: DisabledTaskNotifier(),
            backgroundCoordinator: backgroundCoordinator
        )

        #expect(backgroundCoordinator.registeredTaskIDs == [task.id])
    }

    @Test @MainActor
    func expirationEndsOnlyLocalMonitoringAndKeepsServerTaskActive() async throws {
        let container = try makeContainer()
        let backgroundCoordinator = RecordingBackgroundCoordinator()
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: BackgroundFakeReminderExecutor()),
            notifier: DisabledTaskNotifier(),
            resultReporter: DisabledToolResultReporter(),
            checkpointReporter: DisabledTaskCheckpointReporter(),
            serverTaskClient: PendingBackgroundServerClient(),
            backgroundCoordinator: backgroundCoordinator
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_expiration",
                capability: "travel.plan",
                arguments: #"{"destination":"上海","startDate":"2026-10-01","endDate":"2026-10-03"}"#,
                executionLocation: .server
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.startTask(taskID: task.id)
        await backgroundCoordinator.expire(taskID: task.id)

        #expect(task.status.isActive)
        #expect(task.errorMessage == nil)
    }

    @MainActor
    private func makeContainer() throws -> ModelContainer {
        let schema = Schema([
            Conversation.self,
            ChatMessage.self,
            ChatAttachment.self,
            AgentTask.self,
            AgentTaskStep.self,
            TaskArtifact.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return try ModelContainer(for: schema, configurations: [configuration])
    }
}

@MainActor
private final class RecordingBackgroundCoordinator: AgentBackgroundCoordinating {
    private(set) var registeredTaskIDs: [UUID] = []
    private(set) var submittedTaskIDs: [UUID] = []
    private(set) var updatedTaskIDs: [UUID] = []
    private(set) var finishedTasks: [UUID: Bool] = [:]
    private var expirationHandler: ExpirationHandler?

    func configure(
        workHandler: @escaping WorkHandler,
        expirationHandler: @escaping ExpirationHandler
    ) {
        self.expirationHandler = expirationHandler
    }

    func register(taskID: UUID) {
        registeredTaskIDs.append(taskID)
    }

    func submit(taskID: UUID, title: String, subtitle: String) throws {
        submittedTaskIDs.append(taskID)
    }

    func update(taskID: UUID, progress: Double?, title: String, subtitle: String?) {
        updatedTaskIDs.append(taskID)
    }

    func finish(taskID: UUID, success: Bool) {
        finishedTasks[taskID] = success
    }

    func cancel(taskID: UUID) {
        finishedTasks[taskID] = false
    }

    func expire(taskID: UUID) async {
        await expirationHandler?(taskID)
    }
}

private actor CompletingBackgroundServerClient: ServerTaskServing {
    private let taskID = UUID()
    private var conversationID = UUID()
    private var title = "规划上海旅行"

    func create(
        conversationID: UUID,
        toolCallID: String,
        capability: String,
        title: String,
        input: [String: JSONValue]
    ) async throws -> ServerTaskSnapshot {
        self.conversationID = conversationID
        self.title = title
        return snapshot(status: "queued", progress: 0)
    }

    func confirm(taskID: UUID) async throws -> ServerTaskSnapshot {
        snapshot(status: "queued", progress: 0)
    }

    func cancel(taskID: UUID) async throws -> ServerTaskSnapshot {
        snapshot(status: "cancelled", progress: 0)
    }

    func get(taskID: UUID) async throws -> ServerTaskSnapshot {
        snapshot(status: "completed", progress: 1)
    }

    private func snapshot(status: String, progress: Double) -> ServerTaskSnapshot {
        ServerTaskSnapshot(
            id: taskID,
            conversationId: conversationID,
            capability: "travel.plan",
            title: title,
            status: status,
            phase: status,
            progress: progress,
            detail: status == "completed" ? "旅行规划已完成" : "等待执行",
            resultSummary: status == "completed" ? "旅行规划已完成" : nil,
            errorMessage: nil,
            attemptCount: status == "completed" ? 1 : 0,
            updatedAt: Date(),
            steps: [],
            artifacts: []
        )
    }
}

private actor PendingBackgroundServerClient: ServerTaskServing {
    private let taskID = UUID()
    private var conversationID = UUID()
    private var title = "规划上海旅行"

    func create(
        conversationID: UUID,
        toolCallID: String,
        capability: String,
        title: String,
        input: [String: JSONValue]
    ) async throws -> ServerTaskSnapshot {
        self.conversationID = conversationID
        self.title = title
        return snapshot()
    }

    func confirm(taskID: UUID) async throws -> ServerTaskSnapshot { snapshot() }

    func cancel(taskID: UUID) async throws -> ServerTaskSnapshot { snapshot() }

    func get(taskID: UUID) async throws -> ServerTaskSnapshot { snapshot() }

    private func snapshot() -> ServerTaskSnapshot {
        ServerTaskSnapshot(
            id: taskID,
            conversationId: conversationID,
            capability: "travel.plan",
            title: title,
            status: "running",
            phase: "executing",
            progress: 0.37,
            detail: "Agent 正在进行规划与检查",
            resultSummary: nil,
            errorMessage: nil,
            attemptCount: 1,
            updatedAt: Date(),
            steps: [],
            artifacts: []
        )
    }
}

@MainActor
private final class BackgroundFakeReminderExecutor: ReminderExecuting {
    func create(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder {
        CreatedReminder(identifier: "background-test", listTitle: "提醒事项")
    }

    func recover(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder? {
        nil
    }

    func verify(
        _ created: CreatedReminder,
        matches draft: ReminderDraft
    ) throws -> VerifiedReminder {
        VerifiedReminder(listTitle: created.listTitle, identifierDigest: "background-test")
    }
}
