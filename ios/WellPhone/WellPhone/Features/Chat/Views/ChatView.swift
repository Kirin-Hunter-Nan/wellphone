import SwiftUI

struct ChatView: View {
    @Environment(ConversationController.self) private var controller
    @Environment(TaskController.self) private var taskController
    @Environment(VoiceActivationStore.self) private var voiceActivationStore
    @FocusState private var isComposerFocused: Bool
    @State private var isSidebarPresented = false
    @State private var isTaskCenterPresented = false
    @State private var voiceSession = VoiceSessionController()
    @State private var preparedVoiceActivationID: UUID?

    var body: some View {
        @Bindable var controller = controller

        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                NavigationStack {
                    VStack(spacing: 0) {
                        conversation
                        ChatComposer(
                            isFocused: $isComposerFocused,
                            startVoiceConversation: {
                                isComposerFocused = false
                                voiceActivationStore.requestActivation(source: .composer)
                            }
                        )
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
            .overlay(alignment: .top) {
                if let banner = taskController.completionBanner {
                    TaskCompletionBannerView(banner: banner)
                        .padding(.horizontal, 16)
                        .padding(.top, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .zIndex(10)
                }
            }
            .animation(.snappy, value: taskController.completionBanner?.id)
            .fullScreenCover(
                isPresented: Binding(
                    get: { voiceActivationStore.isVoiceConversationPresented },
                    set: { _ in }
                )
            ) {
                VoiceConversationView(
                    session: voiceSession,
                    dismiss: {
                        voiceActivationStore.dismissActivation()
                    },
                    submit: { transcript in
                        voiceSession.cancel()
                        controller.draft = transcript
                        voiceActivationStore.dismissActivation()
                        controller.sendDraft()
                    }
                )
                .onAppear {
                    voiceActivationStore.markPresented()
                }
            }
            .onChange(of: voiceActivationStore.activationID, initial: true) { _, activationID in
                guard let activationID,
                      preparedVoiceActivationID != activationID else { return }
                preparedVoiceActivationID = activationID
                if voiceActivationStore.source == .externalWake {
                    controller.startNewConversation()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .agentTaskNotificationOpened)) { _ in
                isComposerFocused = false
                isSidebarPresented = false
                isTaskCenterPresented = true
            }
            .alert(
                "是否添加到 Apple 日历？",
                isPresented: Binding(
                    get: { taskController.calendarImportPrompt != nil },
                    set: { presented in
                        if !presented { taskController.dismissCalendarImportPrompt() }
                    }
                )
            ) {
                Button("暂不", role: .cancel) {
                    taskController.dismissCalendarImportPrompt()
                }
                if let prompt = taskController.calendarImportPrompt {
                    Button("添加到日历") {
                        Task { await taskController.importTravelCalendar(taskID: prompt.taskID) }
                    }
                }
            } message: {
                if let prompt = taskController.calendarImportPrompt {
                    Text("“\(prompt.title)”已经生成。你可以将每天的安排写入系统日历。")
                }
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

}
