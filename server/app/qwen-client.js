export class UpstreamError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "UpstreamError";
    this.status = status;
  }
}

export async function createQwenStream({ messages, config, signal, fetchImpl = fetch }) {
  const endpoint = new URL("chat/completions", ensureTrailingSlash(config.baseURL));
  const requestTime = new Date().toISOString();
  const response = await fetchImpl(endpoint, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      model: config.model,
      messages: [
        {
          role: "system",
          content: `You are WellPhone, an iPhone assistant. The current time is ${requestTime}. The user's IANA time zone is Asia/Shanghai. Use reminder_create only when the user explicitly asks to create a reminder. Resolve relative dates to an absolute ISO 8601 date-time with an explicit UTC offset. Never claim a reminder was created: the iPhone app will ask for confirmation and report the result.`,
        },
        ...messages,
      ],
      tools: [reminderCreateTool],
      tool_choice: "auto",
      tool_stream: false,
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

export const reminderCreateTool = {
  type: "function",
  function: {
    name: "reminder_create",
    description: "Prepare one Apple Reminders item for explicit user confirmation. Do not use for general questions or when the user is only discussing a possible reminder feature.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        title: {
          type: "string",
          description: "A concise reminder title in the user's language.",
        },
        dueAt: {
          type: "string",
          description: "Absolute ISO 8601 date-time including a UTC offset, for example 2026-09-10T18:00:00+08:00.",
        },
        notes: {
          type: "string",
          description: "Optional useful context from the user request. Omit when unnecessary.",
        },
        listName: {
          type: "string",
          description: "Optional existing Apple Reminders list name. Omit to use the default list.",
        },
      },
      required: ["title", "dueAt"],
    },
  },
};

function ensureTrailingSlash(url) {
  return new URL(url.href.endsWith("/") ? url.href : `${url.href}/`);
}
