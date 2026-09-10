import assert from "node:assert/strict";
import test from "node:test";
import { validateChatRequest } from "../app/validation.js";

test("accepts text and multimodal messages", () => {
  const result = validateChatRequest({
    messages: [
      { role: "assistant", content: "请上传票据" },
      {
        role: "user",
        content: [
          { type: "image_url", image_url: { url: "data:image/jpeg;base64,AA==" } },
          { type: "text", text: "识别这张票据" },
        ],
      },
    ],
  });

  assert.equal(result.messages.length, 2);
});

test("rejects unsupported content and assistant-final history", () => {
  assert.throws(
    () => validateChatRequest({
      messages: [{ role: "user", content: [{ type: "file", url: "x" }] }],
    }),
    /Unsupported content type/
  );
  assert.throws(
    () => validateChatRequest({ messages: [{ role: "assistant", content: "完成" }] }),
    /final message/
  );
});
