/**
 * 后端 API 封装：健康检查、会话、发消息、SSE、批准、收件箱、模型、插件、回退。
 * 类型与 schemas / 路由返回值对齐。真正的 Agent 循环在 backend agent_runtime。
 */
export type DutyChannels = {
  wecom: boolean;
  dingtalk: boolean;
  webhook: boolean;
  workspace?: boolean | string;
};

export type Health = {
  ok: boolean;
  model: string;
  has_openai: boolean;
  has_anthropic: boolean;
  has_deepseek: boolean;
  has_zhipu?: boolean;
  has_dashscope?: boolean;
  has_xai?: boolean;
  has_cursor?: boolean;
  duty?: DutyChannels;
};

export type DutyAlert = {
  id: string;
  title: string;
  summary: string;
  service: string;
  severity: string;
  source: string;
  stack: string;
  link: string;
  status: string;
  session_id: string | null;
  assigned_to: string;
  acked_by: string;
  acked_at: string;
  created_at: string;
  prompt: string;
};

export type AuthUser = {
  id: string;
  username: string;
  role: string;
  display_name: string;
};

export type DutyStatus = {
  ok: boolean;
  public_url: string;
  workspace: string;
  wecom: boolean;
  dingtalk: boolean;
  webhook: boolean;
  target?: string;
};

export type TargetBug = { id: number; title: string; hint: string };

export type TargetStatus = {
  name: string;
  cwd: string;
  pid: number | null;
  running: boolean;
  active_bug: number;
  health: { ok: boolean; status_code: number; body: string };
  canary: { ok: boolean; status_code: number; body: string };
  health_url?: string;
  canary_url?: string;
  fixed?: boolean;
  catalog?: TargetBug[];
  log_tail?: string;
  log_path?: string;
};

export type Session = {
  id: string;
  title: string;
  workspace_path: string;
  model: string;
  mode: "plan" | "build";
  pending_tool?: string;
  pending_arguments?: string;
  live_run?: boolean;
  run_status?: string;
};

export type FileEntry = { name: string; path: string; dir: boolean };

export type ChatMessage = {
  id?: string;
  role: string;
  kind?: string;
  text: string;
};

export type DriveInfo = { path: string; name: string };

export type BrowseResult = {
  path: string;
  parent: string | null;
  writable: boolean;
  blocked: boolean;
  entries: { name: string; path: string; dir: boolean }[];
};

export type ModelVariant = { id: string; label: string; custom_id?: string };

export type ModelProvider = {
  id: string;
  label: string;
  default?: boolean;
  configured?: boolean;
  variants: ModelVariant[];
};

export type PluginInfo = {
  id: string;
  name: string;
  version?: string;
  publisher?: string;
  summary: string;
  exts: string[];
  kind?: string;
  builtin: boolean;
  recommended: boolean;
  license?: string;
  installed: boolean;
  enabled: boolean;
  readme?: string;
};

export class PluginRequiredError extends Error {
  constructor(
    public plugin: { id: string; name: string; summary?: string },
    public ext: string,
  ) {
    super(`需要插件：${plugin.name}`);
    this.name = "PluginRequiredError";
  }
}

const AUTH_KEY = "forge.auth";
const AUTH_META = "forge.auth.meta";

export function getAuthToken(): string {
  const remembered = localStorage.getItem(AUTH_KEY);
  const metaRaw = localStorage.getItem(AUTH_META);
  if (remembered && metaRaw) {
    try {
      const meta = JSON.parse(metaRaw) as { exp?: number };
      if (meta.exp && Date.now() < meta.exp) return remembered;
    } catch {
      /* ignore */
    }
    localStorage.removeItem(AUTH_KEY);
    localStorage.removeItem(AUTH_META);
  }
  return sessionStorage.getItem(AUTH_KEY) || "";
}

export function setAuthToken(token: string, opts?: { remember?: boolean; expiresAt?: string }) {
  sessionStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(AUTH_META);
  if (!token) return;
  sessionStorage.setItem(AUTH_KEY, token);
  if (opts?.remember) {
    const exp = opts.expiresAt ? Date.parse(opts.expiresAt) : Date.now() + 3 * 24 * 3600 * 1000;
    localStorage.setItem(AUTH_KEY, token);
    localStorage.setItem(AUTH_META, JSON.stringify({ exp }));
  }
}

function headers(json = false): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const envToken = import.meta.env.VITE_FORGE_TOKEN as string | undefined;
  if (envToken) h["X-Forge-Token"] = envToken;
  const session = getAuthToken();
  if (session) h.Authorization = `Bearer ${session}`;
  return h;
}

