import SwiftUI

struct ChatView: View {
    @Environment(ConversationController.self) private var controller
    @Environment(TaskController.self) private var taskController
    @FocusState private var isComposerFocused: Bool
    @State private var isSidebarPresented = false
    @State private var isTaskCenterPresented = false

    var body: some View {
        @Bindable var controller = controller

        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                NavigationStack {
                    VStack(spacing: 0) {
                        conversation
                        composer(draft: $controller.draft)
                    }
                    .navigationTitle("WellPhone")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarLeading) {
                            Button {
                                openSidebar()
                            } label: {
                                Image(systemName: "sidebar.left")
                                    .overlay(alignment: .topTrailing) {
                                        if !taskController.activeTasks.isEmpty {
                                            Circle()
                                                .fill(Color.red)
                                                .frame(width: 7, height: 7)
                                                .offset(x: 4, y: -3)
                                        }
                                    }
                            }
                            .accessibilityLabel("打开侧边栏")
                        }
                    }
                    .navigationDestination(isPresented: $isTaskCenterPresented) {
                        TaskCenterView()
                    }
                }
                .disabled(isSidebarPresented)
                .simultaneousGesture(sidebarRevealGesture)

                if isSidebarPresented {
                    Color.black.opacity(0.25)
                        .ignoresSafeArea()
                        .onTapGesture {
                            closeSidebar()
                        }

                    AppSidebar(
                        openTasks: {
                            closeSidebar()
                            isTaskCenterPresented = true
                        },
                        startNewConversation: {
                            controller.startNewConversation()
                            closeSidebar()
                            isComposerFocused = true
                        },
                        selectConversation: { conversation in
                            controller.selectConversation(conversation)
                            closeSidebar()
                        }
                    )
                    .frame(width: min(300, geometry.size.width * 0.78))
                    .transition(.move(edge: .leading))
                    .shadow(color: .black.opacity(0.2), radius: 16, x: 5)
                }
            }
            .animation(.snappy, value: isSidebarPresented)
            .onReceive(NotificationCenter.default.publisher(for: .agentTaskNotificationOpened)) { _ in
                isComposerFocused = false
                isSidebarPresented = false
                isTaskCenterPresented = true
            }
        }
    }

    private func closeSidebar() {
        isComposerFocused = false
        withAnimation(.snappy) {
            isSidebarPresented = false
        }
    }

    private func openSidebar() {
        isComposerFocused = false
        withAnimation(.snappy) {
            isSidebarPresented = true
        }
    }

    private var sidebarRevealGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .local)
            .onEnded { value in
                guard !isSidebarPresented, !isTaskCenterPresented else { return }

                let horizontalDistance = value.translation.width
                let verticalDistance = abs(value.translation.height)
                guard horizontalDistance >= 80,
                      horizontalDistance > verticalDistance * 1.25 else { return }

                openSidebar()
            }
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 16) {
                    if controller.messages.isEmpty {
                        EmptyConversationView()
                            .padding(.top, 72)
                    }

                    ForEach(controller.messages) { message in
                        ChatMessageRow(message: message) {
                            controller.retryLastResponse()
                        }
                        .id(message.id)
                    }

                    if let errorMessage = controller.errorMessage {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(.red)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 20)
            }
            .scrollDismissesKeyboard(.interactively)
            .accessibilityIdentifier("chat.conversation")
            .contentShape(Rectangle())
            .onTapGesture {
                isComposerFocused = false
            }
            .onChange(of: controller.messages.last?.text) {
                guard let lastMessageID = controller.messages.last?.id else { return }
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(lastMessageID, anchor: .bottom)
                }
            }
        }
    }

    private func composer(draft: Binding<String>) -> some View {
        let canSend = !controller.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty

        return HStack(alignment: .bottom, spacing: 8) {
            TextField("给 WellPhone 发消息…", text: draft, axis: .vertical)
                .lineLimit(1...6)
                .textFieldStyle(.plain)
                .accessibilityIdentifier("chat.composer")
                .font(.body)
                .padding(.leading, 10)
                .padding(.vertical, 9)
                .focused($isComposerFocused)
                .submitLabel(.send)
                .onSubmit {
                    guard canSend, !controller.isGenerating else { return }
                    controller.sendDraft()
                }

            Group {
                if controller.isGenerating {
                    Button {
                        controller.stopGenerating()
                    } label: {
                        Image(systemName: "stop.fill")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white)
                            .frame(width: 36, height: 36)
                            .background(Color.primary, in: Circle())
                    }
                    .accessibilityLabel("停止生成")
                } else {
                    Button {
                        controller.sendDraft()
                    } label: {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(canSend ? Color.white : Color.secondary)
                            .frame(width: 36, height: 36)
                            .background(canSend ? Color.accentColor : Color.secondary.opacity(0.12), in: Circle())
                    }
                    .disabled(!canSend)
                    .accessibilityLabel("发送")
                }
            }
            .buttonStyle(.plain)
        }
        .padding(6)
        .background(
            Color(uiColor: .secondarySystemBackground),
            in: RoundedRectangle(cornerRadius: 24, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 0.5)
        }
        .shadow(color: .black.opacity(0.06), radius: 10, y: 3)
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .background(.bar)
    }
}

