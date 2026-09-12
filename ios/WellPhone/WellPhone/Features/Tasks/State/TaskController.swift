import Foundation
import Observation
import SwiftData

@MainActor
@Observable
final class TaskController {
    struct CalendarImportPrompt: Identifiable, Equatable {
        let taskID: UUID
        let title: String

        var id: UUID { taskID }
    }

    struct CompletionBanner: Identifiable, Equatable {
        let id = UUID()
        let taskID: UUID
        let title: String
        let summary: String
    }

    struct AssistantFollowUp: Equatable, Sendable {
        let conversationID: UUID
        let toolCallID: String
        let text: String
    }

    var tasks: [AgentTask] = []
    private(set) var calendarImportPrompt: CalendarImportPrompt?
    private(set) var completionBanner: CompletionBanner?
    let modelContext: ModelContext
    private let runtime: AgentRuntime
    let notifier: any TaskNotifying
    private let resultReporter: any ToolResultReporting
    private let checkpointReporter: any TaskCheckpointReporting
    let serverTaskClient: (any ServerTaskServing)?
    private let calendarImporter: (any TravelCalendarImporting)?
    private let executionRetryPolicy: ToolExecutionRetryPolicy
    private let executionDeadlinePolicy: ToolExecutionDeadlinePolicy
    @ObservationIgnored
    private var recoveringTaskIDs: Set<UUID> = []
    @ObservationIgnored
    var serverPollingTasks: [UUID: Task<Void, Never>] = [:]
    @ObservationIgnored
    private var completionBannerTask: Task<Void, Never>?
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
        checkpointReporter: any TaskCheckpointReporting = DisabledTaskCheckpointReporter(),
        serverTaskClient: (any ServerTaskServing)? = nil,
        calendarImporter: (any TravelCalendarImporting)? = nil,
        executionRetryPolicy: ToolExecutionRetryPolicy = .standard,
        executionDeadlinePolicy: ToolExecutionDeadlinePolicy = .standard
    ) {
        self.modelContext = modelContext
        self.runtime = runtime
        self.notifier = notifier
        self.resultReporter = resultReporter
        self.checkpointReporter = checkpointReporter
        self.serverTaskClient = serverTaskClient
        self.calendarImporter = calendarImporter
        self.executionRetryPolicy = executionRetryPolicy
        self.executionDeadlinePolicy = executionDeadlinePolicy
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
            ),
            serverTaskClient: URLSessionServerTaskClient(
                baseURL: AppConfiguration.modelProxyBaseURL
            ),
            calendarImporter: EventKitTravelCalendarImporter()
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
            checkpointReporter: DisabledTaskCheckpointReporter(),
            executionRetryPolicy: .immediateTesting
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
        if request.executionLocation == .server {
            return try await prepareServerTool(
                from: request,
                conversationID: conversationID,
                sourceMessageID: sourceMessageID
            )
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
            executionLocation: .device,
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
        if task.executionLocation == .server {
            await confirmServerTask(task)
            return
        }

        do {
            completeStep(1, for: task)
            startStep(2, for: task)
            task.status = .running
            task.phase = .executing
            task.progress = 0.55
            task.errorMessage = nil
            touchAndSave(task)
            queueCheckpoint(for: task)

            let receipt = try await executeWithRetry(task)
            persistReceiptAndBeginVerification(receipt, for: task)
            try await verifyAndComplete(task, receipt: receipt)
        } catch TaskRecoveryError.cancellationRequested {
            await finishCancellation(
                task,
                summary: TaskRecoveryError.cancellationRequested.localizedDescription
            )
        } catch {
            await fail(task, error: error)
        }
    }

    func recoverInterruptedTasks() async {
        let remoteTasks = tasks.filter {
            $0.executionLocation == .server && $0.status.isActive
        }
        for task in remoteTasks {
            startPollingServerTask(task)
        }
        let interruptedTasks = tasks.filter {
            $0.executionLocation == .device && $0.status == .running
        }
        for task in interruptedTasks {
            guard recoveringTaskIDs.insert(task.id).inserted else { continue }
            await recoverInterruptedTask(task)
            recoveringTaskIDs.remove(task.id)
        }
    }

    private func recoverInterruptedTask(_ task: AgentTask) async {
        switch task.phase {
        case .verifying:
            guard let receiptData = task.executionReceiptData else {
                await fail(
                    task,
                    error: TaskRecoveryError.missingExecutionReceipt,
                    reportResultImmediately: false
                )
                return
            }
            do {
                try await verifyAndComplete(
                    task,
                    receipt: ToolExecutionReceipt(payload: receiptData),
                    reportResultImmediately: false
                )
            } catch {
                await fail(task, error: error, reportResultImmediately: false)
            }
        case .executing:
            do {
                let receipt = try await recoverOrExecute(task)
                persistReceiptAndBeginVerification(receipt, for: task)
                try await verifyAndComplete(
                    task,
                    receipt: receipt,
                    reportResultImmediately: false
                )
            } catch TaskRecoveryError.cancellationRequested {
                await finishCancellation(
                    task,
                    summary: TaskRecoveryError.cancellationRequested.localizedDescription
                )
            } catch {
                await fail(task, error: error, reportResultImmediately: false)
            }
        case .understanding, .planning, .waitingForConfirmation,
             .completed, .failed, .cancelled:
            await fail(
                task,
                error: TaskRecoveryError.invalidInterruptedPhase,
                reportResultImmediately: false
            )
        }
    }

    func cancelTask(taskID: UUID) async {
        guard let task = task(id: taskID) else { return }
        if task.executionLocation == .server {
            await cancelServerTask(task)
            return
        }
        if task.status == .waitingForConfirmation {
            completeStep(1, for: task)
            await finishCancellation(
                task,
                summary: "你取消了这次操作，未执行任何系统写入。"
            )
            return
        }
        guard task.status == .running,
              task.phase == .executing,
              task.cancellationRequestedAt == nil else { return }
        task.cancellationRequestedAt = Date()
        task.nextExecutionRetryAt = nil
        touchAndSave(task)
        queueCheckpoint(for: task)
    }

    func flushPendingCheckpoints() async {
        let pendingTasks = tasks.filter { $0.checkpointReportState == .pending }
        for task in pendingTasks {
            guard let report = TaskReportFactory.checkpoint(for: task) else { continue }
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

    private func recoverOrExecute(_ task: AgentTask) async throws -> ToolExecutionReceipt {
        let deadlineExpired = task.executionDeadlineAt.map { $0 <= Date() } ?? false
        do {
            if let recovered = try await runtime.recoverExecution(task: task) {
                task.executionDeadlineAt = nil
                queueCheckpoint(for: task)
                return recovered
            }
        } catch {
            let canRetry = try runtime.supportsExecutionRetry(task: task)
            let disposition = try runtime.executionErrorDisposition(error, for: task)
            guard canRetry, disposition == .retryable else { throw error }
            task.lastExecutionErrorMessage = error.localizedDescription
            queueCheckpoint(for: task)
        }

        try throwIfCancellationRequested(task)
        if deadlineExpired {
            task.executionDeadlineAt = nil
            throw TaskRecoveryError.executionDeadlineExceeded
        }
        guard try runtime.supportsExecutionRetry(task: task) else {
            throw TaskRecoveryError.executionOutcomeUnknown
        }
        return try await executeWithRetry(task)
    }

    private func executeWithRetry(_ task: AgentTask) async throws -> ToolExecutionReceipt {
        let supportsRetry = try runtime.supportsExecutionRetry(task: task)
        while task.executionAttemptCount < executionRetryPolicy.maximumAttempts {
            try throwIfCancellationRequested(task)
            try await waitForScheduledRetry(task)
            try throwIfCancellationRequested(task)
            task.executionAttemptCount += 1
            task.nextExecutionRetryAt = nil
            task.executionDeadlineAt = Date().addingTimeInterval(
                Double(executionDeadlinePolicy.attemptTimeoutNanoseconds) / 1_000_000_000
            )
            queueCheckpoint(for: task)

            do {
                let receipt = try await executeWithinDeadline(task)
                task.executionDeadlineAt = nil
                return receipt
            } catch TaskRecoveryError.executionDeadlineExceeded {
                task.lastExecutionErrorMessage = (
                    TaskRecoveryError.executionDeadlineExceeded.localizedDescription
                )
                queueCheckpoint(for: task)
                return try await recoverTimedOutExecution(task)
            } catch {
                task.executionDeadlineAt = nil
                let disposition = try runtime.executionErrorDisposition(error, for: task)
                guard supportsRetry,
                      disposition == .retryable else { throw error }

                task.lastExecutionErrorMessage = error.localizedDescription
                try throwIfCancellationRequested(task)
                guard let delay = executionRetryPolicy.delay(
                    afterFailedAttempt: task.executionAttemptCount
                ) else {
                    throw TaskRecoveryError.executionRetriesExhausted(
                        lastError: error.localizedDescription
                    )
                }
                task.nextExecutionRetryAt = Date().addingTimeInterval(
                    Double(delay) / 1_000_000_000
                )
                queueCheckpoint(for: task)
            }
        }
        throw TaskRecoveryError.executionRetriesExhausted(
            lastError: task.lastExecutionErrorMessage
        )
    }

    private func executeWithinDeadline(_ task: AgentTask) async throws -> ToolExecutionReceipt {
        let timeout = executionDeadlinePolicy.attemptTimeoutNanoseconds
        let executionRequest = try runtime.executionRequest(for: task)
        return try await withThrowingTaskGroup(of: ToolExecutionReceipt.self) { group in
            group.addTask { [runtime] in
                try await runtime.execute(request: executionRequest)
            }
            group.addTask {
                try await Task<Never, Never>.sleep(nanoseconds: timeout)
                throw TaskRecoveryError.executionDeadlineExceeded
            }
            defer { group.cancelAll() }
            guard let first = try await group.next() else {
                throw TaskRecoveryError.executionDeadlineExceeded
            }
            return first
        }
    }

    private func recoverTimedOutExecution(
        _ task: AgentTask
    ) async throws -> ToolExecutionReceipt {
        do {
            if let recovered = try await runtime.recoverExecution(task: task) {
                task.executionDeadlineAt = nil
                queueCheckpoint(for: task)
                return recovered
            }
        } catch {
            task.executionDeadlineAt = nil
            throw TaskRecoveryError.executionOutcomeUnknownAfterTimeout(
                recoveryError: error.localizedDescription
            )
        }
        task.executionDeadlineAt = nil
        try throwIfCancellationRequested(task)
        throw TaskRecoveryError.executionDeadlineExceeded
    }

    private func waitForScheduledRetry(_ task: AgentTask) async throws {
        while let retryAt = task.nextExecutionRetryAt {
            try throwIfCancellationRequested(task)
            let remainingSeconds = retryAt.timeIntervalSinceNow
            guard remainingSeconds > 0 else { return }
            let slice = min(remainingSeconds, 0.1)
            try await Task<Never, Never>.sleep(
                nanoseconds: UInt64(slice * 1_000_000_000)
            )
        }
    }

    private func throwIfCancellationRequested(_ task: AgentTask) throws {
        guard task.cancellationRequestedAt == nil else {
            throw TaskRecoveryError.cancellationRequested
        }
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

    private func cancelRunningStep(for task: AgentTask) {
        guard let step = steps(for: task).first(where: { $0.status == .running }) else { return }
        step.status = .cancelled
        step.completedAt = Date()
    }

    private func finishCancellation(
        _ task: AgentTask,
        summary: String
    ) async {
        cancelRunningStep(for: task)
        task.status = .cancelled
        task.phase = .cancelled
        task.progress = nil
        task.nextExecutionRetryAt = nil
        task.executionDeadlineAt = nil
        task.resultSummary = summary
        touchAndSave(task)
        queueCheckpoint(for: task)
        await queueAndReportResult(for: task)
    }

    private func persistReceiptAndBeginVerification(
        _ receipt: ToolExecutionReceipt,
        for task: AgentTask
    ) {
        task.executionReceiptData = receipt.payload
        task.nextExecutionRetryAt = nil
        task.executionDeadlineAt = nil
        completeStep(2, for: task)
        startStep(3, for: task)
        task.phase = .verifying
        task.progress = 0.82
        touchAndSave(task)
        queueCheckpoint(for: task)
    }

    private func verifyAndComplete(
        _ task: AgentTask,
        receipt: ToolExecutionReceipt,
        reportResultImmediately: Bool = true
    ) async throws {
        let verified = try runtime.verify(receipt: receipt, for: task)
        completeStep(3, for: task)
        task.status = .completed
        task.phase = .completed
        task.progress = 1
        if task.cancellationRequestedAt != nil {
            task.resultSummary = verified.summary
                + " 取消请求到达时系统写入已经完成，因此仍保留并验证了结果。"
        } else {
            task.resultSummary = verified.summary
        }
        task.errorMessage = nil
        touchAndSave(task)
        queueCheckpoint(for: task)
        await notifier.post(AgentTaskNotification(
            taskID: task.id,
            kind: .completed,
            title: "任务已完成",
            body: verified.summary
        ))
        showCompletionBanner(for: task, offerCalendarImport: false)
        queueResult(for: task)
        if reportResultImmediately {
            await reportResult(for: task)
        }
    }

    private func fail(
        _ task: AgentTask,
        error: any Error,
        reportResultImmediately: Bool = true
    ) async {
        failRunningStep(for: task)
        task.status = .failed
        task.phase = .failed
        task.progress = nil
        task.nextExecutionRetryAt = nil
        task.executionDeadlineAt = nil
        task.errorMessage = error.localizedDescription
        touchAndSave(task)
        queueCheckpoint(for: task)
        queueResult(for: task)
        if reportResultImmediately {
            await reportResult(for: task)
        }
    }

    func touchAndSave(_ task: AgentTask) {
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
        guard let report = TaskReportFactory.checkpoint(for: task) else { return }
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

    func queueAndReportResult(for task: AgentTask) async {
        queueResult(for: task)
        await reportResult(for: task)
    }

    private func queueResult(for task: AgentTask) {
        guard task.toolCallID != nil, task.capability != nil else { return }
        task.resultReportState = .pending
        task.resultReportError = nil
        touchAndSave(task)
    }

    private func reportResult(for task: AgentTask) async {
        guard let report = TaskReportFactory.result(
            for: task,
            artifacts: artifacts(for: task)
        ) else { return }

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

    func artifacts(for task: AgentTask) -> [TaskArtifact] {
        let taskID = task.id
        let descriptor = FetchDescriptor<TaskArtifact>(
            predicate: #Predicate { $0.taskID == taskID },
            sortBy: [SortDescriptor(\.createdAt)]
        )
        return (try? modelContext.fetch(descriptor)) ?? []
    }

    func importTravelCalendar(taskID: UUID) async {
        guard let task = task(id: taskID),
              let calendarImporter,
              let artifact = artifacts(for: task).first(where: {
                  $0.contentType == "application/vnd.wellphone.calendar-events+json"
              }),
              artifact.storageReference?.hasPrefix("eventkit:") != true,
              let payloadJSON = artifact.payloadJSON,
              let data = payloadJSON.data(using: .utf8) else { return }
        do {
            let identifiers = try await calendarImporter.importEvents(payload: data)
            artifact.storageReference = "eventkit:" + identifiers.joined(separator: ",")
            task.detail = "行程已添加到 Apple 日历。"
            task.errorMessage = nil
            touchAndSave(task)
            calendarImportPrompt = nil
        } catch {
            task.errorMessage = error.localizedDescription
            touchAndSave(task)
        }
    }

    func dismissCalendarImportPrompt() {
        calendarImportPrompt = nil
    }

    func showCompletionBanner(
        for task: AgentTask,
        offerCalendarImport: Bool
    ) {
        completionBannerTask?.cancel()
        let banner = CompletionBanner(
            taskID: task.id,
            title: task.title,
            summary: task.resultSummary ?? "任务已经完成。"
        )
        completionBanner = banner
        completionBannerTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(2_500))
            guard !Task.isCancelled, let self else { return }
            if self.completionBanner?.id == banner.id {
                self.completionBanner = nil
            }
            if offerCalendarImport {
                self.calendarImportPrompt = CalendarImportPrompt(
                    taskID: task.id,
                    title: task.title
                )
            }
            self.completionBannerTask = nil
        }
    }
}

private enum TaskRecoveryError: LocalizedError {
    case cancellationRequested
    case executionDeadlineExceeded
    case executionOutcomeUnknownAfterTimeout(recoveryError: String)
    case executionOutcomeUnknown
    case executionRetriesExhausted(lastError: String?)
    case missingExecutionReceipt
    case invalidInterruptedPhase

    var errorDescription: String? {
        switch self {
        case .cancellationRequested:
            "任务已按你的请求安全停止，没有开始新的系统写入。"
        case .executionDeadlineExceeded:
            "Tool 执行已超时，且没有找到可验证的系统写入结果；为避免重复执行，任务不会自动重放。"
        case .executionOutcomeUnknownAfterTimeout(let recoveryError):
            "Tool 执行已超时，恢复检查也未能完成：\(recoveryError)"
        case .executionOutcomeUnknown:
            "App 在系统写入阶段中断，无法确认操作结果。为避免重复写入，本次任务不会自动重试。"
        case .executionRetriesExhausted(let lastError):
            if let lastError, !lastError.isEmpty {
                "Tool 执行已达到重试上限。最后一次错误：\(lastError)"
            } else {
                "Tool 执行已达到重试上限。"
            }
        case .missingExecutionReceipt:
            "任务已进入验证阶段，但缺少本机执行凭证，无法安全恢复。"
        case .invalidInterruptedPhase:
            "任务的中断状态不完整，无法安全恢复。"
        }
    }
}
