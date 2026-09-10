//
//  WellPhoneTests.swift
//  WellPhoneTests
//
//  Created by 南佳琪 on 2026/9/10.
//

import Foundation
import Testing
@testable import WellPhone

struct WellPhoneTests {

    @Test @MainActor
    func demoGatewayStreamsAReply() async throws {
        let gateway = DemoModelGateway()
        let prompt = [ChatPromptMessage(role: .user, content: "测试消息")]
        var reply = ""

        for try await chunk in gateway.streamReply(
            to: prompt,
            conversationID: UUID()
        ) {
            reply += chunk
        }

        #expect(reply.contains("测试消息"))
        #expect(reply.contains("演示模式"))
    }

    @Test @MainActor
    func promptMessageRoundTripsThroughJSON() throws {
        let original = ChatPromptMessage(role: .assistant, content: "你好")
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(ChatPromptMessage.self, from: data)

        #expect(decoded == original)
    }

    @Test @MainActor
    func qwenStreamDecoderReadsDeltasAndDone() throws {
        let delta = #"data: {"choices":[{"delta":{"content":"你好"}}]}"#

        #expect(try QwenStreamDecoder.decode(line: delta) == .delta("你好"))
        #expect(try QwenStreamDecoder.decode(line: "data: [DONE]") == .done)
        #expect(try QwenStreamDecoder.decode(line: ": keep-alive") == .ignored)
    }

}
