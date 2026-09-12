import Foundation

extension TaskController {
    func configureBackgroundCoordinator() {
        backgroundCoordinator.configure(
            workHandler: { [weak self] taskID in
                guard let self else { return false }
                return await self.monitorServerTaskInBackground(taskID: taskID)
            },
            expirationHandler: { [weak self] taskID in
                await self?.handleBackgroundTaskExpiration(taskID: taskID)
            }
        )
        for task in tasks where task.executionLocation == .server && task.status.isActive {
            backgroundCoordinator.register(taskID: task.id)
        }
    }

    func beginContinuedProcessing(for task: AgentTask) {
        guard task.executionLocation == .server, task.status.isActive else { return }
        do {
            try backgroundCoordinator.submit(
                taskID: task.id,
                title: task.title,
                subtitle: task.detail ?? "正在启动任务"
            )
        } catch {
            // Submission is an execution-quality enhancement. The server job and
            // foreground polling remain valid if the system temporarily rejects it.
            task.lastExecutionErrorMessage = "无法启动持续后台执行：\(error.localizedDescription)"
            touchAndSave(task)
        }
    }

    func monitorServerTaskInBackground(taskID: UUID) async -> Bool {
        guard let task = task(id: taskID), task.executionLocation == .server else {
            return false
        }
        guard task.status.isActive else { return task.status == .completed }
        startPollingServerTask(task)

        while task.status.isActive && !Task.isCancelled {
            backgroundCoordinator.update(
                taskID: task.id,
                progress: task.progress,
                title: task.title,
                subtitle: task.detail
            )
            try? await Task<Never, Never>.sleep(for: .milliseconds(250))
        }
        return task.status == .completed
    }

    func handleBackgroundTaskExpiration(taskID: UUID) async {
        serverPollingTasks.removeValue(forKey: taskID)?.cancel()
        await flushPendingCheckpoints()
        await flushPendingResultReports()
    }
}
