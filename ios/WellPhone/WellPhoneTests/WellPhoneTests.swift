//
//  WellPhoneTests.swift
//  WellPhoneTests
//
//  Created by 南佳琪 on 2026/9/10.
//

import Foundation
import SwiftData
import Testing
@testable import WellPhone

struct WellPhoneTests {

    @Test @MainActor
    func demoGatewayStreamsAReply() async throws {
        let gateway = DemoModelGateway()
        let prompt = [ChatPromptMessage(role: .user, content: "测试消息")]
        var reply = ""

        for try await event in gateway.streamReply(
            to: prompt,
            conversationID: UUID()
        ) {
            if case .textDelta(let chunk) = event {
                reply += chunk
            }
        }

        #expect(reply.contains("测试消息"))
        #expect(reply.contains("演示模式"))
    }

    @Test @MainActor
    func promptMessageRoundTripsThroughJSON() throws {
        let original = ChatPromptMessage(role: .assistant, content: "你好")
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(ChatPromptMessage.self, from: data)

        #expect(decoded == original)
    }

    @Test @MainActor
    func wellPhoneStreamDecoderReadsDeltasAndCompletion() throws {
        let delta = #"data: {"type":"assistant.delta","protocolVersion":"1.0","responseId":"resp_1","text":"你好"}"#
        let completed = #"data: {"type":"response.completed","protocolVersion":"1.0","responseId":"resp_1"}"#

        #expect(try WellPhoneStreamDecoder.decode(line: delta) == .textDelta("你好"))
        #expect(try WellPhoneStreamDecoder.decode(line: completed) == .completed)
        #expect(try WellPhoneStreamDecoder.decode(line: ": keep-alive") == .ignored)
    }

    @Test @MainActor
    func wellPhoneStreamDecoderReadsCapabilityRequest() throws {
        let line = #"data: {"type":"tool.requested","protocolVersion":"1.0","responseId":"resp_1","toolCallId":"call_1","capability":"reminder.create","arguments":{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}}"#
        let event = try WellPhoneStreamDecoder.decode(line: line)
        guard case .toolRequested(let request) = event else {
            Issue.record("Expected a tool request")
            return
        }

        #expect(request.id == "call_1")
        #expect(request.capability == "reminder.create")
        #expect(try ReminderDraft.decode(arguments: request.arguments).title == "提交报销")
    }

