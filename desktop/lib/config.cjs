const DEFAULTS = Object.freeze({
  backendUrl: "http://localhost:8000",
  dashboardUrl: "http://localhost:5173",
  authMode: "api-key",
  workspaceId: "",
  title: "Desktop meeting",
  vocabulary: "",
  projectHint: "",
  language: "auto",
  includeMic: true,
  shortcut: "CommandOrControl+Shift+Space",
  keepLocalAudio: true,
  platform: "other",
});
function validateUrl(value) {
  const url = new URL(value);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["http:", "https:"].includes(url.protocol)
  )
    throw new Error(
      "Use an HTTP(S) address without credentials, query parameters or fragments.",
    );
  if (
    url.protocol === "http:" &&
    !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
  )
    throw new Error(
      "Use HTTPS for a remote server. HTTP is supported only on this computer.",
    );
  return url.href.replace(/\/+$/, "");
}
function normalizeConfig(input = {}) {
  const result = { ...DEFAULTS };
  for (const key of Object.keys(DEFAULTS))
    if (Object.hasOwn(input, key)) result[key] = input[key];
  result.backendUrl = validateUrl(String(result.backendUrl));
  result.dashboardUrl = validateUrl(String(result.dashboardUrl));
  if (!["api-key", "bearer"].includes(result.authMode))
    throw new Error("Choose an API key or workspace access token.");
  if (
    result.workspaceId &&
    !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(
      result.workspaceId,
    )
  )
    throw new Error("Enter a valid workspace ID.");
  for (const [key, maximum] of [
    ["title", 512],
    ["vocabulary", 4000],
    ["projectHint", 256],
    ["language", 20],
    ["shortcut", 100],
  ]) {
    if (typeof result[key] !== "string" || result[key].length > maximum)
      throw new Error(`Invalid ${key}.`);
    result[key] = result[key].trim();
  }
  const terms = result.vocabulary
    .split(/[,\n]/)
    .map((term) => term.trim())
    .filter(Boolean);
  if (terms.length > 50 || terms.some((term) => term.length > 80))
    throw new Error(
      "Use up to 50 vocabulary terms, each at most 80 characters.",
    );
  if (!result.title) result.title = DEFAULTS.title;
  if (!/^[a-z]{2,3}(?:-[A-Z]{2})?$|^auto$/.test(result.language))
    throw new Error("Choose an available listening language.");
  if (
    !/^(?=.*(?:CommandOrControl|Command|Control|Ctrl))(?=.*(?:Shift|Alt|Option))[-+A-Za-z0-9]+$/.test(
      result.shortcut,
    )
  )
    throw new Error(
      "Use a shortcut with Control/Command and Shift or Alt, such as CommandOrControl+Shift+Space.",
    );
  if (!["teams", "slack", "discord", "other"].includes(result.platform))
    throw new Error("Choose a call app.");
  result.includeMic = result.includeMic === true;
  result.keepLocalAudio = result.keepLocalAudio === true;
  return result;
}
function headers(config, token) {
  return {
    "Content-Type": "application/json",
    ...(token
      ? config.authMode === "bearer"
        ? { Authorization: `Bearer ${token}` }
        : { "X-API-Key": token }
      : {}),
    ...(config.workspaceId ? { "X-Workspace-ID": config.workspaceId } : {}),
  };
}
function websocketUrl(base, path) {
  if (!/^\/api\/live\/ws\/[\da-f-]{36}$/i.test(path))
    throw new Error("The backend returned an invalid meeting address.");
  return base.replace(/^http/, "ws") + path;
}
module.exports = {
  DEFAULTS,
  normalizeConfig,
  headers,
  validateUrl,
  websocketUrl,
};
