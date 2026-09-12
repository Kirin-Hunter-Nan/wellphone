import SwiftUI

extension AgentTask {
    var displayedProgress: Double {
        status == .completed ? 1 : progress ?? 0
    }

    var progressLabel: String {
        switch status {
        case .cancelled: "已取消"
        case .failed: "执行失败"
        default: "进度 \(Int(displayedProgress * 100))%"
        }
    }
}

extension AgentTaskStatus {
    var title: String {
        switch self {
        case .created: "已创建"
        case .running: "运行中"
        case .waitingForConfirmation: "等待确认"
        case .completed: "已完成"
        case .failed: "失败"
        case .cancelled: "已取消"
        }
    }

    var tint: Color {
        switch self {
        case .created, .running: .accentColor
        case .waitingForConfirmation: .orange
        case .completed: .green
        case .failed: .red
        case .cancelled: .secondary
        }
    }
}

extension AgentTaskStepStatus {
    var icon: String {
        switch self {
        case .pending: "circle"
        case .running: "arrow.trianglehead.2.clockwise.rotate.90"
        case .completed: "checkmark.circle.fill"
        case .failed: "exclamationmark.circle.fill"
        case .cancelled: "xmark.circle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .pending, .cancelled: .secondary
        case .running: .accentColor
        case .completed: .green
        case .failed: .red
        }
    }
}

extension TaskArtifactKind {
    var icon: String {
        switch self {
        case .itinerary: "map"
        case .json: "calendar"
        case .text: "doc.text"
        case .image: "photo"
        case .pdf: "doc.richtext"
        case .map: "map.fill"
        }
    }
}

extension ToolResultReportState {
    var title: String {
        switch self {
        case .pending: "等待同步"
        case .delivered: "已同步"
        case .conflict: "结果冲突"
        }
    }
}
