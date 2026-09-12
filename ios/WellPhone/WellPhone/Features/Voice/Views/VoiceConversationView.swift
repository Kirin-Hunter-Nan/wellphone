import SwiftUI

struct VoiceConversationView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Bindable var session: VoiceSessionController
    let dismiss: () -> Void
    let submit: (String) -> Void
    @State private var didAutoSubmit = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 28) {
                Spacer()

                Image(systemName: session.phase == .listening
                      ? "waveform.circle.fill"
                      : "waveform.circle")
                    .font(.system(size: 88))
                    .foregroundStyle(session.phase == .listening ? Color.accentColor : .secondary)
                    .symbolEffect(.pulse, isActive: session.phase == .listening)
                    .accessibilityHidden(true)

                VStack(spacing: 8) {
                    Text(statusTitle)
                        .font(.title2.bold())
                    Text(statusDetail)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }

                transcriptView

                Spacer()

                controls
            }
            .padding(24)
            .navigationTitle("语音对话")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") {
                        session.cancel()
                        dismiss()
                    }
                }
            }
        }
        .interactiveDismissDisabled()
        .task {
            didAutoSubmit = false
            await session.start()
        }
        .onChange(of: session.phase) { _, phase in
            guard phase == .ready, !didAutoSubmit, session.canSubmit else { return }
            didAutoSubmit = true
            submit(session.transcript)
        }
        .onChange(of: scenePhase) { _, newPhase in
            switch newPhase {
            case .active:
                Task { await session.resumeAfterBackground() }
            case .inactive, .background:
                session.pauseForBackground()
            @unknown default:
                break
            }
        }
    }

    @ViewBuilder
    private var transcriptView: some View {
        if session.transcript.isEmpty {
            Text("你的语音会在这里实时转成文字")
                .foregroundStyle(.tertiary)
                .frame(maxWidth: .infinity, minHeight: 120, alignment: .topLeading)
                .padding(16)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 16))
        } else {
            ScrollView {
                Text(session.transcript)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, minHeight: 120, maxHeight: 240)
            .padding(16)
            .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 16))
        }
    }

    @ViewBuilder
    private var controls: some View {
        switch session.phase {
        case .preparing, .prompting, .finalizing, .ready:
            ProgressView()
                .controlSize(.large)
                .frame(height: 56)
        case .paused:
            Button("继续聆听") {
                Task { await session.resumeAfterBackground() }
            }
            .buttonStyle(.borderedProminent)
            .frame(height: 52)
        case .listening:
            Label("检测到你说完后将自动发送", systemImage: "waveform")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .frame(height: 52)
        case .failed, .idle:
            Button("重新开始") {
                Task { await session.start() }
            }
            .buttonStyle(.borderedProminent)
            .frame(height: 52)
        }
    }

    private var statusTitle: String {
        switch session.phase {
        case .idle: "准备开始"
        case .preparing: "正在准备语音识别"
        case .listening: "我在听"
        case .prompting: "请告诉我你的指令"
        case .paused: "语音输入已暂停"
        case .finalizing: "正在整理你的指令"
        case .ready: "正在发送"
        case .failed: "无法开始语音对话"
        }
    }

    private var statusDetail: String {
        if let errorMessage = session.errorMessage {
            return errorMessage
        }
        return switch session.phase {
        case .idle: "轻点开始后说出你的任务"
        case .preparing: "首次使用可能需要下载本地语言模型"
        case .listening: "自然说话即可，停顿后会自动发送"
        case .prompting: "没有听到声音，我会继续等待"
        case .paused: "返回前台后会自动继续，也可以轻点下方按钮"
        case .finalizing: "语音仍在设备端完成最后转写"
        case .ready: "正在将语音消息交给 Agent"
        case .failed: "请检查麦克风权限和网络后重试"
        }
    }
}
