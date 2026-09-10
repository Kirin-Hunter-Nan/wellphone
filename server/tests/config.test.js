import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig } from "../app/config.js";

test("loads a valid Qwen configuration", () => {
  const config = loadConfig({
    DASHSCOPE_API_KEY: "test-key",
    QWEN_BASE_URL: "https://workspace.example.com/compatible-mode/v1",
    QWEN_MODEL: "qwen-multimodal-test",
    PORT: "9000",
  });

  assert.equal(config.apiKey, "test-key");
  assert.equal(config.baseURL.protocol, "https:");
  assert.equal(config.model, "qwen-multimodal-test");
  assert.equal(config.host, "127.0.0.1");
  assert.equal(config.port, 9000);
});

test("rejects missing secrets and insecure upstream URLs", () => {
  assert.throws(() => loadConfig({}), /DASHSCOPE_API_KEY/);
  assert.throws(
    () => loadConfig({
      DASHSCOPE_API_KEY: "test-key",
      QWEN_BASE_URL: "http://example.com/v1",
      QWEN_MODEL: "qwen-test",
    }),
    /HTTPS/
  );
});
