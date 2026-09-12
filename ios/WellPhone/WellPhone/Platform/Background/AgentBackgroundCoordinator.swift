import BackgroundTasks
import Foundation
import OSLog

@MainActor
protocol AgentBackgroundCoordinating: AnyObject {
    typealias WorkHandler = @MainActor (UUID) async -> Bool
    typealias ExpirationHandler = @MainActor (UUID) async -> Void

    func configure(
        workHandler: @escaping WorkHandler,
        expirationHandler: @escaping ExpirationHandler
    )
    func register(taskID: UUID)
    func submit(taskID: UUID, title: String, subtitle: String) throws
    func update(taskID: UUID, progress: Double?, title: String, subtitle: String?)
    func finish(taskID: UUID, success: Bool)
    func cancel(taskID: UUID)
}

@MainActor
final class ContinuedProcessingBackgroundCoordinator: AgentBackgroundCoordinating {
    private let scheduler = BGTaskScheduler.shared
    private let identifierPrefix: String
    private let logger = Logger(
        subsystem: Bundle.main.bundleIdentifier ?? "com.michaelnan.WellPhone",
        category: "BackgroundCoordinator"
    )
    private var registeredIdentifiers: Set<String> = []
    private var systemTasks: [UUID: BGContinuedProcessingTask] = [:]
    private var workTasks: [UUID: Task<Void, Never>] = [:]
    private var workHandler: WorkHandler?
    private var expirationHandler: ExpirationHandler?

    init(bundleIdentifier: String = Bundle.main.bundleIdentifier ?? "com.michaelnan.WellPhone") {
        identifierPrefix = "\(bundleIdentifier).agent-task"
    }

    func configure(
        workHandler: @escaping WorkHandler,
        expirationHandler: @escaping ExpirationHandler
    ) {
        self.workHandler = workHandler
        self.expirationHandler = expirationHandler
    }

    func register(taskID: UUID) {
        let identifier = identifier(for: taskID)
        guard registeredIdentifiers.insert(identifier).inserted else { return }

        let registered = scheduler.register(
            forTaskWithIdentifier: identifier,
            using: .main
        ) { [weak self] task in
            guard let continuedTask = task as? BGContinuedProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            Task { @MainActor [weak self] in
                self?.handleLaunch(continuedTask, taskID: taskID)
            }
        }
        if !registered {
            registeredIdentifiers.remove(identifier)
        }
    }

    func submit(taskID: UUID, title: String, subtitle: String) throws {
        register(taskID: taskID)
        let request = BGContinuedProcessingTaskRequest(
            identifier: identifier(for: taskID),
            title: title,
            subtitle: subtitle
        )
        request.strategy = .queue
        try scheduler.submit(request)
    }

    func update(
        taskID: UUID,
        progress: Double?,
        title: String,
        subtitle: String?
    ) {
        guard let task = systemTasks[taskID] else { return }
        let clampedProgress = min(max(progress ?? 0, 0), 1)
        task.progress.completedUnitCount = Int64(clampedProgress * 1_000)
        task.updateTitle(title, subtitle: subtitle ?? "正在处理")
    }

    func finish(taskID: UUID, success: Bool) {
        workTasks.removeValue(forKey: taskID)?.cancel()
        guard let task = systemTasks.removeValue(forKey: taskID) else {
            // The request may still be queued if the server finishes before iOS
            // invokes the launch handler. There is no active BGTask to complete.
            scheduler.cancel(taskRequestWithIdentifier: identifier(for: taskID))
            return
        }
        if success {
            task.progress.completedUnitCount = task.progress.totalUnitCount
        }
        task.expirationHandler = nil
        task.setTaskCompleted(success: success)
        logger.info("Completed background lease for task \(taskID, privacy: .public), success: \(success)")
    }

    func cancel(taskID: UUID) {
        workTasks.removeValue(forKey: taskID)?.cancel()
        guard let task = systemTasks.removeValue(forKey: taskID) else {
            scheduler.cancel(taskRequestWithIdentifier: identifier(for: taskID))
            return
        }
        task.updateTitle("任务已取消", subtitle: "已停止处理")
        task.progress.completedUnitCount = task.progress.totalUnitCount
        task.expirationHandler = nil
        // An intentional cancellation isn't a failed execution. The persisted
        // AgentTask carries the business-level cancelled state.
        task.setTaskCompleted(success: true)
        logger.info("Cancelled background lease for task \(taskID, privacy: .public)")
    }

    private func handleLaunch(_ task: BGContinuedProcessingTask, taskID: UUID) {
        systemTasks[taskID] = task
        task.progress.totalUnitCount = 1_000
        task.progress.completedUnitCount = 0

        task.expirationHandler = { [weak self] in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.workTasks.removeValue(forKey: taskID)?.cancel()
                await self.expirationHandler?(taskID)
                self.completeExpiredLease(taskID: taskID)
            }
        }

        guard let workHandler else {
            finish(taskID: taskID, success: false)
            return
        }
        workTasks[taskID] = Task { @MainActor [weak self] in
            let success = await workHandler(taskID)
            guard !Task.isCancelled else { return }
            self?.finish(taskID: taskID, success: success)
        }
    }

    private func identifier(for taskID: UUID) -> String {
        "\(identifierPrefix).\(taskID.uuidString.lowercased())"
    }

    private func completeExpiredLease(taskID: UUID) {
        guard let task = systemTasks.removeValue(forKey: taskID) else { return }
        task.updateTitle("已转由服务端继续处理", subtitle: "任务未失败，稍后同步结果")
        task.progress.completedUnitCount = task.progress.totalUnitCount
        task.expirationHandler = nil
        // This BG task represents the on-device monitoring lease, not the remote
        // business job. Expiration ends local monitoring, while the durable server
        // task remains the source of truth and continues running.
        task.setTaskCompleted(success: true)
        logger.notice("Background lease expired for task \(taskID, privacy: .public); server work continues")
    }
}

@MainActor
final class DisabledAgentBackgroundCoordinator: AgentBackgroundCoordinating {
    func configure(
        workHandler: @escaping WorkHandler,
        expirationHandler: @escaping ExpirationHandler
    ) {}

    func register(taskID: UUID) {}

    func submit(taskID: UUID, title: String, subtitle: String) throws {}

    func update(taskID: UUID, progress: Double?, title: String, subtitle: String?) {}

    func finish(taskID: UUID, success: Bool) {}

    func cancel(taskID: UUID) {}
}
