import Foundation

enum ToolRiskLevel: String, Codable, Sendable {
    case read
    case write
    case externalSideEffect
    case destructive
}

enum ToolConfirmationPolicy: String, Codable, Sendable {
    case never
    case always
}

struct AgentToolDescriptor: Equatable, Sendable {
    let capability: String
    let riskLevel: ToolRiskLevel
    let confirmationPolicy: ToolConfirmationPolicy
    let supportsRetry: Bool
}

struct AgentToolStepTitles: Equatable, Sendable {
    let validation: String
    let confirmation: String
    let execution: String
    let verification: String
}

struct PreparedAgentToolTask: Equatable, Sendable {
    let title: String
    let detail: String?
    let scheduledAt: Date?
    let targetName: String?
    let stepTitles: AgentToolStepTitles
}

struct ToolExecutionReceipt: Sendable {
    let payload: Data
}

struct VerifiedToolResult: Equatable, Sendable {
    let summary: String
}

/// A device capability with deterministic preparation, execution, and verification.
/// Provider-specific model tool names are normalized by the AI backend before this layer.
@MainActor
protocol AgentTool: AnyObject {
    var descriptor: AgentToolDescriptor { get }

    func prepare(argumentsJSON: String) throws -> PreparedAgentToolTask
    func execute(
        argumentsJSON: String,
        idempotencyKey: String
    ) async throws -> ToolExecutionReceipt
    func recoverExecution(
        argumentsJSON: String,
        idempotencyKey: String
    ) async throws -> ToolExecutionReceipt?
    func verify(
        receipt: ToolExecutionReceipt,
        argumentsJSON: String
    ) throws -> VerifiedToolResult
}
