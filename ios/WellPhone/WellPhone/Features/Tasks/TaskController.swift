import Foundation
import Observation
import SwiftData

@MainActor
@Observable
final class TaskController {
    private(set) var tasks: [AgentTask] = []
    private let modelContext: ModelContext
    private let runtime: AgentRuntime
    private let notifier: any TaskNotifying

    var activeTasks: [AgentTask] {
        tasks.filter { $0.status.isActive }
    }

    var completedTasks: [AgentTask] {
        tasks.filter { $0.status == .completed }
    }

    var inactiveTasks: [AgentTask] {
        tasks.filter { !$0.status.isActive && $0.status != .completed }
    }

    init(
        modelContext: ModelContext,
        runtime: AgentRuntime,
        notifier: any TaskNotifying
    ) {
        self.modelContext = modelContext
        self.runtime = runtime
        self.notifier = notifier
        refresh()
    }

    convenience init(modelContext: ModelContext) {
        self.init(
            modelContext: modelContext,
            runtime: .live(),
            notifier: LocalTaskNotificationCenter.shared
        )
    }

    convenience init(
        modelContext: ModelContext,
        reminderExecutor: any ReminderExecuting
    ) {
        self.init(
            modelContext: modelContext,
            runtime: .testing(reminderExecutor: reminderExecutor),
            notifier: DisabledTaskNotifier()
        )
    }

    func refresh() {
        let descriptor = FetchDescriptor<AgentTask>(
            sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]
        )
        tasks = (try? modelContext.fetch(descriptor)) ?? []
    }

    func steps(for task: AgentTask) -> [AgentTaskStep] {
        let taskID = task.id
        let descriptor = FetchDescriptor<AgentTaskStep>(
            predicate: #Predicate { $0.taskID == taskID },
            sortBy: [SortDescriptor(\.sequence)]
        )
        return (try? modelContext.fetch(descriptor)) ?? []
    }

    func task(id: UUID) -> AgentTask? {
        tasks.first { $0.id == id }
    }

    func sourceMessage(for task: AgentTask) -> ChatMessage? {
        guard let sourceMessageID = task.sourceMessageID else { return nil }
        let descriptor = FetchDescriptor<ChatMessage>(
            predicate: #Predicate { $0.id == sourceMessageID }
        )
        return try? modelContext.fetch(descriptor).first
    }

    func prepareTool(
        from request: AgentToolRequest,
        conversationID: UUID,
        sourceMessageID: UUID?
    ) async throws -> AgentTask {
        let preparedRequest = try runtime.prepare(request: request)
        let prepared = preparedRequest.task
        let requiresConfirmation = preparedRequest.descriptor.confirmationPolicy == .always
        let task = AgentTask(
            conversationID: conversationID,
            sourceMessageID: sourceMessageID,
            title: prepared.title,
            toolCallID: request.id,
            capability: preparedRequest.descriptor.capability,
            status: requiresConfirmation ? .waitingForConfirmation : .created,
            phase: requiresConfirmation ? .waitingForConfirmation : .planning,
            progress: requiresConfirmation ? 0.35 : 0.2,
            detail: prepared.detail,
            scheduledAt: prepared.scheduledAt,
            targetName: prepared.targetName,
            argumentsJSON: request.arguments
        )
        modelContext.insert(task)

        let now = Date()
        let taskSteps = [
            AgentTaskStep(
                taskID: task.id,
                sequence: 0,
                title: prepared.stepTitles.validation,
                status: .completed,
                startedAt: now,
                completedAt: now
            ),
            AgentTaskStep(
                taskID: task.id,
                sequence: 1,
                title: prepared.stepTitles.confirmation,
                status: requiresConfirmation ? .running : .completed,
                startedAt: now,
                completedAt: requiresConfirmation ? nil : now
            ),
            AgentTaskStep(
                taskID: task.id,
                sequence: 2,
                title: prepared.stepTitles.execution
            ),
            AgentTaskStep(
                taskID: task.id,
                sequence: 3,
                title: prepared.stepTitles.verification
            ),
        ]
        taskSteps.forEach(modelContext.insert)
        try modelContext.save()
        tasks.insert(task, at: 0)

        if requiresConfirmation {
            await notifier.post(AgentTaskNotification(
                taskID: task.id,
                kind: .authorizationRequired,
                title: "任务等待你的确认",
                body: "“\(task.title)”需要你授权后才能继续。"
            ))
        }
        return task
    }

    func confirmTask(taskID: UUID) async {
        guard let task = task(id: taskID),
              task.status == .waitingForConfirmation else { return }

        do {
            completeStep(1, for: task)
            startStep(2, for: task)
            task.status = .running
            task.phase = .executing
            task.progress = 0.55
            task.errorMessage = nil
            touchAndSave(task)

            let receipt = try await runtime.execute(task: task)
            completeStep(2, for: task)
            startStep(3, for: task)
            task.phase = .verifying
            task.progress = 0.82
            touchAndSave(task)

            let verified = try runtime.verify(receipt: receipt, for: task)
            completeStep(3, for: task)
            task.status = .completed
            task.phase = .completed
            task.progress = 1
            task.resultSummary = verified.summary
            touchAndSave(task)
            await notifier.post(AgentTaskNotification(
                taskID: task.id,
                kind: .completed,
                title: "任务已完成",
                body: verified.summary
            ))
        } catch {
            failRunningStep(for: task)
            task.status = .failed
            task.phase = .failed
            task.progress = nil
            task.errorMessage = error.localizedDescription
            touchAndSave(task)
        }
    }

    func cancelTask(taskID: UUID) {
        guard let task = task(id: taskID), task.status == .waitingForConfirmation else { return }
        completeStep(1, for: task)
        task.status = .cancelled
        task.phase = .cancelled
        task.progress = nil
        task.resultSummary = "你取消了这次操作，未执行任何系统写入。"
        touchAndSave(task)
    }

    private func step(_ sequence: Int, for task: AgentTask) -> AgentTaskStep? {
        steps(for: task).first { $0.sequence == sequence }
    }

    private func startStep(_ sequence: Int, for task: AgentTask) {
        guard let step = step(sequence, for: task) else { return }
        step.status = .running
        step.startedAt = Date()
    }

    private func completeStep(_ sequence: Int, for task: AgentTask) {
        guard let step = step(sequence, for: task) else { return }
        step.status = .completed
        step.completedAt = Date()
    }

    private func failRunningStep(for task: AgentTask) {
        guard let step = steps(for: task).first(where: { $0.status == .running }) else { return }
        step.status = .failed
        step.completedAt = Date()
    }

    private func touchAndSave(_ task: AgentTask) {
        task.updatedAt = Date()
        try? modelContext.save()
        tasks.sort { $0.updatedAt > $1.updatedAt }
    }
}