async function parseError(res: Response): Promise<never> {
  const text = await res.text();
  let detail: unknown = text;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    detail = parsed.detail ?? text;
  } catch {
    /* raw body */
  }
  if (detail && typeof detail === "object" && (detail as { code?: string }).code === "plugin_required") {
    const d = detail as {
      plugin: { id: string; name: string; summary?: string };
      ext: string;
    };
    throw new PluginRequiredError(d.plugin, d.ext);
  }
  if (typeof detail === "string") throw new Error(detail);
  if (detail && typeof detail === "object" && "message" in detail) {
    throw new Error(String((detail as { message: string }).message));
  }
  throw new Error(text || res.statusText);
}

async function ok<T>(res: Response): Promise<T> {
  if (!res.ok) await parseError(res);
  return (await res.json()) as T;
}

export async function health(): Promise<Health> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error("API not reachable");
  return res.json();
}

export async function listDrives(): Promise<DriveInfo[]> {
  const res = await fetch("/api/system/drives", { headers: headers() });
  const data = await ok<{ drives: DriveInfo[] }>(res);
  return data.drives;
}

export async function browseDir(path: string): Promise<BrowseResult> {
  const res = await fetch(`/api/system/browse?path=${encodeURIComponent(path)}`, {
    headers: headers(),
  });
  return ok<BrowseResult>(res);
}

export async function listModelCatalog(): Promise<{
  providers: ModelProvider[];
  custom: { id: string; provider: string; label: string; model_id: string }[];
}> {
  const res = await fetch("/api/models", { headers: headers() });
  return ok(res);
}

export async function addCustomModel(body: { provider: string; label: string; model_id: string }) {
  const res = await fetch("/api/models", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  return ok<{ id: string; provider: string; label: string; model_id: string }>(res);
}

export async function setDefaultModel(model: string) {
  const res = await fetch("/api/models/default", {
    method: "PUT",
    headers: headers(true),
    body: JSON.stringify({ model }),
  });
  return ok<{ ok: boolean; model: string }>(res);
}

export async function deleteCustomModel(id: string) {
  const res = await fetch(`/api/models/${id}`, { method: "DELETE", headers: headers() });
  if (!res.ok) await parseError(res);
}

export async function listPlugins(q = ""): Promise<{
  plugins: PluginInfo[];
  install_root: string;
  library: string;
}> {
  const res = await fetch(`/api/plugins?q=${encodeURIComponent(q)}`, { headers: headers() });
  return ok(res);
}

export async function getPlugin(id: string): Promise<PluginInfo> {
  const res = await fetch(`/api/plugins/${id}`, { headers: headers() });
  return ok(res);
}

export async function installPlugin(id: string) {
  const res = await fetch(`/api/plugins/${id}/install`, { method: "POST", headers: headers() });
  return ok<{ ok: boolean; plugin_id: string; path?: string; files?: string[] }>(res);
}

export async function uninstallPlugin(id: string) {
  const res = await fetch(`/api/plugins/${id}/uninstall`, { method: "POST", headers: headers() });
  return ok<{ ok: boolean }>(res);
}

export async function installAllPlugins() {
  const res = await fetch("/api/plugins/install-all", { method: "POST", headers: headers() });
  return ok<{ ok: boolean; installed: string[] }>(res);
}

export async function listSessions(): Promise<Session[]> {
  const res = await fetch("/api/sessions", { headers: headers() });
  return ok<Session[]>(res);
}

export async function createSession(workspace_path: string, mode: "plan" | "build", model = "") {
  const res = await fetch("/api/sessions", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({
      workspace_path,
      mode,
      model,
      title: workspace_path.split(/[\\/]/).pop(),
    }),
  });
  return ok<Session>(res);
}

export async function attachSession(sessionId: string) {
  const res = await fetch(`/api/sessions/${sessionId}/attach`, {
    method: "POST",
    headers: headers(),
  });
  return ok<Session>(res);
}

export async function deleteSession(sessionId: string) {
  const res = await fetch(`/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!res.ok) await parseError(res);
}

export async function listMessages(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(`/api/sessions/${sessionId}/messages`, { headers: headers() });
  const rows = await ok<{ id: string; role: string; kind: string; content: string }[]>(res);
  return rows.map((r) => ({ id: r.id, role: r.role, kind: r.kind || undefined, text: r.content }));
}

export async function listTree(sessionId: string, path = ".") {
  const res = await fetch(`/api/sessions/${sessionId}/tree?path=${encodeURIComponent(path)}`, {
    headers: headers(),
  });
  return ok<{ path: string; parent: string | null; entries: FileEntry[] }>(res);
}

export async function mkdirEntry(sessionId: string, path: string) {
  const res = await fetch(`/api/sessions/${sessionId}/mkdir`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ path }),
  });
  return ok<{ ok: boolean; path: string }>(res);
}

export async function createFileEntry(sessionId: string, path: string, content = "") {
  const res = await fetch(`/api/sessions/${sessionId}/touch`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ path, content }),
  });
  return ok<{ ok: boolean; path: string }>(res);
}

export async function renameEntry(sessionId: string, path: string, name: string) {
  const res = await fetch(`/api/sessions/${sessionId}/rename`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ path, name }),
  });
  return ok<{ ok: boolean; path: string; dir: boolean }>(res);
}

export async function deleteEntry(sessionId: string, path: string) {
  const res = await fetch(`/api/sessions/${sessionId}/entry?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
    headers: headers(),
  });
  return ok<{ ok: boolean; path: string }>(res);
}

