import http from "node:http";
import { loadConfig } from "./config.js";
import { createQwenStream, UpstreamError } from "./qwen-client.js";
import { RequestValidationError, validateChatRequest } from "./validation.js";

const config = loadConfig();
const conversationPath = /^\/v1\/conversations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/messages$/i;

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? "/", "http://localhost");

    if (request.method === "GET" && url.pathname === "/health") {
      return sendJSON(response, 200, { status: "ok", model: config.model });
    }

    if (request.method !== "POST" || !conversationPath.test(url.pathname)) {
      return sendJSON(response, 404, {
        error: { code: "not_found", message: "Route not found" },
      });
    }

    const body = await readJSON(request, config.maxRequestBytes);
    const { messages } = validateChatRequest(body);
    const abortController = new AbortController();
    response.on("close", () => {
      if (!response.writableEnded) abortController.abort();
    });

    const stream = await createQwenStream({
      messages,
      config,
      signal: abortController.signal,
    });

    response.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });

    for await (const chunk of stream) {
      if (!response.write(chunk)) {
        await new Promise((resolve) => response.once("drain", resolve));
      }
    }
    response.end();
  } catch (error) {
    handleError(error, response);
  }
});

server.listen(config.port, config.host, () => {
  console.log(`WellPhone model proxy listening at http://${config.host}:${config.port}`);
});

async function readJSON(request, maxBytes) {
  const chunks = [];
  let receivedBytes = 0;

  for await (const chunk of request) {
    receivedBytes += chunk.length;
    if (receivedBytes > maxBytes) {
      const error = new RequestValidationError("Request body is too large");
      error.status = 413;
      throw error;
    }
    chunks.push(chunk);
  }

  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RequestValidationError("Request body must be valid JSON");
  }
}

function handleError(error, response) {
  if (response.headersSent) {
    response.destroy(error instanceof Error ? error : undefined);
    return;
  }

  if (error instanceof RequestValidationError) {
    sendJSON(response, error.status ?? 400, {
      error: { code: "invalid_request", message: error.message },
    });
    return;
  }

  if (error instanceof UpstreamError) {
    console.error(`Qwen upstream error (${error.status}): ${error.message}`);
    sendJSON(response, 502, {
      error: {
        code: "model_upstream_error",
        message: "模型服务暂时不可用，请稍后重试。",
      },
    });
    return;
  }

  if (error?.name === "AbortError") return;
  console.error(error);
  sendJSON(response, 500, {
    error: { code: "internal_error", message: "服务端发生内部错误。" },
  });
}

function sendJSON(response, status, value) {
  const data = JSON.stringify(value);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(data),
  });
  response.end(data);
}