    @Test @MainActor
    func wellPhoneStreamDecoderRejectsUnsupportedProtocolVersion() {
        let line = #"data: {"type":"response.completed","protocolVersion":"2.0","responseId":"resp_1"}"#

        #expect(throws: WellPhoneProtocolError.self) {
            try WellPhoneStreamDecoder.decode(line: line)
        }
    }

    @Test @MainActor
    func reminderDraftValidatesArguments() throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let arguments = #"{"title":"带伞","dueAt":"2027-01-16T10:00:00+08:00","notes":"出门前"}"#
        let draft = try ReminderDraft.decode(arguments: arguments, now: now)

        #expect(draft.title == "带伞")
        #expect(draft.notes == "出门前")
        #expect(draft.listName == nil)
    }

    @Test @MainActor
    func conversationControllerRestoresAndSwitchesHistory() throws {
        let container = try makeContainer()
        let context = container.mainContext
        let older = Conversation(
            title: "较早会话",
            createdAt: Date(timeIntervalSince1970: 100),
            updatedAt: Date(timeIntervalSince1970: 100)
        )
        let newer = Conversation(
            title: "最近会话",
            createdAt: Date(timeIntervalSince1970: 200),
            updatedAt: Date(timeIntervalSince1970: 200)
        )
        context.insert(older)
        context.insert(newer)
        context.insert(ChatMessage(
            conversationID: older.id,
            role: .user,
            text: "旧消息",
            deliveryState: .sent
        ))
        context.insert(ChatMessage(
            conversationID: newer.id,
            role: .user,
            text: "新消息",
            deliveryState: .sent
        ))
        try context.save()

        let taskController = TaskController(modelContext: context)
        let controller = ConversationController(
            modelContext: context,
            gateway: DemoModelGateway(),
            taskController: taskController
        )

        #expect(controller.activeConversationID == newer.id)
        #expect(controller.conversations.map(\.id) == [newer.id, older.id])
        #expect(controller.messages.first?.text == "新消息")

        controller.selectConversation(older)
        #expect(controller.activeConversationID == older.id)
        #expect(controller.messages.first?.text == "旧消息")
    }

    @Test @MainActor
    func conversationLinksCapabilityRequestToInlineTaskCard() async throws {
        let container = try makeContainer()
        let taskController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: FakeReminderExecutor()
        )
        let controller = ConversationController(
            modelContext: container.mainContext,
            gateway: ToolRequestGateway(),
            taskController: taskController
        )
        controller.draft = "明天上午九点提醒我带伞"

        controller.sendDraft()
        for _ in 0..<100 where controller.isGenerating {
            await Task.yield()
        }

        let response = controller.messages.last
        #expect(response?.role == .assistant)
        guard let relatedTaskID = response?.relatedTaskID else {
            Issue.record("Expected the assistant message to link an inline task")
            return
        }
        #expect(taskController.task(id: relatedTaskID)?.status == .waitingForConfirmation)
    }

    @Test @MainActor
    func taskControllerGroupsTasksByStatus() throws {
        let container = try makeContainer()
        let context = container.mainContext
        let conversationID = UUID()
        context.insert(AgentTask(
            conversationID: conversationID,
            title: "运行任务",
            status: .running,
            phase: .executing
        ))
        context.insert(AgentTask(
            conversationID: conversationID,
            title: "完成任务",
            status: .completed,
            phase: .completed
        ))
        try context.save()

        let controller = TaskController(modelContext: context)
        #expect(controller.activeTasks.count == 1)
        #expect(controller.completedTasks.count == 1)
        #expect(controller.inactiveTasks.isEmpty)
    }

    @Test @MainActor
    func reminderTaskRequiresConfirmationThenCompletesAfterVerification() async throws {
        let container = try makeContainer()
        let context = container.mainContext
        let executor = FakeReminderExecutor()
        let notifier = RecordingTaskNotifier()
        let resultReporter = RecordingToolResultReporter()
        let controller = TaskController(
            modelContext: context,
            runtime: .testing(reminderExecutor: executor),
            notifier: notifier,
            resultReporter: resultReporter
        )
        let request = AgentToolRequest(
            id: "call_1",
            capability: "reminder.create",
            arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
        )

        let task = try await controller.prepareTool(
            from: request,
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        #expect(task.status == .waitingForConfirmation)
        #expect(task.toolCallID == "call_1")
        #expect(executor.createCount == 0)
        #expect(notifier.notifications.map(\.kind) == [.authorizationRequired])
        #expect(notifier.notifications.first?.taskID == task.id)

        await controller.confirmTask(taskID: task.id)

        #expect(executor.createCount == 1)
        #expect(executor.verifyCount == 1)
        #expect(task.status == .completed)
        #expect(task.phase == .completed)
        #expect(controller.steps(for: task).allSatisfy { $0.status == .completed })
        #expect(notifier.notifications.map(\.kind) == [.authorizationRequired, .completed])
        #expect(notifier.notifications.last?.body == task.resultSummary)
        #expect(task.resultReportState == .delivered)
        let reports = await resultReporter.reports
        #expect(reports.count == 1)
        #expect(reports.first?.result.toolCallID == "call_1")
        #expect(reports.first?.result.status == .verified)
        #expect(reports.first?.result.result?.title == "提交报销")
        #expect(reports.first?.result.result?.dueAt != nil)
        #expect(reports.first?.result.result?.timeZone == TimeZone.current.identifier)
    }

    @Test @MainActor
    func verifiedToolResultAddsServerFollowUpToChatOnce() async throws {
        let container = try makeContainer()
        let context = container.mainContext
        let reporter = ReplyingToolResultReporter()
        let taskController = TaskController(
            modelContext: context,
            runtime: .testing(reminderExecutor: FakeReminderExecutor()),
            notifier: DisabledTaskNotifier(),
            resultReporter: reporter
        )
        let controller = ConversationController(
            modelContext: context,
            gateway: DemoModelGateway(),
            taskController: taskController
        )
        let conversationID = UUID()
        let conversation = Conversation(title: "提醒测试")
        conversation.id = conversationID
        context.insert(conversation)
        try context.save()
        controller.selectConversation(conversation)
        let task = try await taskController.prepareTool(
            from: AgentToolRequest(
                id: "call_follow_up",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: conversationID,
            sourceMessageID: nil
        )

        await taskController.confirmTask(taskID: task.id)
        await taskController.flushPendingResultReports()

        let followUps = controller.messages.filter {
            $0.sourceToolCallID == "call_follow_up"
        }
        #expect(followUps.count == 1)
        #expect(followUps.first?.text == "提醒事项已经成功创建。")
    }

    @Test @MainActor
    func runtimeKeepsReminderAtomicWhileExposingInternalSteps() throws {
        let executor = FakeReminderExecutor()
        let runtime = AgentRuntime.testing(reminderExecutor: executor)
        let request = try runtime.prepare(request: AgentToolRequest(
            id: "call_1",
            capability: "reminder.create",
            arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
        ))

        #expect(request.descriptor.capability == "reminder.create")
        #expect(request.descriptor.confirmationPolicy == .always)
        #expect(request.descriptor.supportsRetry == false)
        #expect(request.task.stepTitles.execution == "写入系统提醒事项")
        #expect(request.task.stepTitles.verification == "回读并验证结果")
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
    }

    @Test @MainActor
    func runtimeRejectsUnregisteredCapabilities() {
        let runtime = AgentRuntime.testing(reminderExecutor: FakeReminderExecutor())

        #expect(throws: AgentRuntimeError.self) {
            try runtime.prepare(request: AgentToolRequest(
                id: "call_unsupported",
                capability: "calendar.delete",
                arguments: "{}"
            ))
        }
    }

    @Test @MainActor
    func cancellingPreparedToolDoesNotExecuteIt() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let controller = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_1",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.cancelTask(taskID: task.id)

        #expect(task.status == .cancelled)
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
    }

    @Test @MainActor
    func pendingToolResultRetriesWithoutChangingVerifiedTask() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let reporter = FailOnceToolResultReporter()
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: executor),
            notifier: DisabledTaskNotifier(),
            resultReporter: reporter
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_retry",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.confirmTask(taskID: task.id)
        #expect(task.status == .completed)
        #expect(task.resultReportState == .pending)

        await controller.flushPendingResultReports()
        #expect(task.status == .completed)
        #expect(task.resultReportState == .delivered)
        let attemptCount = await reporter.attemptCount
        #expect(attemptCount == 2)
    }

    @MainActor
    private func makeContainer() throws -> ModelContainer {
        let schema = Schema([
            Conversation.self,
            ChatMessage.self,
            AgentTask.self,
            AgentTaskStep.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return try ModelContainer(for: schema, configurations: [configuration])
    }

}

@MainActor
private final class FakeReminderExecutor: ReminderExecuting {
    private(set) var createCount = 0
    private(set) var verifyCount = 0

    func create(_ draft: ReminderDraft) async throws -> CreatedReminder {
        createCount += 1
        return CreatedReminder(identifier: "test-reminder-id", listTitle: "提醒事项")
    }

    func verify(_ created: CreatedReminder, matches draft: ReminderDraft) throws -> VerifiedReminder {
        verifyCount += 1
        return VerifiedReminder(listTitle: created.listTitle, identifierDigest: "abc123")
    }
}

@MainActor
private final class RecordingTaskNotifier: TaskNotifying {
    private(set) var notifications: [AgentTaskNotification] = []

    func post(_ notification: AgentTaskNotification) async {
        notifications.append(notification)
    }
}

private actor RecordingToolResultReporter: ToolResultReporting {
    struct CapturedReport: Sendable {
        let result: AgentToolResultReport
        let conversationID: UUID
    }

    private(set) var reports: [CapturedReport] = []

    func report(
        _ result: AgentToolResultReport,
        conversationID: UUID
    ) async throws -> ToolResultAcknowledgement {
        reports.append(CapturedReport(result: result, conversationID: conversationID))
        return ToolResultAcknowledgement(
            accepted: true,
            duplicate: false,
            continuationStatus: .unavailable,
            assistantMessage: nil,
            protocolVersion: "1.0"
        )
    }
}

private actor FailOnceToolResultReporter: ToolResultReporting {
    private(set) var attemptCount = 0

    func report(
        _ result: AgentToolResultReport,
        conversationID: UUID
    ) async throws -> ToolResultAcknowledgement {
        attemptCount += 1
        if attemptCount == 1 {
            throw URLError(.notConnectedToInternet)
        }
        return ToolResultAcknowledgement(
            accepted: true,
            duplicate: true,
            continuationStatus: .unavailable,
            assistantMessage: nil,
            protocolVersion: "1.0"
        )
    }
}

private actor ReplyingToolResultReporter: ToolResultReporting {
    func report(
        _ result: AgentToolResultReport,
        conversationID: UUID
    ) async throws -> ToolResultAcknowledgement {
        ToolResultAcknowledgement(
            accepted: true,
            duplicate: false,
            continuationStatus: .completed,
            assistantMessage: "提醒事项已经成功创建。",
            protocolVersion: "1.0"
        )
    }
}

private struct ToolRequestGateway: ModelGateway {
    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        AsyncThrowingStream { continuation in
            continuation.yield(.toolRequest(AgentToolRequest(
                id: "call_inline_card",
                capability: "reminder.create",
                arguments: #"{"title":"带伞","dueAt":"2099-09-11T09:00:00+08:00"}"#
            )))
            continuation.yield(.done)
            continuation.finish()
        }
    }
}