export type FileRead = {
  path: string;
  content: string;
  kind: string;
  binary: boolean;
  size?: number;
};

export async function readFile(sessionId: string, path: string): Promise<FileRead> {
  const res = await fetch(`/api/sessions/${sessionId}/file?path=${encodeURIComponent(path)}`, {
    headers: headers(),
  });
  return ok<FileRead>(res);
}

export async function saveFile(sessionId: string, path: string, content: string) {
  const res = await fetch(`/api/sessions/${sessionId}/file`, {
    method: "PUT",
    headers: headers(true),
    body: JSON.stringify({ path, content }),
  });
  return ok<{ ok: boolean; path: string }>(res);
}

export async function saveBlob(
  sessionId: string,
  path: string,
  data: Blob | ArrayBuffer | Uint8Array,
) {
  const h = headers() as Record<string, string>;
  h["Content-Type"] = "application/octet-stream";
  const body =
    data instanceof Blob
      ? data
      : new Blob([data instanceof Uint8Array ? data : new Uint8Array(data)]);
  const res = await fetch(`/api/sessions/${sessionId}/blob?path=${encodeURIComponent(path)}`, {
    method: "PUT",
    headers: h,
    body,
  });
  return ok<{ ok: boolean; path: string; size: number }>(res);
}

export async function readBlob(sessionId: string, path: string): Promise<Blob> {
  const res = await fetch(`/api/sessions/${sessionId}/blob?path=${encodeURIComponent(path)}`, {
    headers: headers(),
  });
  if (!res.ok) await parseError(res);
  return res.blob();
}

export async function previewWrite(sessionId: string, path: string, content: string) {
  const res = await fetch(`/api/sessions/${sessionId}/preview-write`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ path, content }),
  });
  return ok<{ exists: boolean; diff: string }>(res);
}

export type FileBackup = {
  id: string;
  rel_path: string;
  action: string;
  existed: boolean;
  actor: string;
  model: string;
  summary: string;
  created_at: string;
  pushed_at: string;
  expires_at: string;
  restored_at: string;
  versions: number;
};

export async function listBackups(sessionId: string) {
  const res = await fetch(`/api/sessions/${sessionId}/backups`, { headers: headers() });
  return ok<{ items: FileBackup[] }>(res);
}

export async function restoreBackup(sessionId: string, backupId: string) {
  const res = await fetch(`/api/sessions/${sessionId}/backups/${backupId}/restore`, {
    method: "POST",
    headers: headers(),
  });
  return ok<{ ok: boolean; restored: string }>(res);
}

export async function restoreAllBackups(sessionId: string) {
  const res = await fetch(`/api/sessions/${sessionId}/backups/restore-all`, {
    method: "POST",
    headers: headers(),
  });
  return ok<{ ok: boolean; restored: string[] }>(res);
}

export async function setMode(sessionId: string, mode: "plan" | "build") {
  const res = await fetch(`/api/sessions/${sessionId}?mode=${mode}`, {
    method: "PATCH",
    headers: headers(),
  });
  return ok<Session>(res);
}

export async function setSessionModel(sessionId: string, model: string) {
  const res = await fetch(`/api/sessions/${sessionId}?model=${encodeURIComponent(model)}`, {
    method: "PATCH",
    headers: headers(),
  });
  return ok<Session>(res);
}

export type AgentEvent = {
  type: string;
  text?: string;
  tool?: string;
  arguments?: string;
  output?: string;
  message?: string;
  call_id?: string;
  path?: string;
  op?: string;
  seq?: number;
};

export async function cancelSession(sessionId: string): Promise<{ ok: boolean }> {
  const res = await fetch(`/api/sessions/${sessionId}/cancel`, {
    method: "POST",
    headers: headers(),
  });
  return ok<{ ok: boolean }>(res);
}

export function cancelOnUnload(sessionId: string) {
  fetch(`/api/sessions/${sessionId}/cancel`, {
    method: "POST",
    headers: headers(),
    keepalive: true,
  }).catch(() => undefined);
}

