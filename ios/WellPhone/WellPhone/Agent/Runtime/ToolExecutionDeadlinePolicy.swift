import Foundation

struct ToolExecutionDeadlinePolicy: Equatable, Sendable {
    let attemptTimeoutNanoseconds: UInt64

    static let standard = ToolExecutionDeadlinePolicy(
        attemptTimeoutNanoseconds: 30_000_000_000
    )
}
