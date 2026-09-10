import assert from "node:assert/strict";
import test from "node:test";
import { createQwenStream, UpstreamError } from "../app/qwen-client.js";

const config = {
  apiKey: "secret-test-key",
  baseURL: new URL("https://workspace.example.com/compatible-mode/v1"),
  model: "qwen-multimodal-test",
};

test("requests and returns the upstream SSE stream", async () => {
  let capturedURL;
  let capturedOptions;
  const expected = 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]\n\n';
  const fetchImpl = async (url, options) => {
    capturedURL = url;
    capturedOptions = options;
    return new Response(expected, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  };

  const stream = await createQwenStream({
    messages: [{ role: "user", content: "你好" }],
    config,
    fetchImpl,
  });
  const output = await new Response(stream).text();
  const requestBody = JSON.parse(capturedOptions.body);

  assert.equal(capturedURL.href, "https://workspace.example.com/compatible-mode/v1/chat/completions");
  assert.equal(capturedOptions.headers.Authorization, "Bearer secret-test-key");
  assert.equal(requestBody.model, config.model);
  assert.equal(requestBody.stream, true);
  assert.equal(output, expected);
});

test("maps an upstream failure to UpstreamError", async () => {
  const fetchImpl = async () => new Response("bad credentials", { status: 401 });

  await assert.rejects(
    createQwenStream({ messages: [{ role: "user", content: "x" }], config, fetchImpl }),
    (error) => error instanceof UpstreamError && error.status === 401
  );
});
