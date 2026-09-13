import CoreGraphics
import Foundation
import Testing
@testable import WellPhone

struct BusinessTripPDFRendererTests {
    @Test @MainActor
    func rendersChineseMarkdownAsAPaginatedPDF() throws {
        let repeatedItems = (1...80).map {
            "- **09:00-10:00 第\($0)项上海会议** · 固定\n  上海国际会议中心"
        }.joined(separator: "\n")
        let markdown = """
        # 上海商务出差计划

        包含真实 Gmail 来源锁定的固定安排。

        ## 2026-10-01 · 客户会议

        \(repeatedItems)

        ## 出发前待办

        - [ ] 准备会议材料
        """

        let data = try BusinessTripPDFRenderer.render(markdown: markdown)
        #expect(data.starts(with: Data("%PDF".utf8)))
        let provider = CGDataProvider(data: data as CFData)
        let document = provider.flatMap(CGPDFDocument.init)
        #expect((document?.numberOfPages ?? 0) >= 2)

        if let output = ProcessInfo.processInfo.environment["WELLPHONE_PDF_QA_OUTPUT"] {
            try data.write(to: URL(fileURLWithPath: output), options: .atomic)
        }
    }
}
