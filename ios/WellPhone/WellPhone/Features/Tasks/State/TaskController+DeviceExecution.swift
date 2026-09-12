import Foundation

extension TaskController {
    func recoverOrExecute(_ task: AgentTask) async throws -> ToolExecutionReceipt {
        let deadlineExpired = task.executionDeadlineAt.map { $0 <= Date() } ?? false
        do {
            if let recovered = try await runtime.recoverExecution(task: task) {
                task.executionDeadlineAt = nil
                queueCheckpoint(for: task)
                return recovered
            }
        } catch {
            let canRetry = try runtime.supportsExecutionRetry(task: task)
            let disposition = try runtime.executionErrorDisposition(error, for: task)
            guard canRetry, disposition == .retryable else { throw error }
            task.lastExecutionErrorMessage = error.localizedDescription
            queueCheckpoint(for: task)
        }

        try throwIfCancellationRequested(task)
        if deadlineExpired {
            task.executionDeadlineAt = nil
            throw TaskRecoveryError.executionDeadlineExceeded
        }
        guard try runtime.supportsExecutionRetry(task: task) else {
            throw TaskRecoveryError.executionOutcomeUnknown
        }
        return try await executeWithRetry(task)
    }

    func executeWithRetry(_ task: AgentTask) async throws -> ToolExecutionReceipt {
        let supportsRetry = try runtime.supportsExecutionRetry(task: task)
        while task.executionAttemptCount < executionRetryPolicy.maximumAttempts {
            try throwIfCancellationRequested(task)
            try await waitForScheduledRetry(task)
            try throwIfCancellationRequested(task)
            task.executionAttemptCount += 1
            task.nextExecutionRetryAt = nil
            task.executionDeadlineAt = Date().addingTimeInterval(
                Double(executionDeadlinePolicy.attemptTimeoutNanoseconds) / 1_000_000_000
            )
            queueCheckpoint(for: task)

            do {
                let receipt = try await executeWithinDeadline(task)
                task.executionDeadlineAt = nil
                return receipt
            } catch TaskRecoveryError.executionDeadlineExceeded {
                task.lastExecutionErrorMessage = (
                    TaskRecoveryError.executionDeadlineExceeded.localizedDescription
                )
                queueCheckpoint(for: task)
                return try await recoverTimedOutExecution(task)
            } catch {
                task.executionDeadlineAt = nil
                let disposition = try runtime.executionErrorDisposition(error, for: task)
                guard supportsRetry,
                      disposition == .retryable else { throw error }

                task.lastExecutionErrorMessage = error.localizedDescription
                try throwIfCancellationRequested(task)
                guard let delay = executionRetryPolicy.delay(
                    afterFailedAttempt: task.executionAttemptCount
                ) else {
                    throw TaskRecoveryError.executionRetriesExhausted(
                        lastError: error.localizedDescription
                    )
                }
                task.nextExecutionRetryAt = Date().addingTimeInterval(
                    Double(delay) / 1_000_000_000
                )
                queueCheckpoint(for: task)
            }
        }
        throw TaskRecoveryError.executionRetriesExhausted(
            lastError: task.lastExecutionErrorMessage
        )
    }

    func executeWithinDeadline(_ task: AgentTask) async throws -> ToolExecutionReceipt {
        let timeout = executionDeadlinePolicy.attemptTimeoutNanoseconds
        let executionRequest = try runtime.executionRequest(for: task)
        return try await withThrowingTaskGroup(of: ToolExecutionReceipt.self) { group in
            group.addTask { [runtime] in
                try await runtime.execute(request: executionRequest)
            }
            group.addTask {
                try await Task<Never, Never>.sleep(nanoseconds: timeout)
                throw TaskRecoveryError.executionDeadlineExceeded
            }
            defer { group.cancelAll() }
            guard let first = try await group.next() else {
                throw TaskRecoveryError.executionDeadlineExceeded
            }
            return first
        }
    }

    func recoverTimedOutExecution(
        _ task: AgentTask
    ) async throws -> ToolExecutionReceipt {
        do {
            if let recovered = try await runtime.recoverExecution(task: task) {
                task.executionDeadlineAt = nil
                queueCheckpoint(for: task)
                return recovered
            }
        } catch {
            task.executionDeadlineAt = nil
            throw TaskRecoveryError.executionOutcomeUnknownAfterTimeout(
                recoveryError: error.localizedDescription
            )
        }
        task.executionDeadlineAt = nil
        try throwIfCancellationRequested(task)
        throw TaskRecoveryError.executionDeadlineExceeded
    }

    func waitForScheduledRetry(_ task: AgentTask) async throws {
        while let retryAt = task.nextExecutionRetryAt {
            try throwIfCancellationRequested(task)
            let remainingSeconds = retryAt.timeIntervalSinceNow
            guard remainingSeconds > 0 else { return }
            let slice = min(remainingSeconds, 0.1)
            try await Task<Never, Never>.sleep(
                nanoseconds: UInt64(slice * 1_000_000_000)
            )
        }
    }

