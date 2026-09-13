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

    struct AssistantFollowUp: Equatable, Sendable {
        let conversationID: UUID
        let toolCallID: String
        let text: String
    }

    var tasks: [AgentTask] = []
    var calendarImportPrompt: CalendarImportPrompt?
    let modelContext: ModelContext
    let runtime: AgentRuntime
    let notifier: any TaskNotifying
    let resultReporter: any ToolResultReporting
    let checkpointReporter: any TaskCheckpointReporting
    let serverTaskClient: (any ServerTaskServing)?
    let deviceToolExecutor: any DeviceToolExecuting
    let calendarImporter: (any TravelCalendarImporting)?
    let backgroundCoordinator: any AgentBackgroundCoordinating
    let executionRetryPolicy: ToolExecutionRetryPolicy
    let executionDeadlinePolicy: ToolExecutionDeadlinePolicy
    @ObservationIgnored
    private var recoveringTaskIDs: Set<UUID> = []
    @ObservationIgnored
    var serverPollingTasks: [UUID: Task<Void, Never>] = [:]
    @ObservationIgnored
    var pendingDeviceToolResults: [String: DeviceToolExecutionResult] = [:]
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
        deviceToolExecutor: any DeviceToolExecuting = DisabledDeviceToolExecutor(),
        calendarImporter: (any TravelCalendarImporting)? = nil,
        backgroundCoordinator: any AgentBackgroundCoordinating = DisabledAgentBackgroundCoordinator(),
        executionRetryPolicy: ToolExecutionRetryPolicy = .standard,
        executionDeadlinePolicy: ToolExecutionDeadlinePolicy = .standard
    ) {
        self.modelContext = modelContext
        self.runtime = runtime
        self.notifier = notifier
        self.resultReporter = resultReporter
        self.checkpointReporter = checkpointReporter
        self.serverTaskClient = serverTaskClient
        self.deviceToolExecutor = deviceToolExecutor
        self.calendarImporter = calendarImporter
        self.backgroundCoordinator = backgroundCoordinator
        self.executionRetryPolicy = executionRetryPolicy
        self.executionDeadlinePolicy = executionDeadlinePolicy
        refresh()
        configureBackgroundCoordinator()
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
            deviceToolExecutor: WellPhoneDeviceToolExecutor(),
            calendarImporter: EventKitTravelCalendarImporter(),
            backgroundCoordinator: ContinuedProcessingBackgroundCoordinator()
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

    func startTask(taskID: UUID) async {
        guard let task = task(id: taskID),
              task.status == .created || task.status == .waitingForConfirmation else { return }
        if task.executionLocation == .server {
            if task.status == .waitingForConfirmation {
                await confirmServerTask(task)
            } else {
                beginContinuedProcessing(for: task)
                startPollingServerTask(task)
            }
            return
        }

        do {
            if task.status == .waitingForConfirmation {
                completeStep(1, for: task)
            }
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

    func confirmTask(taskID: UUID) async {
        await startTask(taskID: taskID)
    }

    func recoverInterruptedTasks() async {
        let remoteTasks = tasks.filter {
            $0.executionLocation == .server && $0.status.isActive
        }
        for task in remoteTasks {
            backgroundCoordinator.register(taskID: task.id)
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
        if task.status == .waitingForConfirmation || task.status == .created {
            if task.status == .waitingForConfirmation {
                completeStep(1, for: task)
            }
            await finishCancellation(
                task,
                summary: "任务已停止，未执行任何系统写入。"
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

}
