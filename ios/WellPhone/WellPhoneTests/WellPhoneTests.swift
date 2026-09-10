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
    func qwenStreamDecoderReadsDeltasAndDone() throws {
        let delta = #"data: {"choices":[{"delta":{"content":"你好"}}]}"#

        #expect(try QwenStreamDecoder.decode(line: delta) == .textDelta("你好"))
        #expect(try QwenStreamDecoder.decode(line: "data: [DONE]") == .done)
        #expect(try QwenStreamDecoder.decode(line: ": keep-alive") == .ignored)
    }

    @Test @MainActor
    func qwenStreamDecoderReadsToolCallDelta() throws {
        let line = #"data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"reminder_create","arguments":"{\"title\":"}}]}}]}"#
        #expect(
            try QwenStreamDecoder.decode(line: line) == .toolCallDelta(
                index: 0,
                id: "call_1",
                name: "reminder_create",
                arguments: #"{"title":"#
            )
        )
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
        let controller = TaskController(
            modelContext: context,
            runtime: .testing(reminderExecutor: executor),
            notifier: notifier
        )
        let call = ModelToolCall(
            id: "call_1",
            name: "reminder_create",
            arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
        )

        let task = try await controller.prepareTool(
            from: call,
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        #expect(task.status == .waitingForConfirmation)
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
    }

    @Test @MainActor
    func runtimeKeepsReminderAtomicWhileExposingInternalSteps() throws {
        let executor = FakeReminderExecutor()
        let runtime = AgentRuntime.testing(reminderExecutor: executor)
        let request = try runtime.prepare(call: ModelToolCall(
            id: "call_1",
            name: "reminder_create",
            arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
        ))

        #expect(request.descriptor.modelName == "reminder_create")
        #expect(request.descriptor.capability == "reminder.create")
        #expect(request.descriptor.confirmationPolicy == .always)
        #expect(request.descriptor.supportsRetry == false)
        #expect(request.task.stepTitles.execution == "写入系统提醒事项")
        #expect(request.task.stepTitles.verification == "回读并验证结果")
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
    }

    @Test @MainActor
    func runtimeRejectsUnregisteredModelTools() {
        let runtime = AgentRuntime.testing(reminderExecutor: FakeReminderExecutor())

        #expect(throws: AgentRuntimeError.self) {
            try runtime.prepare(call: ModelToolCall(
                id: "call_unsupported",
                name: "calendar_delete",
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
            from: ModelToolCall(
                id: "call_1",
                name: "reminder_create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        controller.cancelTask(taskID: task.id)

        #expect(task.status == .cancelled)
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
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