    func throwIfCancellationRequested(_ task: AgentTask) throws {
        guard task.cancellationRequestedAt == nil else {
            throw TaskRecoveryError.cancellationRequested
        }
    }

    func step(_ sequence: Int, for task: AgentTask) -> AgentTaskStep? {
        steps(for: task).first { $0.sequence == sequence }
    }

    func startStep(_ sequence: Int, for task: AgentTask) {
        guard let step = step(sequence, for: task) else { return }
        step.status = .running
        step.startedAt = Date()
    }

    func completeStep(_ sequence: Int, for task: AgentTask) {
        guard let step = step(sequence, for: task) else { return }
        step.status = .completed
        step.completedAt = Date()
    }

    func failRunningStep(for task: AgentTask) {
        guard let step = steps(for: task).first(where: { $0.status == .running }) else { return }
        step.status = .failed
        step.completedAt = Date()
    }

    func cancelRunningStep(for task: AgentTask) {
        guard let step = steps(for: task).first(where: { $0.status == .running }) else { return }
        step.status = .cancelled
        step.completedAt = Date()
    }

    func finishCancellation(
        _ task: AgentTask,
        summary: String
    ) async {
        cancelRunningStep(for: task)
        task.status = .cancelled
        task.phase = .cancelled
        task.progress = nil
        task.nextExecutionRetryAt = nil
        task.executionDeadlineAt = nil
        task.resultSummary = summary
        touchAndSave(task)
        queueCheckpoint(for: task)
        await queueAndReportResult(for: task)
    }

    func persistReceiptAndBeginVerification(
        _ receipt: ToolExecutionReceipt,
        for task: AgentTask
    ) {
        task.executionReceiptData = receipt.payload
        task.nextExecutionRetryAt = nil
        task.executionDeadlineAt = nil
        completeStep(2, for: task)
        startStep(3, for: task)
        task.phase = .verifying
        task.progress = 0.82
        touchAndSave(task)
        queueCheckpoint(for: task)
    }

    func verifyAndComplete(
        _ task: AgentTask,
        receipt: ToolExecutionReceipt,
        reportResultImmediately: Bool = true
    ) async throws {
        let verified = try runtime.verify(receipt: receipt, for: task)
        completeStep(3, for: task)
        task.status = .completed
        task.phase = .completed
        task.progress = 1
        if task.cancellationRequestedAt != nil {
            task.resultSummary = verified.summary
                + " 取消请求到达时系统写入已经完成，因此仍保留并验证了结果。"
        } else {
            task.resultSummary = verified.summary
        }
        task.errorMessage = nil
        touchAndSave(task)
        queueCheckpoint(for: task)
        await notifier.post(AgentTaskNotification(
            taskID: task.id,
            kind: .completed,
            title: "任务已完成",
            body: verified.summary
        ))
        queueResult(for: task)
        if reportResultImmediately {
            await reportResult(for: task)
        }
    }

    func fail(
        _ task: AgentTask,
        error: any Error,
        reportResultImmediately: Bool = true
    ) async {
        failRunningStep(for: task)
        task.status = .failed
        task.phase = .failed
        task.progress = nil
        task.nextExecutionRetryAt = nil
        task.executionDeadlineAt = nil
        task.errorMessage = error.localizedDescription
        touchAndSave(task)
        queueCheckpoint(for: task)
        queueResult(for: task)
        if reportResultImmediately {
            await reportResult(for: task)
        }
    }
}

enum TaskRecoveryError: LocalizedError {
    case cancellationRequested
    case executionDeadlineExceeded
    case executionOutcomeUnknownAfterTimeout(recoveryError: String)
    case executionOutcomeUnknown
    case executionRetriesExhausted(lastError: String?)
    case missingExecutionReceipt
    case invalidInterruptedPhase

    var errorDescription: String? {
        switch self {
        case .cancellationRequested:
            "任务已按你的请求安全停止，没有开始新的系统写入。"
        case .executionDeadlineExceeded:
            "Tool 执行已超时，且没有找到可验证的系统写入结果；为避免重复执行，任务不会自动重放。"
        case .executionOutcomeUnknownAfterTimeout(let recoveryError):
            "Tool 执行已超时，恢复检查也未能完成：\(recoveryError)"
        case .executionOutcomeUnknown:
            "App 在系统写入阶段中断，无法确认操作结果。为避免重复写入，本次任务不会自动重试。"
        case .executionRetriesExhausted(let lastError):
            if let lastError, !lastError.isEmpty {
                "Tool 执行已达到重试上限。最后一次错误：\(lastError)"
            } else {
                "Tool 执行已达到重试上限。"
            }
        case .missingExecutionReceipt:
            "任务已进入验证阶段，但缺少本机执行凭证，无法安全恢复。"
        case .invalidInterruptedPhase:
            "任务的中断状态不完整，无法安全恢复。"
        }
    }
}
