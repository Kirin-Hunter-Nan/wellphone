import Foundation

struct ToolExecutionRetryPolicy: Equatable, Sendable {
    let maximumAttempts: Int
    let delayNanoseconds: [UInt64]

    static let standard = ToolExecutionRetryPolicy(
        maximumAttempts: 3,
        delayNanoseconds: [1_000_000_000, 2_000_000_000]
    )

    static let immediateTesting = ToolExecutionRetryPolicy(
        maximumAttempts: 3,
        delayNanoseconds: [0, 0]
    )

    func delay(afterFailedAttempt attempt: Int) -> UInt64? {
        guard attempt > 0,
              attempt < maximumAttempts,
              !delayNanoseconds.isEmpty else { return nil }
        return delayNanoseconds[min(attempt - 1, delayNanoseconds.count - 1)]
    }
}
