import SwiftUI

struct VoiceConversationView: View {
    @Bindable var session: VoiceSessionController
    let dismiss: () -> Void
    let submit: (String) -> Void

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
        .interactiveDismissDisabled(session.phase == .preparing || session.phase == .finalizing)
        .task {
            await session.start()
        }
        .onDisappear {
            session.cancel()
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
        case .preparing, .finalizing:
            ProgressView()
                .controlSize(.large)
                .frame(height: 56)
        case .listening:
            Button {
                Task { await session.finish() }
            } label: {
                Label("说完了", systemImage: "stop.fill")
                    .frame(maxWidth: .infinity)
                    .frame(height: 52)
            }
            .buttonStyle(.borderedProminent)
        case .ready:
            Button {
                submit(session.transcript)
            } label: {
                Label("发送给 Agent", systemImage: "arrow.up.circle.fill")
                    .frame(maxWidth: .infinity)
                    .frame(height: 52)
            }
            .buttonStyle(.borderedProminent)
            .disabled(!session.canSubmit)
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
        case .finalizing: "正在整理你的指令"
        case .ready: "请确认后发送"
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
        case .listening: "说完后轻点“说完了”"
        case .finalizing: "语音仍在设备端完成最后转写"
        case .ready: "发送前可以检查上面的文字"
        case .failed: "请检查麦克风权限和网络后重试"
        }
    }
}
