import Foundation
import SwiftData

extension TaskController {
    func prepareServerTool(
        from request: AgentToolRequest,
        conversationID: UUID,
        sourceMessageID: UUID?
    ) async throws -> AgentTask {
        guard let serverTaskClient else {
            throw ServerTaskIntegrationError.unavailable
        }
        let input = try JSONDecoder().decode(
            [String: JSONValue].self,
            from: Data(request.arguments.utf8)
        )
        let title = ServerTaskStateMapper.title(
            capability: request.capability,
            input: input
        )
        let snapshot = try await serverTaskClient.create(
            conversationID: conversationID,
            toolCallID: request.id,
            capability: request.capability,
            title: title,
            input: input
        )
        let task = AgentTask(
            id: snapshot.id,
            conversationID: conversationID,
            sourceMessageID: sourceMessageID,
            title: snapshot.title,
            toolCallID: request.id,
            capability: request.capability,
            executionLocation: .server,
            status: ServerTaskStateMapper.status(snapshot.status),
            phase: ServerTaskStateMapper.phase(
                snapshot.phase,
                status: ServerTaskStateMapper.status(snapshot.status)
            ),
            progress: snapshot.progress,
            detail: snapshot.detail,
            argumentsJSON: request.arguments
        )
        modelContext.insert(task)
        apply(snapshot, to: task)
        try modelContext.save()
        tasks.insert(task, at: 0)
        return task
    }

    func confirmServerTask(_ task: AgentTask) async {
        guard let serverTaskClient else { return }
        do {
            apply(try await serverTaskClient.confirm(taskID: task.id), to: task)
            try modelContext.save()
            beginContinuedProcessing(for: task)
            startPollingServerTask(task)
        } catch {
            task.errorMessage = error.localizedDescription
            touchAndSave(task)
        }
    }

    func cancelServerTask(_ task: AgentTask) async {
        guard let serverTaskClient else { return }
        do {
            apply(try await serverTaskClient.cancel(taskID: task.id), to: task)
            try modelContext.save()
            serverPollingTasks[task.id]?.cancel()
            serverPollingTasks[task.id] = nil
            await queueAndReportResult(for: task)
            backgroundCoordinator.cancel(taskID: task.id)
        } catch {
            task.errorMessage = error.localizedDescription
            touchAndSave(task)
        }
    }

    func startPollingServerTask(_ task: AgentTask) {
        guard serverPollingTasks[task.id] == nil, let serverTaskClient else { return }
        let taskID = task.id
        serverPollingTasks[taskID] = Task { [weak self] in
            defer { self?.serverPollingTasks[taskID] = nil }
            while !Task.isCancelled {
                do {
                    let snapshot = try await serverTaskClient.get(taskID: taskID)
                    guard let self, let localTask = self.task(id: taskID) else { return }
                    let wasCompleted = localTask.status == .completed
                    self.apply(snapshot, to: localTask)
                    try self.modelContext.save()
                    if !localTask.status.isActive {
                        if localTask.status == .completed, !wasCompleted {
                            let calendarArtifactIsAvailable = self.artifacts(for: localTask)
                                .contains {
                                    $0.contentType
                                        == "application/vnd.wellphone.calendar-events+json"
                                        && $0.storageReference?.hasPrefix("eventkit:") != true
                                }
                            if calendarArtifactIsAvailable {
                                if self.calendarWasExplicitlyRequested(for: localTask) {
                                    await self.importTravelCalendar(
                                        taskID: localTask.id,
                                        includeOutcomeInSummary: true
                                    )
                                } else {
                                    self.offerCalendarImport(for: localTask)
                                }
                            }
                            await self.notifier.post(AgentTaskNotification(
                                taskID: localTask.id,
                                kind: .completed,
                                title: "任务已完成",
                                body: localTask.resultSummary ?? localTask.title
                            ))
                        }
                        await self.queueAndReportResult(for: localTask)
                        self.backgroundCoordinator.finish(
                            taskID: localTask.id,
                            success: localTask.status == .completed
                        )
                        return
                    }
                } catch {
                    guard let self, let localTask = self.task(id: taskID) else { return }
                    localTask.detail = "同步暂时中断，稍后自动重试"
                    localTask.lastExecutionErrorMessage = error.localizedDescription
                    self.touchAndSave(localTask)
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func apply(_ snapshot: ServerTaskSnapshot, to task: AgentTask) {
        task.title = snapshot.title
        task.status = ServerTaskStateMapper.status(snapshot.status)
        task.phase = ServerTaskStateMapper.phase(snapshot.phase, status: task.status)
        task.progress = snapshot.progress
        task.detail = snapshot.detail
        task.resultSummary = snapshot.resultSummary
        task.errorMessage = snapshot.errorMessage
        task.executionAttemptCount = snapshot.attemptCount
        task.updatedAt = snapshot.updatedAt
        backgroundCoordinator.update(
            taskID: task.id,
            progress: task.progress,
            title: task.title,
            subtitle: task.detail
        )

        steps(for: task).forEach(modelContext.delete)
        snapshot.steps.forEach { item in
            modelContext.insert(AgentTaskStep(
                taskID: task.id,
                sequence: item.order,
                title: item.title,
                status: AgentTaskStepStatus(rawValue: item.status) ?? .pending
            ))
        }

        let existingArtifactIDs = Set(artifacts(for: task).map(\.id))
        for item in snapshot.artifacts where !existingArtifactIDs.contains(item.id) {
            let data = item.payload.flatMap { try? JSONEncoder().encode($0) }
            modelContext.insert(TaskArtifact(
                id: item.id,
                taskID: task.id,
                kind: TaskArtifactKind(rawValue: item.kind) ?? .json,
                title: item.title,
                contentType: item.contentType,
                payloadJSON: data.flatMap { String(data: $0, encoding: .utf8) },
                storageReference: item.storageReference,
                createdAt: item.createdAt
            ))
        }
    }
}

enum ServerTaskIntegrationError: LocalizedError {
    case unavailable

    var errorDescription: String? { "后台任务服务尚未配置。" }
}
