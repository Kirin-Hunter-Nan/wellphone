import CoreText
import Foundation
import UIKit

@MainActor
enum BusinessTripPDFRenderer {
    private static let pageBounds = CGRect(x: 0, y: 0, width: 595, height: 842)
    private static let contentRect = pageBounds.insetBy(dx: 48, dy: 58)

    static func render(markdown: String) throws -> Data {
        guard !markdown.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw BusinessTripPDFRendererError.emptyDocument
        }
        let document = attributedDocument(from: markdown)
        let framesetter = CTFramesetterCreateWithAttributedString(document)
        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = [
            kCGPDFContextTitle as String: documentTitle(from: markdown),
            kCGPDFContextCreator as String: "WellPhone",
        ]
        let renderer = UIGraphicsPDFRenderer(bounds: pageBounds, format: format)
        var failed = false
        var pageCount = 0
        let data = renderer.pdfData { context in
            var offset = 0
            while offset < document.length {
                context.beginPage()
                pageCount += 1
                let graphics = context.cgContext
                graphics.saveGState()
                graphics.textMatrix = .identity
                graphics.translateBy(x: 0, y: pageBounds.height)
                graphics.scaleBy(x: 1, y: -1)
                let path = CGMutablePath()
                path.addRect(contentRect)
                let frame = CTFramesetterCreateFrame(
                    framesetter,
                    CFRange(location: offset, length: 0),
                    path,
                    nil
                )
                let visible = CTFrameGetVisibleStringRange(frame)
                guard visible.length > 0 else {
                    graphics.restoreGState()
                    failed = true
                    return
                }
                CTFrameDraw(frame, graphics)
                graphics.restoreGState()
                drawFooter(page: pageCount)
                offset += visible.length
            }
        }
        guard !failed, pageCount > 0, data.starts(with: Data("%PDF".utf8)) else {
            throw BusinessTripPDFRendererError.paginationFailed
        }
        return data
    }

    private static func attributedDocument(from markdown: String) -> NSAttributedString {
        let result = NSMutableAttributedString()
        for rawLine in markdown.components(separatedBy: .newlines) {
            let line: String
            let font: UIFont
            let spacingBefore: CGFloat
            let spacingAfter: CGFloat
            if rawLine.hasPrefix("# ") {
                line = String(rawLine.dropFirst(2))
                font = .systemFont(ofSize: 24, weight: .bold)
                spacingBefore = 2
                spacingAfter = 12
            } else if rawLine.hasPrefix("## ") {
                line = String(rawLine.dropFirst(3))
                font = .systemFont(ofSize: 16, weight: .semibold)
                spacingBefore = 12
                spacingAfter = 6
            } else {
                line = plainText(from: rawLine)
                font = .systemFont(ofSize: 11.5)
                spacingBefore = 0
                spacingAfter = rawLine.isEmpty ? 6 : 3
            }
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = 3
            paragraph.paragraphSpacingBefore = spacingBefore
            paragraph.paragraphSpacing = spacingAfter
            result.append(NSAttributedString(
                string: line + "\n",
                attributes: [
                    .font: font,
                    .foregroundColor: UIColor.black,
                    .paragraphStyle: paragraph,
                ]
            ))
        }
        return result
    }

    private static func plainText(from markdownLine: String) -> String {
        var value = markdownLine
            .replacingOccurrences(of: "- [ ] ", with: "[ ] ")
            .replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "–", with: "-")
            .replacingOccurrences(of: "—", with: "-")
            .replacingOccurrences(of: "‑", with: "-")
        let pattern = #"\[([^\]]+)\]\(([^)]+)\)"#
        if let expression = try? NSRegularExpression(pattern: pattern) {
            let range = NSRange(value.startIndex..., in: value)
            value = expression.stringByReplacingMatches(
                in: value,
                range: range,
                withTemplate: "$1: $2"
            )
        }
        return value
    }

    private static func documentTitle(from markdown: String) -> String {
        markdown.components(separatedBy: .newlines)
            .first(where: { $0.hasPrefix("# ") })
            .map { String($0.dropFirst(2)) } ?? "WellPhone 商务出差计划"
    }

    private static func drawFooter(page: Int) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .right
        NSString(string: "WellPhone · 第 \(page) 页").draw(
            in: CGRect(x: 48, y: pageBounds.height - 38, width: pageBounds.width - 96, height: 18),
            withAttributes: [
                .font: UIFont.systemFont(ofSize: 9),
                .foregroundColor: UIColor.darkGray,
                .paragraphStyle: paragraph,
            ]
        )
    }
}

private enum BusinessTripPDFRendererError: LocalizedError {
    case emptyDocument
    case paginationFailed

    var errorDescription: String? {
        switch self {
        case .emptyDocument: "出差计划为空，无法生成 PDF。"
        case .paginationFailed: "出差计划 PDF 分页失败。"
        }
    }
}