export async function streamSse(
  url: string,
  body: unknown | null,
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: body == null ? "GET" : "POST",
    headers: body == null ? headers() : headers(true),
    body: body == null ? undefined : JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) await parseError(res);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const chunks = buf.split("\n\n");
      buf = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as AgentEvent);
        } catch {
          /* keep listening; a bad chunk must not kill the run */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function dutyStatus(): Promise<DutyStatus> {
  const res = await fetch("/api/duty/status", { headers: headers() });
  return ok<DutyStatus>(res);
}

export async function listDutyAlerts(): Promise<DutyAlert[]> {
  const res = await fetch("/api/duty/alerts", { headers: headers() });
  const data = await ok<{ alerts: DutyAlert[] }>(res);
  return data.alerts;
}

export async function getDutyAlert(id: string): Promise<DutyAlert> {
  const res = await fetch(`/api/duty/alerts/${id}`, { headers: headers() });
  return ok<DutyAlert>(res);
}

export async function investigateAlert(id: string): Promise<{
  ok: boolean;
  resumed: boolean;
  prompt: string;
  session: Session;
}> {
  const res = await fetch(`/api/duty/alerts/${id}/investigate`, {
    method: "POST",
    headers: headers(),
  });
  return ok(res);
}

export async function createDemoAlert(): Promise<DutyAlert> {
  const res = await fetch("/api/duty/alerts/demo", { method: "POST", headers: headers() });
  const data = await ok<{ ok: boolean; alert: DutyAlert }>(res);
  return data.alert;
}

export async function testDutyNotify(): Promise<{ sent: string[]; channels: DutyStatus }> {
  const res = await fetch("/api/duty/test-notify", { method: "POST", headers: headers() });
  return ok(res);
}

export async function resolveDutyAlert(id: string): Promise<DutyAlert> {
  const res = await fetch(`/api/duty/alerts/${id}/resolve`, { method: "POST", headers: headers() });
  const data = await ok<{ ok: boolean; alert: DutyAlert }>(res);
  return data.alert;
}

export async function ackDutyAlert(id: string): Promise<DutyAlert> {
  const res = await fetch(`/api/duty/alerts/${id}/ack`, { method: "POST", headers: headers() });
  const data = await ok<{ ok: boolean; alert: DutyAlert }>(res);
  return data.alert;
}

export async function getTargetStatus(): Promise<TargetStatus> {
  const res = await fetch("/api/duty/target", { headers: headers() });
  return ok<TargetStatus>(res);
}

export async function startTarget(): Promise<TargetStatus> {
  const res = await fetch("/api/duty/target/start", { method: "POST", headers: headers() });
  return ok<TargetStatus>(res);
}

export async function restartTarget(): Promise<TargetStatus> {
  const res = await fetch("/api/duty/target/restart", { method: "POST", headers: headers() });
  return ok<TargetStatus>(res);
}

export async function nextTargetBug(): Promise<TargetStatus> {
  const res = await fetch("/api/duty/target/next-bug", { method: "POST", headers: headers() });
  return ok<TargetStatus>(res);
}

export async function assignDutyAlert(id: string, username = ""): Promise<DutyAlert> {
  const res = await fetch(`/api/duty/alerts/${id}/assign`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ username }),
  });
  const data = await ok<{ ok: boolean; alert: DutyAlert }>(res);
  return data.alert;
}

export async function authStatus(): Promise<{ needs_setup: boolean; users: number }> {
  const res = await fetch("/api/auth/status");
  return ok(res);
}

export async function setupAdmin(username: string, password: string, display_name = "") {
  const res = await fetch("/api/auth/setup", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, display_name }),
  });
  const data = await ok<{ token: string; user: AuthUser; expires_at?: string }>(res);
  setAuthToken(data.token, { remember: true, expiresAt: data.expires_at });
  return data.user;
}

export async function login(username: string, password: string, remember = false) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember }),
  });
  const data = await ok<{ token: string; user: AuthUser; expires_at?: string; remember?: boolean }>(res);
  setAuthToken(data.token, { remember: remember || !!data.remember, expiresAt: data.expires_at });
  return data.user;
}

export async function logout() {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "include", headers: headers() });
  } finally {
    setAuthToken("");
  }
}

export async function me(): Promise<AuthUser> {
  const res = await fetch("/api/auth/me", { credentials: "include", headers: headers() });
  const data = await ok<{ user: AuthUser }>(res);
  return data.user;
}

export async function listUsers(): Promise<AuthUser[]> {
  const res = await fetch("/api/auth/users", { headers: headers() });
  const data = await ok<{ users: AuthUser[] }>(res);
  return data.users;
}

export async function createUser(body: {
  username: string;
  password: string;
  role: string;
  display_name?: string;
}) {
  const res = await fetch("/api/auth/users", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  const data = await ok<{ user: AuthUser }>(res);
  return data.user;
}

export async function setUserRole(id: string, role: string) {
  const res = await fetch(`/api/auth/users/${id}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify({ role }),
  });
  return ok<{ user: AuthUser }>(res);
}
