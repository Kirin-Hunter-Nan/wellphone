import Foundation

@MainActor
final class ReminderCreateTool: AgentTool {
    let descriptor = AgentToolDescriptor(
        capability: "reminder.create",
        riskLevel: .write,
        confirmationPolicy: .always,
        supportsRetry: true
    )

    private let creator: any ReminderCreating
    private let verifier: any ReminderVerifying

    init(
        creator: any ReminderCreating,
        verifier: any ReminderVerifying
    ) {
        self.creator = creator
        self.verifier = verifier
    }

    func prepare(argumentsJSON: String) throws -> PreparedAgentToolTask {
        let draft = try ReminderDraft.decode(arguments: argumentsJSON)
        return PreparedAgentToolTask(
            title: draft.title,
            detail: draft.notes,
            scheduledAt: draft.dueAt,
            targetName: draft.listName,
            stepTitles: AgentToolStepTitles(
                validation: "理解并校验提醒内容",
                confirmation: "等待你的确认",
                execution: "写入系统提醒事项",
                verification: "回读并验证结果"
            )
        )
    }

    func execute(
        argumentsJSON: String,
        idempotencyKey: String
    ) async throws -> ToolExecutionReceipt {
        let draft = try ReminderDraft.decode(arguments: argumentsJSON)
        let created = try await creator.create(draft, idempotencyKey: idempotencyKey)
        return ToolExecutionReceipt(payload: try JSONEncoder().encode(created))
    }

    func recoverExecution(
        argumentsJSON: String,
        idempotencyKey: String
    ) async throws -> ToolExecutionReceipt? {
        let draft = try ReminderDraft.decode(arguments: argumentsJSON)
        guard let recovered = try await creator.recover(
            draft,
            idempotencyKey: idempotencyKey
        ) else { return nil }
        return ToolExecutionReceipt(payload: try JSONEncoder().encode(recovered))
    }

    func executionErrorDisposition(_ error: any Error) -> ToolExecutionErrorDisposition {
        guard let reminderError = error as? ReminderToolError else {
            return .terminal
        }
        if case .transientSystemFailure = reminderError {
            return .retryable
        }
        return .terminal
    }

    func verify(
        receipt: ToolExecutionReceipt,
        argumentsJSON: String
    ) throws -> VerifiedToolResult {
        let draft = try ReminderDraft.decode(arguments: argumentsJSON)
        guard let created = try? JSONDecoder().decode(
            CreatedReminder.self,
            from: receipt.payload
        ) else {
            throw ReminderToolError.invalidExecutionReceipt
        }
        let verified = try verifier.verify(created, matches: draft)
        return VerifiedToolResult(
            summary: "已写入“\(verified.listTitle)”并通过回读验证（标识 \(verified.identifierDigest)）。"
        )
    }
}
