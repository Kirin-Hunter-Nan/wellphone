function requiredEnvironment(name, environment) {
  const value = environment[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function positiveInteger(value, fallback, name) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

export function loadConfig(environment = process.env) {
  const apiKey = requiredEnvironment("DASHSCOPE_API_KEY", environment);
  const baseURL = new URL(requiredEnvironment("QWEN_BASE_URL", environment));
  if (baseURL.protocol !== "https:") {
    throw new Error("QWEN_BASE_URL must use HTTPS");
  }

  return Object.freeze({
    apiKey,
    baseURL,
    model: requiredEnvironment("QWEN_MODEL", environment),
    host: environment.HOST?.trim() || "127.0.0.1",
    port: positiveInteger(environment.PORT, 8787, "PORT"),
    maxRequestBytes: positiveInteger(
      environment.MAX_REQUEST_BYTES,
      26_214_400,
      "MAX_REQUEST_BYTES"
    ),
  });
}
