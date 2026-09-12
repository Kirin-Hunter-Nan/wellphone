import Foundation

enum TaskReportFactory {
    static func checkpoint(for task: AgentTask) -> TaskCheckpointReport? {
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
            executionAttemptCount: task.executionAttemptCount,
            nextExecutionRetryAt: task.nextExecutionRetryAt,
            lastExecutionErrorMessage: task.lastExecutionErrorMessage,
            cancellationRequestedAt: task.cancellationRequestedAt,
            executionDeadlineAt: task.executionDeadlineAt,
            occurredAt: task.updatedAt
        )
    }

    static func result(
        for task: AgentTask,
        artifacts: [TaskArtifact]
    ) -> AgentToolResultReport? {
        guard let toolCallID = task.toolCallID,
              let capability = task.capability else { return nil }

        let status: AgentToolResultStatus
        let result: AgentToolResultReport.ResultBody?
        let reportError: AgentToolResultReport.ErrorBody?
        switch task.status {
        case .completed:
            status = .verified
            if task.executionLocation == .server {
                let artifactReferences = artifacts.map {
                    AgentToolResultReport.ArtifactReference(
                        id: $0.id,
                        kind: $0.kind.rawValue,
                        title: $0.title,
                        contentType: $0.contentType,
                        storageReference: $0.storageReference,
                        payload: $0.payloadJSON
                            .flatMap { $0.data(using: .utf8) }
                            .flatMap { try? JSONDecoder().decode(JSONValue.self, from: $0) }
                    )
                }
                result = .init(
                    summary: task.resultSummary ?? "后台任务已完成。",
                    payload: [
                        "serverTaskId": .string(task.id.uuidString.lowercased()),
                        "capability": .string(capability),
                    ],
                    artifacts: artifactReferences.isEmpty ? nil : artifactReferences
                )
                reportError = nil
                break
            }
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime]
            formatter.timeZone = .current
            result = .init(
                summary: task.resultSummary ?? "任务已完成并通过验证。",
                payload: [
                    "title": .string(task.title),
                    "dueAt": task.scheduledAt.map { .string(formatter.string(from: $0)) } ?? .null,
                    "timeZone": .string(TimeZone.current.identifier),
                ]
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
                code: task.executionLocation == .server
                    ? "server_execution_failed" : "device_execution_failed",
                message: task.errorMessage ?? (
                    task.executionLocation == .server
                        ? "服务端任务执行失败。" : "设备端 Tool 执行失败。"
                )
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
