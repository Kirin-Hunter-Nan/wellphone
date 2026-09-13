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
    func assistantMarkdownPreservesSelectableVisualHierarchy() {
        let markdown = """
        # 上海商务出差计划

        已完成 4 项固定安排。

        ## 10 月 1 日

        - **14:00–15:00 客户会议**
          上海市浦东新区
        - [ ] 携带会议材料
        """

        let blocks = AssistantMarkdownRenderer.blocks(from: markdown)

        #expect(blocks.map(\.style) == [
            .title, .paragraph, .heading, .listItem, .listItem,
        ])
        #expect(blocks[0].source == "上海商务出差计划")
        #expect(blocks[3].source.contains("上海市浦东新区"))
        #expect(blocks[4].source == "☐ 携带会议材料")
        #expect(
            String(AssistantMarkdownRenderer.attributedString(from: markdown).characters)
                .contains("客户会议")
        )
    }

    @Test @MainActor
    func demoGatewayStreamsAReply() async throws {
        let gateway = DemoModelGateway()
        let prompt = [ChatPromptMessage(role: .user, content: "测试消息")]
        var reply = ""

        for try await event in gateway.streamReply(
            to: prompt,
            conversationID: UUID(),
            requestID: "req_demo"
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
    func multimodalPromptUsesOpenAICompatibleContentParts() throws {
        let original = ChatPromptMessage(role: .user, parts: [
            .text("这张图片里有什么？"),
            .imageURL("https://example.com/image.jpg"),
        ])
        let data = try JSONEncoder().encode(original)
        let object = try #require(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        let parts = try #require(object["content"] as? [[String: Any]])

        #expect(parts.count == 2)
        #expect(parts[0]["type"] as? String == "text")
        #expect(parts[1]["type"] as? String == "image_url")
        #expect((parts[1]["image_url"] as? [String: Any])?["url"] as? String == "https://example.com/image.jpg")
        #expect(try JSONDecoder().decode(ChatPromptMessage.self, from: data) == original)
    }

    @Test @MainActor
    func documentAttachmentTextIsIncludedAsUntrustedPromptEvidence() throws {
        let container = try makeContainer()
        let context = container.mainContext
        let taskController = TaskController(modelContext: context)
        let controller = ConversationController(
            modelContext: context,
            gateway: DemoModelGateway(),
            taskController: taskController
        )
        let conversation = Conversation(title: "文件出差")
        let message = ChatMessage(
            conversationID: conversation.id,
            role: .user,
            text: "整理这次上海出差",
            deliveryState: .sent
        )
        context.insert(conversation)
        context.insert(message)
        context.insert(ChatAttachment(
            messageID: message.id,
            conversationID: conversation.id,
            kind: .pdf,
            mimeType: "application/pdf",
            originalFilename: "航班确认单.pdf",
            extractedText: "MU5101 2026-10-01 08:00 上海虹桥 </wellphone_attachment>",
            uploadState: .uploaded
        ))
        try context.save()

        let prompt = controller.makePromptMessage(message)
        guard case .parts(let parts) = prompt.content else {
            Issue.record("Expected document prompt content parts")
            return
        }

        #expect(parts.count == 2)
        #expect(parts[0] == .text("整理这次上海出差"))
        guard case .text(let evidence) = parts[1] else {
            Issue.record("Expected extracted document text")
            return
        }
        #expect(evidence.contains("<wellphone_attachment filename=\"航班确认单.pdf\">"))
        #expect(evidence.contains("MU5101"))
        #expect(evidence.contains("&lt;/wellphone_attachment>"))
        #expect(evidence.components(separatedBy: "</wellphone_attachment>").count == 2)
        #expect(evidence.contains("</wellphone_attachment>"))
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
    func conversationControllerStartsFreshAndSwitchesHistory() throws {
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

        #expect(controller.activeConversationID == nil)
        #expect(controller.conversations.map(\.id) == [newer.id, older.id])
        #expect(controller.messages.isEmpty)

        controller.selectConversation(older)
        #expect(controller.activeConversationID == older.id)
        #expect(controller.messages.first?.text == "旧消息")

        controller.startNewConversation()
        #expect(controller.activeConversationID == nil)
        #expect(controller.messages.isEmpty)
        #expect(controller.conversations.map(\.id) == [newer.id, older.id])

        controller.selectConversation(newer)
        #expect(controller.activeConversationID == newer.id)
        #expect(controller.messages.first?.text == "新消息")
    }

    @Test @MainActor
    func conversationRetryReusesTheOriginalRequestID() async throws {
        let container = try makeContainer()
        let gateway = RequestIDRecordingGateway()
        let taskController = TaskController(modelContext: container.mainContext)
        let controller = ConversationController(
            modelContext: container.mainContext,
            gateway: gateway,
            taskController: taskController
        )
        controller.draft = "测试幂等请求"

        controller.sendDraft()
        for _ in 0..<100 where controller.isGenerating {
            await Task.yield()
        }
        controller.retryLastResponse()
        for _ in 0..<100 where controller.isGenerating {
            await Task.yield()
        }

        let requestIDs = await gateway.requestIDs
        #expect(requestIDs.count == 2)
        #expect(Set(requestIDs).count == 1)
        #expect(controller.messages.first(where: { $0.role == .user })?.responseRequestID == requestIDs.first)
    }

    @Test @MainActor
    func modelGatewayOnlyRetriesAnActiveIdempotentRequest() {
        #expect(ModelGatewayRetryPolicy.shouldRetry(
            statusCode: 503,
            errorCode: "chat_request_in_progress",
            retryCount: 0
        ))
        #expect(!ModelGatewayRetryPolicy.shouldRetry(
            statusCode: 503,
            errorCode: "database_unavailable",
            retryCount: 0
        ))
        #expect(!ModelGatewayRetryPolicy.shouldRetry(
            statusCode: 503,
            errorCode: "chat_request_in_progress",
            retryCount: ModelGatewayRetryPolicy.maximumInProgressRetries
        ))
        #expect(ModelGatewayRetryPolicy.delaySeconds(retryAfter: "2") == 2)
        #expect(ModelGatewayRetryPolicy.delaySeconds(retryAfter: "30") == 5)
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
        #expect(taskController.task(id: relatedTaskID)?.status == .completed)
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
    func reminderTaskStartsImmediatelyWhenDispatchedAndCompletesAfterVerification() async throws {
        let container = try makeContainer()
        let context = container.mainContext
        let executor = FakeReminderExecutor()
        let notifier = RecordingTaskNotifier()
        let resultReporter = RecordingToolResultReporter()
        let checkpointReporter = RecordingTaskCheckpointReporter()
        let controller = TaskController(
            modelContext: context,
            runtime: .testing(reminderExecutor: executor),
            notifier: notifier,
            resultReporter: resultReporter,
            checkpointReporter: checkpointReporter
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
        #expect(task.status == .created)
        #expect(task.toolCallID == "call_1")
        #expect(executor.createCount == 0)
        #expect(notifier.notifications.isEmpty)

        await controller.startTask(taskID: task.id)

        #expect(executor.createCount == 1)
        #expect(executor.verifyCount == 1)
        #expect(task.status == .completed)
        #expect(task.phase == .completed)
        #expect(controller.steps(for: task).allSatisfy { $0.status == .completed })
        #expect(notifier.notifications.map(\.kind) == [.completed])
        #expect(notifier.notifications.last?.body == task.resultSummary)
        #expect(task.resultReportState == .delivered)
        let reports = await resultReporter.reports
        #expect(reports.count == 1)
        #expect(reports.first?.result.toolCallID == "call_1")
        #expect(reports.first?.result.status == .verified)
        #expect(reports.first?.result.result?.payload?["title"] == .string("提交报销"))
        if case .string(let dueAt) = reports.first?.result.result?.payload?["dueAt"] {
            #expect(!dueAt.isEmpty)
        } else {
            Issue.record("Expected a dueAt value in the generic Tool result payload")
        }
        #expect(
            reports.first?.result.result?.payload?["timeZone"]
                == .string(TimeZone.current.identifier)
        )
        #expect(executor.lastIdempotencyKey == task.id.uuidString.lowercased())

        for _ in 0..<100 {
            if await checkpointReporter.reportCount >= 4 { break }
            await Task.yield()
        }
        let checkpoints = await checkpointReporter.reports.sorted {
            $0.checkpoint.revision < $1.checkpoint.revision
        }
        #expect(checkpoints.map(\.checkpoint.revision) == [1, 2, 3, 4, 5])
        #expect(checkpoints.map(\.checkpoint.phase) == [
            .planning,
            .executing,
            .executing,
            .verifying,
            .completed,
        ])
        #expect(checkpoints.map(\.checkpoint.executionAttemptCount) == [0, 0, 1, 1, 1])
        #expect(task.checkpointReportState == .delivered)
    }

    @Test @MainActor
    func transientReminderExecutionFailureRetriesAndCompletes() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(createErrors: [
            ReminderToolError.transientSystemFailure("系统服务暂时不可用。")
        ])
        let controller = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_transient_retry",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.confirmTask(taskID: task.id)

        #expect(executor.createCount == 2)
        #expect(executor.verifyCount == 1)
        #expect(task.executionAttemptCount == 2)
        #expect(task.lastExecutionErrorMessage == "系统服务暂时不可用。")
        #expect(task.nextExecutionRetryAt == nil)
        #expect(task.status == .completed)
    }

    @Test @MainActor
    func terminalReminderExecutionFailureDoesNotRetry() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(createErrors: [ReminderToolError.accessDenied])
        let controller = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_terminal_failure",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.confirmTask(taskID: task.id)

        #expect(executor.createCount == 1)
        #expect(executor.verifyCount == 0)
        #expect(task.executionAttemptCount == 1)
        #expect(task.status == .failed)
        #expect(task.errorMessage == ReminderToolError.accessDenied.localizedDescription)
    }

    @Test @MainActor
    func transientReminderExecutionStopsAtRetryLimit() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(createErrors: [
            ReminderToolError.transientSystemFailure("系统服务暂时不可用。"),
            ReminderToolError.transientSystemFailure("系统服务暂时不可用。"),
            ReminderToolError.transientSystemFailure("系统服务暂时不可用。"),
        ])
        let controller = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_retry_exhausted",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.confirmTask(taskID: task.id)

        #expect(executor.createCount == 3)
        #expect(executor.verifyCount == 0)
        #expect(task.executionAttemptCount == 3)
        #expect(task.status == .failed)
        #expect(task.errorMessage?.contains("重试上限") == true)
    }

    @Test @MainActor
    func cancellationDuringRetryDelayPreventsAnotherSystemWrite() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(createErrors: [
            ReminderToolError.transientSystemFailure("系统服务暂时不可用。")
        ])
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: executor),
            notifier: DisabledTaskNotifier(),
            executionRetryPolicy: ToolExecutionRetryPolicy(
                maximumAttempts: 3,
                delayNanoseconds: [5_000_000_000]
            )
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_cancel_retry",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        let confirmation = Task { @MainActor in
            await controller.confirmTask(taskID: task.id)
        }
        for _ in 0..<1_000 {
            if task.nextExecutionRetryAt != nil { break }
            await Task.yield()
        }
        #expect(task.nextExecutionRetryAt != nil)

        await controller.cancelTask(taskID: task.id)
        await confirmation.value

        #expect(executor.createCount == 1)
        #expect(executor.verifyCount == 0)
        #expect(task.cancellationRequestedAt != nil)
        #expect(task.status == .cancelled)
        #expect(task.resultSummary?.contains("安全停止") == true)
    }

    @Test @MainActor
    func cancellationAfterSystemWriteStartsStillVerifiesTheResult() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(createDelayNanoseconds: 200_000_000)
        let controller = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_cancel_in_flight",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        let confirmation = Task { @MainActor in
            await controller.confirmTask(taskID: task.id)
        }
        for _ in 0..<1_000 {
            if executor.createCount == 1 { break }
            await Task.yield()
        }
        #expect(executor.createCount == 1)

        await controller.cancelTask(taskID: task.id)
        await confirmation.value

        #expect(executor.createCount == 1)
        #expect(executor.verifyCount == 1)
        #expect(task.cancellationRequestedAt != nil)
        #expect(task.status == .completed)
        #expect(task.resultSummary?.contains("系统写入已经完成") == true)
    }

    @Test @MainActor
    func timedOutExecutionRecoversAndVerifiesAnExistingWrite() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(
            recoveredReminder: CreatedReminder(
                identifier: "recovered-after-timeout",
                listTitle: "提醒事项"
            ),
            createDelayNanoseconds: 1_000_000_000
        )
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: executor),
            notifier: DisabledTaskNotifier(),
            executionRetryPolicy: .immediateTesting,
            executionDeadlinePolicy: ToolExecutionDeadlinePolicy(
                attemptTimeoutNanoseconds: 10_000_000
            )
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_timeout_recover",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.confirmTask(taskID: task.id)

        #expect(executor.createCount == 1)
        #expect(executor.recoverCount == 1)
        #expect(executor.verifyCount == 1)
        #expect(task.executionAttemptCount == 1)
        #expect(task.executionDeadlineAt == nil)
        #expect(task.status == .completed)
    }

    @Test @MainActor
    func appRestartDoesNotReplayAnExpiredExecutionWithoutAReceipt() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_expired_restart",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        task.status = .running
        task.phase = .executing
        task.executionAttemptCount = 1
        task.executionDeadlineAt = Date().addingTimeInterval(-1)
        try container.mainContext.save()

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        await restoredController.recoverInterruptedTasks()

        #expect(executor.recoverCount == 1)
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
        #expect(task.executionDeadlineAt == nil)
        #expect(task.status == .failed)
        #expect(task.errorMessage?.contains("超时") == true)
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
        #expect(request.descriptor.confirmationPolicy == .never)
        #expect(request.descriptor.supportsRetry == true)
        #expect(request.task.stepTitles.execution == "写入系统提醒事项")
        #expect(request.task.stepTitles.verification == "回读并验证结果")
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
    }

    @Test @MainActor
    func travelCalendarImportRequiresExplicitRequestFlag() throws {
        let container = try makeContainer()
        let controller = TaskController(modelContext: container.mainContext)
        let conversationID = UUID()
        let implicit = AgentTask(
            conversationID: conversationID,
            title: "上海旅行",
            capability: "travel.plan",
            executionLocation: .server,
            argumentsJSON: #"{"destination":"上海","addToCalendar":false}"#
        )
        let explicit = AgentTask(
            conversationID: conversationID,
            title: "上海旅行并添加日历",
            capability: "travel.plan",
            executionLocation: .server,
            argumentsJSON: #"{"destination":"上海","addToCalendar":true}"#
        )
        let businessTrip = AgentTask(
            conversationID: conversationID,
            title: "上海商务出差并添加日历",
            capability: "business-trip.plan",
            executionLocation: .server,
            argumentsJSON: #"{"destination":"上海","addToCalendar":true}"#
        )

        #expect(!controller.calendarWasExplicitlyRequested(for: implicit))
        #expect(controller.calendarWasExplicitlyRequested(for: explicit))
        #expect(controller.calendarWasExplicitlyRequested(for: businessTrip))
    }

    @Test @MainActor
    func explicitTravelCalendarRequestImportsBeforeReportingCompletion() async throws {
        let container = try makeContainer()
        let calendarImporter = RecordingTravelCalendarImporter()
        let serverClient = ImmediatelyCompletingTravelServerClient()
        let notifier = RecordingTaskNotifier()
        let resultReporter = RecordingToolResultReporter()
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: FakeReminderExecutor()),
            notifier: notifier,
            resultReporter: resultReporter,
            checkpointReporter: DisabledTaskCheckpointReporter(),
            serverTaskClient: serverClient,
            calendarImporter: calendarImporter
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_travel_calendar",
                capability: "travel.plan",
                arguments: #"{"destination":"上海","startDate":"2026-10-01","endDate":"2026-10-01","addToCalendar":true}"#,
                executionLocation: .server
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.startTask(taskID: task.id)
        for _ in 0..<1_000 {
            if task.status == .completed, calendarImporter.importCount == 1 { break }
            await Task.yield()
        }

        #expect(task.status == .completed)
        #expect(calendarImporter.importCount == 1)
        #expect(calendarImporter.idempotencyKeys == [task.id.uuidString.lowercased()])
        #expect(controller.calendarImportPrompt == nil)
        #expect(task.resultSummary?.contains("已添加到 Apple 日历") == true)
        #expect(notifier.notifications.map(\.kind) == [.completed])
        let calendarArtifact = controller.artifacts(for: task).first {
            $0.contentType == "application/vnd.wellphone.calendar-events+json"
        }
        #expect(calendarArtifact?.storageReference == "eventkit:event-1")
        let repeatedImportSucceeded = await controller.importTravelCalendar(taskID: task.id)
        #expect(repeatedImportSucceeded)
        #expect(calendarImporter.importCount == 1)
        let reports = await resultReporter.reports
        #expect(reports.count == 1)
        #expect(reports.first?.result.status == .verified)

        #expect(await controller.importTravelCalendar(taskID: task.id))
        #expect(calendarImporter.importCount == 1)
    }

    @Test @MainActor
    func requiredTravelCalendarFailureFailsWholeTaskWithoutCompletionNotification() async throws {
        let container = try makeContainer()
        let calendarImporter = RecordingTravelCalendarImporter(
            error: TravelCalendarImportError.verificationFailed
        )
        let notifier = RecordingTaskNotifier()
        let resultReporter = RecordingToolResultReporter()
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: FakeReminderExecutor()),
            notifier: notifier,
            resultReporter: resultReporter,
            checkpointReporter: DisabledTaskCheckpointReporter(),
            serverTaskClient: ImmediatelyCompletingTravelServerClient(),
            calendarImporter: calendarImporter
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_travel_calendar_failure",
                capability: "travel.plan",
                arguments: #"{"destination":"上海","startDate":"2026-10-01","endDate":"2026-10-01","addToCalendar":true}"#,
                executionLocation: .server
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.startTask(taskID: task.id)
        for _ in 0..<1_000 {
            if task.status == .failed, task.resultReportState == .delivered { break }
            await Task.yield()
        }

        #expect(task.status == .failed)
        #expect(task.phase == .failed)
        #expect(task.errorMessage?.contains("无法回读验证") == true)
        #expect(notifier.notifications.isEmpty)
        let reports = await resultReporter.reports
        #expect(reports.count == 1)
        #expect(reports.first?.result.status == .failed)
    }

    @Test @MainActor
    func serverMapKitRequestExecutesOnDeviceAndReturnsWithoutOpeningUI() async throws {
        let container = try makeContainer()
        let serverClient = DeviceToolServerClient()
        let executor = RecordingDeviceToolExecutor()
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: FakeReminderExecutor()),
            notifier: DisabledTaskNotifier(),
            serverTaskClient: serverClient,
            deviceToolExecutor: executor
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_travel_mapkit",
                capability: "travel.plan",
                arguments: #"{"destination":"上海","startDate":"2026-10-01","endDate":"2026-10-01"}"#,
                executionLocation: .server
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        let handled = try await controller.processPendingDeviceTool(taskID: task.id)

        #expect(handled)
        #expect(executor.requests.map(\.toolName) == ["mapkit.local-search"])
        let submissions = await serverClient.recordedSubmissions()
        #expect(submissions.count == 1)
        #expect(submissions.first?.toolCallID == "search_1")
        #expect(submissions.first?.result.result?["verified"] == .bool(true))
    }

    @Test @MainActor
    func mapKitCanonicalSimilarityDistinguishesSpecificBranchesFromGenericNames() {
        let executor = MapKitDeviceToolExecutor()

        #expect(executor.canonicalNameSimilarity(
            query: "上海博物馆 人民广场",
            candidate: "上海博物馆(人民广场馆)"
        ) >= 0.97)
        #expect(executor.canonicalNameSimilarity(
            query: "上海博物馆",
            candidate: "上海博物馆(东馆)"
        ) < 0.97)
        #expect(executor.canonicalNameSimilarity(
            query: "% Arabica 上海店",
            candidate: "%Arabica"
        ) < 0.97)
    }

    @Test @MainActor
    func googleWorkspaceToolFailsClosedWithoutOAuthConfiguration() async {
        let executor = GoogleWorkspaceDeviceToolExecutor(clientIDOverride: "")
        let result = await executor.execute(DeviceToolRequest(
            taskId: UUID(),
            toolCallId: "gmail_1",
            toolName: "google.gmail.search",
            arguments: [
                "query": .string("after:2026/09/01 上海 出差"),
                "maxResults": .number(12),
            ]
        ))

        #expect(result.result == nil)
        #expect(result.failure?.code == "google_oauth_not_configured")
    }

    @Test @MainActor
    func repeatedToolRequestReusesTheExistingTask() async throws {
        let container = try makeContainer()
        let controller = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: FakeReminderExecutor()
        )
        let conversationID = UUID()
        let request = AgentToolRequest(
            id: "call_replayed",
            capability: "reminder.create",
            arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
        )

        let first = try await controller.prepareTool(
            from: request,
            conversationID: conversationID,
            sourceMessageID: UUID()
        )
        let replay = try await controller.prepareTool(
            from: request,
            conversationID: conversationID,
            sourceMessageID: UUID()
        )

        #expect(first.id == replay.id)
        #expect(controller.tasks.filter { $0.toolCallID == "call_replayed" }.count == 1)
        #expect(controller.steps(for: first).count == 4)
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

    @Test @MainActor
    func pendingTaskCheckpointRetriesWithoutBlockingLocalExecution() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let checkpointReporter = ToggleTaskCheckpointReporter(shouldFail: true)
        let controller = TaskController(
            modelContext: container.mainContext,
            runtime: .testing(reminderExecutor: executor),
            notifier: DisabledTaskNotifier(),
            resultReporter: DisabledToolResultReporter(),
            checkpointReporter: checkpointReporter
        )
        let task = try await controller.prepareTool(
            from: AgentToolRequest(
                id: "call_checkpoint_retry",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        await controller.confirmTask(taskID: task.id)
        await controller.flushPendingCheckpoints()
        #expect(task.status == .completed)
        #expect(executor.createCount == 1)
        #expect(task.checkpointRevision == 5)
        #expect(task.checkpointReportState == .pending)

        await checkpointReporter.setShouldFail(false)
        await controller.flushPendingCheckpoints()
        #expect(task.status == .completed)
        #expect(task.checkpointReportState == .delivered)
        let latestRevision = await checkpointReporter.latestRevision
        #expect(latestRevision == 5)
    }

    @Test @MainActor
    func appRestartResumesVerificationFromPersistedExecutionReceipt() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_resume_verification",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        task.executionReceiptData = try JSONEncoder().encode(CreatedReminder(
            identifier: "persisted-reminder-id",
            listTitle: "提醒事项"
        ))
        task.status = .running
        task.phase = .verifying
        task.progress = 0.82
        for step in initialController.steps(for: task) {
            switch step.sequence {
            case 1, 2:
                step.status = .completed
            case 3:
                step.status = .running
            default:
                break
            }
        }
        try container.mainContext.save()

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        await restoredController.recoverInterruptedTasks()

        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 1)
        #expect(task.status == .completed)
        #expect(task.phase == .completed)
        #expect(restoredController.steps(for: task).allSatisfy { $0.status == .completed })
    }

    @Test @MainActor
    func appRestartKeepsPreparedTaskReadyToStart() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_waiting_restart",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        await restoredController.recoverInterruptedTasks()

        #expect(task.status == .created)
        #expect(task.phase == .planning)
        #expect(executor.recoverCount == 0)
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 0)
        #expect(restoredController.steps(for: task)[1].status == .completed)
    }

    @Test @MainActor
    func appRestartRecoversExistingReminderWithoutRepeatingExecution() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(recoveredReminder: CreatedReminder(
            identifier: "recovered-reminder-id",
            listTitle: "提醒事项"
        ))
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_unknown_execution",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        task.status = .running
        task.phase = .executing
        task.progress = 0.55
        for step in initialController.steps(for: task) {
            if step.sequence == 1 {
                step.status = .completed
            } else if step.sequence == 2 {
                step.status = .running
            }
        }
        try container.mainContext.save()

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        await restoredController.recoverInterruptedTasks()

        #expect(executor.createCount == 0)
        #expect(executor.recoverCount == 1)
        #expect(executor.verifyCount == 1)
        #expect(task.status == .completed)
        #expect(task.phase == .completed)
        #expect(task.resultReportState == .pending)

        await restoredController.flushPendingResultReports()
        #expect(task.resultReportState == .delivered)
    }

    @Test @MainActor
    func concurrentRecoveryCallsDoNotRecoverTheSameTaskTwice() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor(
            recoveredReminder: CreatedReminder(
                identifier: "recovered-once",
                listTitle: "提醒事项"
            ),
            recoverDelayNanoseconds: 100_000_000
        )
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_concurrent_recovery",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        task.status = .running
        task.phase = .executing
        task.progress = 0.55
        try container.mainContext.save()

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        async let first: Void = restoredController.recoverInterruptedTasks()
        async let second: Void = restoredController.recoverInterruptedTasks()
        _ = await (first, second)

        #expect(executor.recoverCount == 1)
        #expect(executor.createCount == 0)
        #expect(executor.verifyCount == 1)
        #expect(task.status == .completed)
    }

    @Test @MainActor
    func appRestartRetriesIdempotentExecutionWhenNoReminderExists() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_retry_idempotent_execution",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        task.status = .running
        task.phase = .executing
        task.progress = 0.55
        try container.mainContext.save()

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        await restoredController.recoverInterruptedTasks()

        #expect(executor.recoverCount == 1)
        #expect(executor.createCount == 1)
        #expect(executor.verifyCount == 1)
        #expect(executor.lastIdempotencyKey == task.id.uuidString.lowercased())
        #expect(task.status == .completed)
        #expect(task.phase == .completed)
    }

    @Test @MainActor
    func appRestartDoesNotExceedPersistedExecutionRetryLimit() async throws {
        let container = try makeContainer()
        let executor = FakeReminderExecutor()
        let initialController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        let task = try await initialController.prepareTool(
            from: AgentToolRequest(
                id: "call_exhausted_after_restart",
                capability: "reminder.create",
                arguments: #"{"title":"提交报销","dueAt":"2099-09-11T15:00:00+08:00"}"#
            ),
            conversationID: UUID(),
            sourceMessageID: UUID()
        )
        task.status = .running
        task.phase = .executing
        task.executionAttemptCount = 3
        task.lastExecutionErrorMessage = "系统服务暂时不可用。"
        try container.mainContext.save()

        let restoredController = TaskController(
            modelContext: container.mainContext,
            reminderExecutor: executor
        )
        await restoredController.recoverInterruptedTasks()

        #expect(executor.recoverCount == 1)
        #expect(executor.createCount == 0)
        #expect(task.executionAttemptCount == 3)
        #expect(task.status == .failed)
        #expect(task.errorMessage?.contains("已达到重试上限") == true)
    }

    @MainActor
    private func makeContainer() throws -> ModelContainer {
        let schema = Schema([
            Conversation.self,
            ChatMessage.self,
            ChatAttachment.self,
            AgentTask.self,
            AgentTaskStep.self,
            TaskArtifact.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return try ModelContainer(for: schema, configurations: [configuration])
    }

}

@MainActor
private final class FakeReminderExecutor: ReminderExecuting {
    private(set) var createCount = 0
    private(set) var recoverCount = 0
    private(set) var verifyCount = 0
    private(set) var lastIdempotencyKey: String?
    private let recoveredReminder: CreatedReminder?
    private var createErrors: [any Error]
    private let createDelayNanoseconds: UInt64
    private let recoverDelayNanoseconds: UInt64

    init(
        recoveredReminder: CreatedReminder? = nil,
        createErrors: [any Error] = [],
        createDelayNanoseconds: UInt64 = 0,
        recoverDelayNanoseconds: UInt64 = 0
    ) {
        self.recoveredReminder = recoveredReminder
        self.createErrors = createErrors
        self.createDelayNanoseconds = createDelayNanoseconds
        self.recoverDelayNanoseconds = recoverDelayNanoseconds
    }

    func create(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder {
        createCount += 1
        lastIdempotencyKey = idempotencyKey
        if createDelayNanoseconds > 0 {
            try await Task<Never, Never>.sleep(nanoseconds: createDelayNanoseconds)
        }
        if !createErrors.isEmpty {
            throw createErrors.removeFirst()
        }
        return CreatedReminder(identifier: "test-reminder-id", listTitle: "提醒事项")
    }

    func recover(
        _ draft: ReminderDraft,
        idempotencyKey: String
    ) async throws -> CreatedReminder? {
        recoverCount += 1
        lastIdempotencyKey = idempotencyKey
        if recoverDelayNanoseconds > 0 {
            try await Task<Never, Never>.sleep(nanoseconds: recoverDelayNanoseconds)
        }
        return recoveredReminder
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

private actor RecordingTaskCheckpointReporter: TaskCheckpointReporting {
    struct CapturedReport: Sendable {
        let checkpoint: TaskCheckpointReport
        let conversationID: UUID
    }

    private(set) var reports: [CapturedReport] = []

    var reportCount: Int { reports.count }

    func report(
        _ checkpoint: TaskCheckpointReport,
        conversationID: UUID
    ) async throws -> TaskCheckpointAcknowledgement {
        let revision = await checkpoint.revision
        reports.append(CapturedReport(
            checkpoint: checkpoint,
            conversationID: conversationID
        ))
        return TaskCheckpointAcknowledgement(
            accepted: true,
            duplicate: false,
            applied: true,
            currentRevision: revision,
            gap: false,
            protocolVersion: "1.0"
        )
    }
}

private actor ToggleTaskCheckpointReporter: TaskCheckpointReporting {
    private var shouldFail: Bool
    private(set) var latestRevision = 0

    init(shouldFail: Bool) {
        self.shouldFail = shouldFail
    }

    func setShouldFail(_ shouldFail: Bool) {
        self.shouldFail = shouldFail
    }

    func report(
        _ checkpoint: TaskCheckpointReport,
        conversationID: UUID
    ) async throws -> TaskCheckpointAcknowledgement {
        let revision = await checkpoint.revision
        latestRevision = max(latestRevision, revision)
        if shouldFail {
            throw URLError(.notConnectedToInternet)
        }
        return TaskCheckpointAcknowledgement(
            accepted: true,
            duplicate: false,
            applied: true,
            currentRevision: revision,
            gap: false,
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

@MainActor
private final class RecordingTravelCalendarImporter: TravelCalendarImporting {
    private(set) var importCount = 0
    private(set) var idempotencyKeys: [String] = []
    private let error: (any Error)?

    init(error: (any Error)? = nil) {
        self.error = error
    }

    func importEvents(payload: Data, idempotencyKey: String) async throws -> [String] {
        _ = payload
        importCount += 1
        idempotencyKeys.append(idempotencyKey)
        if let error { throw error }
        return ["event-1"]
    }
}

private actor ImmediatelyCompletingTravelServerClient: ServerTaskServing {
    private let taskID = UUID()
    private var conversationID = UUID()
    private var title = "规划上海旅行"

    func create(
        conversationID: UUID,
        toolCallID: String,
        capability: String,
        title: String,
        input: [String: JSONValue]
    ) async throws -> ServerTaskSnapshot {
        _ = toolCallID
        _ = input
        self.conversationID = conversationID
        self.title = title
        return snapshot(
            capability: capability,
            status: "queued",
            phase: "queued",
            progress: 0,
            detail: "等待执行",
            resultSummary: nil,
            artifacts: []
        )
    }

    func confirm(taskID: UUID) async throws -> ServerTaskSnapshot {
        _ = taskID
        return snapshot(
            capability: "travel.plan",
            status: "queued",
            phase: "queued",
            progress: 0,
            detail: "等待执行",
            resultSummary: nil,
            artifacts: []
        )
    }

    func cancel(taskID: UUID) async throws -> ServerTaskSnapshot {
        _ = taskID
        return snapshot(
            capability: "travel.plan",
            status: "cancelled",
            phase: "cancelled",
            progress: 0,
            detail: "任务已取消",
            resultSummary: nil,
            artifacts: []
        )
    }

    func get(taskID: UUID) async throws -> ServerTaskSnapshot {
        _ = taskID
        return snapshot(
            capability: "travel.plan",
            status: "completed",
            phase: "completed",
            progress: 1,
            detail: "上海一日旅行已完成。",
            resultSummary: "上海一日旅行已完成。",
            artifacts: [ServerTaskSnapshot.Artifact(
                id: UUID(),
                kind: "json",
                title: "上海一日旅行（日历事件）",
                contentType: "application/vnd.wellphone.calendar-events+json",
                payload: .object(["events": .array([])]),
                storageReference: nil,
                createdAt: Date()
            )]
        )
    }

    private func snapshot(
        capability: String,
        status: String,
        phase: String,
        progress: Double,
        detail: String,
        resultSummary: String?,
        artifacts: [ServerTaskSnapshot.Artifact]
    ) -> ServerTaskSnapshot {
        ServerTaskSnapshot(
            id: taskID,
            conversationId: conversationID,
            capability: capability,
            title: title,
            status: status,
            phase: phase,
            progress: progress,
            detail: detail,
            resultSummary: resultSummary,
            errorMessage: nil,
            attemptCount: status == "completed" ? 1 : 0,
            updatedAt: Date(),
            steps: [],
            artifacts: artifacts
        )
    }
}

@MainActor
private final class RecordingDeviceToolExecutor: DeviceToolExecuting {
    private(set) var requests: [DeviceToolRequest] = []

    func execute(_ request: DeviceToolRequest) async -> DeviceToolExecutionResult {
        requests.append(request)
        return .completed([
            "name": .string("上海博物馆（人民广场馆）"),
            "formatted_address": .string("上海市黄浦区人民大道201号"),
            "latitude": .number(31.2304),
            "longitude": .number(121.4737),
            "map_url": .string("https://maps.apple.com/place?place-id=test"),
            "place_id": .string("test"),
            "verified": .bool(true),
            "source": .string("mapkit-native"),
        ])
    }
}

private actor DeviceToolServerClient: ServerTaskServing {
    struct Submission: Sendable {
        let toolCallID: String
        let result: DeviceToolExecutionResult
    }

    private let taskID = UUID()
    private var conversationID = UUID()
    private var submissions: [Submission] = []

    func create(
        conversationID: UUID,
        toolCallID: String,
        capability: String,
        title: String,
        input: [String: JSONValue]
    ) async throws -> ServerTaskSnapshot {
        _ = toolCallID
        _ = input
        self.conversationID = conversationID
        return snapshot(capability: capability, title: title)
    }

    func confirm(taskID: UUID) async throws -> ServerTaskSnapshot {
        _ = taskID
        return snapshot(capability: "travel.plan", title: "规划上海旅行")
    }

    func cancel(taskID: UUID) async throws -> ServerTaskSnapshot {
        _ = taskID
        return snapshot(
            capability: "travel.plan",
            title: "规划上海旅行",
            status: "cancelled",
            phase: "cancelled"
        )
    }

    func get(taskID: UUID) async throws -> ServerTaskSnapshot {
        _ = taskID
        return snapshot(capability: "travel.plan", title: "规划上海旅行")
    }

    func pendingDeviceTool(taskID: UUID) async throws -> DeviceToolRequest? {
        DeviceToolRequest(
            taskId: taskID,
            toolCallId: "search_1",
            toolName: "mapkit.local-search",
            arguments: [
                "query": .string("上海博物馆"),
                "destination": .string("上海"),
                "language": .string("zh-CN"),
            ]
        )
    }

    func submitDeviceToolResult(
        taskID: UUID,
        toolCallID: String,
        result: DeviceToolExecutionResult
    ) async throws {
        _ = taskID
        submissions.append(Submission(toolCallID: toolCallID, result: result))
    }

    func recordedSubmissions() -> [Submission] { submissions }

    private func snapshot(
        capability: String,
        title: String,
        status: String = "running",
        phase: String = "usingTools"
    ) -> ServerTaskSnapshot {
        ServerTaskSnapshot(
            id: taskID,
            conversationId: conversationID,
            capability: capability,
            title: title,
            status: status,
            phase: phase,
            progress: 0.5,
            detail: "正在核对地点",
            resultSummary: nil,
            errorMessage: nil,
            attemptCount: 1,
            updatedAt: Date(),
            steps: [],
            artifacts: []
        )
    }
}

private struct ToolRequestGateway: ModelGateway {
    func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID,
        requestID: String
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        _ = requestID
        return AsyncThrowingStream { continuation in
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

private actor RequestIDRecordingGateway: ModelGateway {
    private(set) var requestIDs: [String] = []

    nonisolated func streamReply(
        to messages: [ChatPromptMessage],
        conversationID: UUID,
        requestID: String
    ) -> AsyncThrowingStream<ModelGatewayEvent, any Error> {
        _ = messages
        _ = conversationID
        return AsyncThrowingStream { continuation in
            Task {
                await self.record(requestID)
                continuation.yield(.textDelta("收到"))
                continuation.yield(.done)
                continuation.finish()
            }
        }
    }

    private func record(_ requestID: String) {
        requestIDs.append(requestID)
    }
}
