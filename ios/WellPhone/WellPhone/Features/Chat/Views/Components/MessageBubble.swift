import SwiftUI

struct MessageBubble: View {
    @Environment(ConversationController.self) private var controller
    @Bindable var message: ChatMessage
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .bottom) {
            if message.role == .user {
                Spacer(minLength: 48)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                let attachments = controller.attachments(for: message)
                if !attachments.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(attachments) { attachment in
                                if let path = attachment.localPath,
                                   let image = UIImage(contentsOfFile: path) {
                                    Image(uiImage: image)
                                        .resizable()
                                        .scaledToFill()
                                        .frame(width: 180, height: 150)
                                        .clipShape(RoundedRectangle(cornerRadius: 16))
                                }
                            }
                        }
                    }
                }
                if !message.text.isEmpty || attachments.isEmpty {
                    renderedText
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

    @ViewBuilder
    private var renderedText: some View {
        let content = message.text.isEmpty ? " " : message.text
        if message.role == .assistant {
            Text(AssistantMarkdownRenderer.attributedString(from: content))
                .lineSpacing(3)
                .tint(.accentColor)
        } else {
            Text(content)
        }
    }
}

struct AssistantMarkdownBlock: Equatable {
    enum Style: Equatable {
        case title
        case heading
        case paragraph
        case listItem
    }

    let style: Style
    var source: String
}

enum AssistantMarkdownRenderer {
    static func blocks(from source: String) -> [AssistantMarkdownBlock] {
        let lines = source
            .replacingOccurrences(of: "\r\n", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
        var result: [AssistantMarkdownBlock] = []
        var paragraphLines: [String] = []
        var listLines: [String] = []

        func flushParagraph() {
            guard !paragraphLines.isEmpty else { return }
            result.append(AssistantMarkdownBlock(
                style: .paragraph,
                source: paragraphLines.joined(separator: " ")
            ))
            paragraphLines.removeAll(keepingCapacity: true)
        }

        func flushListItem() {
            guard !listLines.isEmpty else { return }
            result.append(AssistantMarkdownBlock(
                style: .listItem,
                source: normalizedListItem(listLines)
            ))
            listLines.removeAll(keepingCapacity: true)
        }

        for rawLine in lines {
            let trimmed = rawLine.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                flushParagraph()
                flushListItem()
                continue
            }

            if let heading = heading(from: trimmed) {
                flushParagraph()
                flushListItem()
                result.append(heading)
                continue
            }

            if isListItem(trimmed) {
                flushParagraph()
                flushListItem()
                listLines = [trimmed]
                continue
            }

            if !listLines.isEmpty, rawLine.first?.isWhitespace == true {
                listLines.append(trimmed)
                continue
            }

            flushListItem()
            paragraphLines.append(trimmed)
        }

        flushParagraph()
        flushListItem()
        return result
    }

    static func attributedString(from source: String) -> AttributedString {
        let parsed = blocks(from: source)
        guard !parsed.isEmpty else { return AttributedString(source) }

        var result = AttributedString()
        for (index, block) in parsed.enumerated() {
            var fragment = (try? AttributedString(
                markdown: block.source,
                options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
            )) ?? AttributedString(block.source)
            switch block.style {
            case .title:
                fragment.font = .title3.weight(.semibold)
            case .heading:
                fragment.font = .headline
            case .paragraph, .listItem:
                fragment.font = .body
            }
            result.append(fragment)

            guard index < parsed.index(before: parsed.endIndex) else { continue }
            let next = parsed[index + 1]
            let separator = block.style == .listItem && next.style == .listItem
                ? "\n"
                : "\n\n"
            result.append(AttributedString(separator))
        }
        return result
    }

    private static func heading(from line: String) -> AssistantMarkdownBlock? {
        let markerCount = line.prefix { $0 == "#" }.count
        guard (1...6).contains(markerCount),
              line.dropFirst(markerCount).first == " " else { return nil }
        return AssistantMarkdownBlock(
            style: markerCount == 1 ? .title : .heading,
            source: String(line.dropFirst(markerCount + 1))
        )
    }

    private static func isListItem(_ line: String) -> Bool {
        if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("+ ") {
            return true
        }
        return line.range(of: #"^\d+\.\s+"#, options: .regularExpression) != nil
    }

    private static func normalizedListItem(_ lines: [String]) -> String {
        guard let first = lines.first else { return "" }
        let normalizedFirst: String
        if first.hasPrefix("- [ ] ") {
            normalizedFirst = "☐ " + first.dropFirst(6)
        } else if first.lowercased().hasPrefix("- [x] ") {
            normalizedFirst = "☑ " + first.dropFirst(6)
        } else if first.hasPrefix("- ") || first.hasPrefix("* ") || first.hasPrefix("+ ") {
            normalizedFirst = "• " + first.dropFirst(2)
        } else {
            normalizedFirst = first
        }
        return ([normalizedFirst] + lines.dropFirst().map { "  \($0)" })
            .joined(separator: "\n")
    }
}
