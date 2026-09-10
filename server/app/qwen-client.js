export class UpstreamError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "UpstreamError";
    this.status = status;
  }
}

export async function createQwenStream({ messages, config, signal, fetchImpl = fetch }) {
  const endpoint = new URL("chat/completions", ensureTrailingSlash(config.baseURL));
  const response = await fetchImpl(endpoint, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      model: config.model,
      messages,
      stream: true,
      stream_options: { include_usage: true },
    }),
    signal,
  });

  if (!response.ok) {
    const upstreamBody = (await response.text()).slice(0, 2_000);
    throw new UpstreamError(response.status, upstreamBody || response.statusText);
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream")) {
    throw new UpstreamError(502, "Qwen returned a non-streaming response");
  }
  if (!response.body) {
    throw new UpstreamError(502, "Qwen returned an empty response stream");
  }

  return response.body;
}

function ensureTrailingSlash(url) {
  return new URL(url.href.endsWith("/") ? url.href : `${url.href}/`);
}
