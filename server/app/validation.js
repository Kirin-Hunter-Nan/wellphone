const allowedRoles = new Set(["user", "assistant"]);
const allowedContentTypes = new Set(["text", "image_url"]);

export class RequestValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "RequestValidationError";
  }
}

function validateContentPart(part) {
  if (!part || typeof part !== "object" || Array.isArray(part)) {
    throw new RequestValidationError("Each content part must be an object");
  }
  if (!allowedContentTypes.has(part.type)) {
    throw new RequestValidationError(`Unsupported content type: ${part.type}`);
  }
  if (part.type === "text" && (typeof part.text !== "string" || !part.text.trim())) {
    throw new RequestValidationError("Text content must not be empty");
  }
  if (part.type === "image_url") {
    const url = part.image_url?.url;
    if (typeof url !== "string" || !url.trim()) {
      throw new RequestValidationError("image_url.url is required");
    }
    if (!url.startsWith("https://") && !url.startsWith("data:image/")) {
      throw new RequestValidationError("Images must use HTTPS or a data:image URI");
    }
  }
}

export function validateChatRequest(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new RequestValidationError("Request body must be a JSON object");
  }
  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    throw new RequestValidationError("messages must be a non-empty array");
  }
  if (body.messages.length > 100) {
    throw new RequestValidationError("messages exceeds the limit of 100");
  }

  for (const message of body.messages) {
    if (!message || typeof message !== "object" || !allowedRoles.has(message.role)) {
      throw new RequestValidationError("Each message must have a supported role");
    }
    if (typeof message.content === "string") {
      if (!message.content.trim()) {
        throw new RequestValidationError("Message content must not be empty");
      }
      continue;
    }
    if (!Array.isArray(message.content) || message.content.length === 0) {
      throw new RequestValidationError("Message content must be text or content parts");
    }
    message.content.forEach(validateContentPart);
  }

  if (body.messages.at(-1).role !== "user") {
    throw new RequestValidationError("The final message must have the user role");
  }

  return { messages: body.messages };
}
