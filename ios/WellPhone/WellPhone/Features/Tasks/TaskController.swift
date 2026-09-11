import Foundation
import Observation
import SwiftData

@MainActor
@Observable
final class TaskController {
    struct AssistantFollowUp: Equatable, Sendable {
        let conversationID: UUID
        let toolCallID: String
        let text: String
    }

    private(set) var tasks: [AgentTask] = []
    private let modelContext: ModelContext
    private let runtime: AgentRuntime
    private let notifier: any TaskNotifying
    private let resultReporter: any ToolResultReporting
    private let checkpointReporter: any TaskCheckpointReporting
    @ObservationIgnored
    var onAssistantFollowUp: ((AssistantFollowUp) -> Void)?

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
        notifier: any TaskNotifying,
        resultReporter: any ToolResultReporting = DisabledToolResultReporter(),
        checkpointReporter: any TaskCheckpointReporting = DisabledTaskCheckpointReporter()
    ) {
        self.modelContext = modelContext
        self.runtime = runtime
        self.notifier = notifier
        self.resultReporter = resultReporter
        self.checkpointReporter = checkpointReporter
        refresh()
    }

    convenience init(modelContext: ModelContext) {
        self.init(
            modelContext: modelContext,
            runtime: .live(),
            notifier: LocalTaskNotificationCenter.shared,
            resultReporter: URLSessionToolResultReporter(
                baseURL: AppConfiguration.modelProxyBaseURL
            ),
            checkpointReporter: URLSessionTaskCheckpointReporter(
                baseURL: AppConfiguration.modelProxyBaseURL
            )
        )
    }

    convenience init(
        modelContext: ModelContext,
        reminderExecutor: any ReminderExecuting
    ) {
        self.init(
            modelContext: modelContext,
            runtime: .testing(reminderExecutor: reminderExecutor),
            notifier: DisabledTaskNotifier(),
            resultReporter: DisabledToolResultReporter(),
            checkpointReporter: DisabledTaskCheckpointReporter()
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
        if let existingTask = tasks.first(where: {
            $0.conversationID == conversationID && $0.toolCallID == request.id
        }) {
            guard existingTask.capability == request.capability,
                  existingTask.argumentsJSON == request.arguments else {
                throw AgentRuntimeError.conflictingToolRequest(request.id)
            }
            return existingTask
        }
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
        queueCheckpoint(for: task)

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
            queueCheckpoint(for: task)

            let receipt = try await runtime.execute(task: task)
            completeStep(2, for: task)
            startStep(3, for: task)
            task.phase = .verifying
            task.progress = 0.82
            touchAndSave(task)
            queueCheckpoint(for: task)

            let verified = try runtime.verify(receipt: receipt, for: task)
            completeStep(3, for: task)
            task.status = .completed
            task.phase = .completed
            task.progress = 1
            task.resultSummary = verified.summary
            touchAndSave(task)
            queueCheckpoint(for: task)
            await notifier.post(AgentTaskNotification(
                taskID: task.id,
                kind: .completed,
                title: "任务已完成",
                body: verified.summary
            ))
            await queueAndReportResult(for: task)
        } catch {
            failRunningStep(for: task)
            task.status = .failed
            task.phase = .failed
            task.progress = nil
            task.errorMessage = error.localizedDescription
            touchAndSave(task)
            queueCheckpoint(for: task)
            await queueAndReportResult(for: task)
        }
    }

    func cancelTask(taskID: UUID) async {
        guard let task = task(id: taskID), task.status == .waitingForConfirmation else { return }
        completeStep(1, for: task)
        task.status = .cancelled
        task.phase = .cancelled
        task.progress = nil
        task.resultSummary = "你取消了这次操作，未执行任何系统写入。"
        touchAndSave(task)
        queueCheckpoint(for: task)
        await queueAndReportResult(for: task)
    }

    func flushPendingCheckpoints() async {
        let pendingTasks = tasks.filter { $0.checkpointReportState == .pending }
        for task in pendingTasks {
            guard let report = makeCheckpointReport(for: task) else { continue }
            await reportCheckpoint(
                report,
                conversationID: task.conversationID,
                taskID: task.id
            )
        }
    }

    func flushPendingResultReports() async {
        let pendingTasks = tasks.filter { $0.resultReportState == .pending }
        for task in pendingTasks {
            await reportResult(for: task)
        }
    }

    func retryResultReport(taskID: UUID) async {
        guard let task = task(id: taskID), task.resultReportState == .pending else { return }
        await reportResult(for: task)
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

    private func queueCheckpoint(for task: AgentTask) {
        guard task.toolCallID != nil, task.capability != nil else { return }
        task.checkpointRevision += 1
        task.checkpointReportState = .pending
        task.checkpointReportError = nil
        touchAndSave(task)
        guard let report = makeCheckpointReport(for: task) else { return }
        let conversationID = task.conversationID
        let taskID = task.id
        Task { [weak self] in
            await self?.reportCheckpoint(
                report,
                conversationID: conversationID,
                taskID: taskID
            )
        }
    }

    private func reportCheckpoint(
        _ report: TaskCheckpointReport,
        conversationID: UUID,
        taskID: UUID
    ) async {
        do {
            let acknowledgement = try await checkpointReporter.report(
                report,
                conversationID: conversationID
            )
            guard let task = task(id: taskID),
                  task.checkpointRevision == report.revision else { return }
            task.checkpointRevision = max(
                task.checkpointRevision,
                acknowledgement.currentRevision
            )
            task.checkpointReportState = .delivered
            task.checkpointReportError = nil
            touchAndSave(task)
        } catch let error as TaskCheckpointReporterError where error.isConflict {
            guard let task = task(id: taskID),
                  task.checkpointRevision == report.revision else { return }
            task.checkpointReportState = .conflict
            task.checkpointReportError = error.localizedDescription
            touchAndSave(task)
        } catch {
            guard let task = task(id: taskID),
                  task.checkpointRevision == report.revision else { return }
            task.checkpointReportState = .pending
            task.checkpointReportError = error.localizedDescription
            touchAndSave(task)
        }
    }

    private func makeCheckpointReport(for task: AgentTask) -> TaskCheckpointReport? {
        guard let toolCallID = task.toolCallID,
              let capability = task.capability,
              task.checkpointRevision > 0 else { return nil }
        let revision = task.checkpointRevision
        return TaskCheckpointReport(
            requestID: "task-checkpoint-\(task.id.uuidString.lowercased())-\(revision)",
            protocolVersion: WellPhoneStreamDecoder.protocolVersion,
            taskID: task.id,
            revision: revision,
            toolCallID: toolCallID,
            capability: capability,
            status: task.status,
            phase: task.phase,
            progress: task.progress,
            detail: task.detail,
            resultSummary: task.resultSummary,
            errorMessage: task.errorMessage,
            occurredAt: task.updatedAt
        )
    }

    private func queueAndReportResult(for task: AgentTask) async {
        guard task.toolCallID != nil, task.capability != nil else { return }
        task.resultReportState = .pending
        task.resultReportError = nil
        touchAndSave(task)
        await reportResult(for: task)
    }

    private func reportResult(for task: AgentTask) async {
        guard let report = makeResultReport(for: task) else { return }

        do {
            let acknowledgement = try await resultReporter.report(
                report,
                conversationID: task.conversationID
            )
            if let assistantMessage = acknowledgement.assistantMessage?
                .trimmingCharacters(in: .whitespacesAndNewlines),
               !assistantMessage.isEmpty,
               let toolCallID = task.toolCallID {
                onAssistantFollowUp?(AssistantFollowUp(
                    conversationID: task.conversationID,
                    toolCallID: toolCallID,
                    text: assistantMessage
                ))
            }
            task.resultReportState = .delivered
            task.resultReportError = nil
        } catch let error as ToolResultReporterError where error.isConflict {
            task.resultReportState = .conflict
            task.resultReportError = error.localizedDescription
        } catch {
            task.resultReportState = .pending
            task.resultReportError = error.localizedDescription
        }
        touchAndSave(task)
    }

    private func makeResultReport(for task: AgentTask) -> AgentToolResultReport? {
        guard let toolCallID = task.toolCallID,
              let capability = task.capability else { return nil }

        let status: AgentToolResultStatus
        let result: AgentToolResultReport.ResultBody?
        let reportError: AgentToolResultReport.ErrorBody?
        switch task.status {
        case .completed:
            status = .verified
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime]
            formatter.timeZone = .current
            result = .init(
                summary: task.resultSummary ?? "任务已完成并通过验证。",
                title: task.title,
                dueAt: task.scheduledAt.map(formatter.string(from:)),
                timeZone: TimeZone.current.identifier
            )
            reportError = nil
        case .cancelled:
            status = .declined
            result = .init(summary: task.resultSummary ?? "用户取消了这次操作。")
            reportError = nil
        case .failed:
            status = .failed
            result = nil
            reportError = .init(
                code: "device_execution_failed",
                message: task.errorMessage ?? "设备端 Tool 执行失败。"
            )
        case .created, .running, .waitingForConfirmation:
            return nil
        }

        return AgentToolResultReport(
            requestID: "tool-result-\(task.id.uuidString.lowercased())",
            protocolVersion: WellPhoneStreamDecoder.protocolVersion,
            toolCallID: toolCallID,
            taskID: task.id,
            capability: capability,
            status: status,
            result: result,
            error: reportError
        )
    }
}
