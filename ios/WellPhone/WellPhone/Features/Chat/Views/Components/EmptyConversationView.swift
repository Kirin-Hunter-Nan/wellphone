import SwiftUI

struct EmptyConversationView: View {
    var body: some View {
        ContentUnavailableView {
            Label("开始一段对话", systemImage: "bubble.left.and.bubble.right")
        } description: {
            Text("消息会保存在这台设备上。你可以随时停止生成或重试回复。")
        }
    }
}