private struct ChatMessageRow: View {
    @Environment(TaskController.self) private var taskController
    @Bindable var message: ChatMessage
    let retry: () -> Void

    var body: some View {
        if let taskID = message.relatedTaskID,
           let task = taskController.task(id: taskID) {
            ReminderTaskCard(task: task)
        } else {
            MessageBubble(message: message, retry: retry)
        }
    }
}

private struct ReminderTaskCard: View {
    @Environment(TaskController.self) private var controller
    @Bindable var task: AgentTask

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: statusIcon)
                    .foregroundStyle(statusColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("提醒事项")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(task.title)
                        .font(.headline)
                }
                Spacer()
            }

            if let scheduledAt = task.scheduledAt {
                Label {
                    Text(scheduledAt, format: .dateTime.year().month().day().weekday().hour().minute())
                } icon: {
                    Image(systemName: "calendar.badge.clock")
                }
                .font(.subheadline)
            }

            if let detail = task.detail {
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            if task.status == .waitingForConfirmation {
                Text("确认后才会请求系统权限并写入提醒事项。")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack {
                    Button("取消", role: .cancel) {
                        Task { await controller.cancelTask(taskID: task.id) }
                    }
                    .buttonStyle(.bordered)

                    Spacer()

                    Button("确认创建") {
                        Task { await controller.confirmTask(taskID: task.id) }
                    }
                    .buttonStyle(.borderedProminent)
                }
            } else if task.status.isActive {
                ProgressView(task.phase.title)
                    .font(.subheadline)
            } else if let result = task.resultSummary {
                Text(result)
                    .font(.footnote)
                    .foregroundStyle(task.status == .completed ? .green : .secondary)
            } else if let error = task.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .padding(16)
        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.trailing, 48)
    }

    private var statusIcon: String {
        switch task.status {
        case .waitingForConfirmation: "bell.badge"
        case .created, .running: "clock.arrow.trianglehead.counterclockwise.rotate.90"
        case .completed: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        case .cancelled: "xmark.circle"
        }
    }

    private var statusColor: Color {
        switch task.status {
        case .waitingForConfirmation: .orange
        case .created, .running: .accentColor
        case .completed: .green
        case .failed: .red
        case .cancelled: .secondary
        }
    }
}

private struct EmptyConversationView: View {
    var body: some View {
        ContentUnavailableView {
            Label("开始一段对话", systemImage: "bubble.left.and.bubble.right")
        } description: {
            Text("消息会保存在这台设备上。你可以随时停止生成或重试回复。")
        }
    }
}

private struct MessageBubble: View {
    @Bindable var message: ChatMessage
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .bottom) {
            if message.role == .user {
                Spacer(minLength: 48)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                Text(message.text.isEmpty ? " " : message.text)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .foregroundStyle(message.role == .user ? Color.white : Color.primary)
                    .background(
                        message.role == .user ? Color.accentColor : Color.secondary.opacity(0.12),
                        in: RoundedRectangle(cornerRadius: 18, style: .continuous)
                    )
                    .overlay(alignment: .leading) {
                        if message.deliveryState == .streaming && message.text.isEmpty {
                            ProgressView()
                                .controlSize(.small)
                                .padding(.horizontal, 14)
                        }
                    }

                if message.role == .assistant,
                   [.failed, .stopped].contains(message.deliveryState) {
                    Button(message.deliveryState == .stopped ? "已停止 · 重试" : "重试") {
                        retry()
                    }
                    .font(.caption)
                }
            }

            if message.role == .assistant {
                Spacer(minLength: 48)
            }
        }
        .frame(maxWidth: .infinity)
    }
}
