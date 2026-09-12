import Foundation
import SwiftData

extension TaskController {
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

    func touchAndSave(_ task: AgentTask) {
        task.updatedAt = Date()
        try? modelContext.save()
        tasks.sort { $0.updatedAt > $1.updatedAt }
    }

    func queueCheckpoint(for task: AgentTask) {
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

    func reportCheckpoint(
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

    func queueResult(for task: AgentTask) {
        guard task.toolCallID != nil, task.capability != nil else { return }
        task.resultReportState = .pending
        task.resultReportError = nil
        touchAndSave(task)
    }

    func reportResult(for task: AgentTask) async {
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
}
