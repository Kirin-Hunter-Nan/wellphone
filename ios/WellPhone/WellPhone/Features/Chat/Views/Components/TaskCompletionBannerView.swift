import SwiftUI

struct TaskCompletionBannerView: View {
    let banner: TaskController.CompletionBanner

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.title2)
                .foregroundStyle(.green)

            VStack(alignment: .leading, spacing: 2) {
                Text("任务完成")
                    .font(.headline)
                Text(banner.summary)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Spacer(minLength: 4)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        .shadow(color: .black.opacity(0.14), radius: 12, y: 5)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("任务完成：\(banner.title)。\(banner.summary)")
    }
}
