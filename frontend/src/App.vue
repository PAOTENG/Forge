<!--
  值班工作台主界面。
  功能：侧栏会话、对话气泡、SSE 看模型输出、HITL 批准条、文件树、金丝雀卡片。
  发消息走 POST /api/sessions/{id}/messages；刷新走 GET /events?after=seq。
  不在浏览器里跑 Agent。
-->
<script setup lang="ts">
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  attachSession,
  createDemoAlert,
  createSession,
  deleteEntry,
  deleteSession,
  mkdirEntry,
  createFileEntry,
  renameEntry,
  dutyStatus,
  health,
  investigateAlert,
  listDutyAlerts,
  listMessages,
  listSessions,
  listTree,
  previewWrite,
  readBlob,
  readFile,
  saveFile,
  saveBlob,
  setMode,
  setSessionModel,
  setDefaultModel,
  streamSse,
  cancelSession,
  cancelOnUnload,
  installAllPlugins,
  ackDutyAlert,
  assignDutyAlert,
  authStatus,
  getTargetStatus,
  nextTargetBug,
  restartTarget,
  startTarget,
  logout as logoutApi,
  me,
  resolveDutyAlert,
  testDutyNotify,
  type AgentEvent,
  type AuthUser,
  type DutyAlert,
  type DutyStatus,
  type FileEntry,
  type Health,
  type Session,
  type TargetStatus,
} from "./api";
import AuthGate from "./components/AuthGate.vue";
import DocxEditor from "./components/DocxEditor.vue";
import DutyInbox from "./components/DutyInbox.vue";
import FeedbackLayer from "./components/FeedbackLayer.vue";
import TargetCard from "./components/TargetCard.vue";
import FolderPicker from "./components/FolderPicker.vue";
import UsersPanel from "./components/UsersPanel.vue";
import ModelMenu from "./components/ModelMenu.vue";
import PluginPanel from "./components/PluginPanel.vue";
import ResizeHandle from "./components/ResizeHandle.vue";
import RollbackModal from "./components/RollbackModal.vue";
import UserTerminal from "./components/UserTerminal.vue";
import { docxToHtml, htmlToDocx } from "./lib/word";
import { renderMarkdown } from "./lib/markdown";
import { formatToolCall, formatToolOutput } from "./lib/chat";
import { colLabel, emptyGrid, parseCsv, parseWorkbook, stringifyCsv, writeWorkbook } from "./lib/sheet";
import { languageFor, monaco } from "./monaco";

const status = ref<Health | null>(null);
const error = ref("");
const workspace = ref("D:\\project\\newAgentProject");
const workspaceOk = ref(false);
const preferredModel = ref("deepseek/deepseek-chat");
const session = ref<Session | null>(null);
const entries = ref<FileEntry[]>([]);
const treePath = ref(".");
const treeParent = ref<string | null>(null);
const selected = ref<FileEntry | null>(null);
const fsPrompt = ref<{ kind: "file" | "folder" | "rename"; name: string } | null>(null);
const deleteTarget = ref<FileEntry | null>(null);
const fsBusy = ref(false);
const filePath = ref("");
const fileKind = ref("");
const fileBinary = ref(false);
const fileContent = ref("");
const dirty = ref(false);
const mdMode = ref<"preview" | "markdown">("preview");
const pluginNeed = ref<{ id: string; name: string; summary?: string } | null>(null);
const highlightPlugin = ref("");
const imageUrl = ref("");
const pdfUrl = ref("");
const sheetNames = ref<string[]>([]);
const sheetMap = ref<Record<string, string[][]>>({});
const activeSheet = ref("");
const csvRows = ref<string[][]>([]);
const docHtml = ref("<p></p>");
const fileSize = ref(0);
const prompt = ref("");
const streaming = ref(false);
const lastRunStopped = ref(false);
const userPickedRel = ref("");
let streamCtl: AbortController | null = null;
let userStopping = false;
let lastSeq = 0;
let loadToken = 0;
const opening = ref(false);
const saving = ref(false);
const pendingApproval = ref<AgentEvent | null>(null);
const runActive = computed(() => streaming.value || !!pendingApproval.value);
const writeDiff = ref("");
const sessions = ref<Session[]>([]);
const messages = reactive<{ role: string; text: string; kind?: string }[]>([]);
const chatEl = ref<HTMLDivElement | null>(null);
const pluginsOpen = ref(false);
const rollbackOpen = ref(false);
const termOpen = ref(false);
const changeWs = ref(false);
const pendingPath = ref("");
const dutyMode = ref(false);
const isNarrow = ref(false);
const mobilePane = ref<"chat" | "files" | "log">("chat");
const dutyAlerts = ref<DutyAlert[]>([]);
const dutyInfo = ref<DutyStatus | null>(null);
const dutyBusy = ref(false);
const feedbackBusy = ref("");
const toast = ref<{ text: string; kind: "ok" | "err" } | null>(null);
const highlightAlert = ref("");
const meUser = ref<AuthUser | null>(null);
const needsSetup = ref(false);
const usersOpen = ref(false);
const targetInfo = ref<TargetStatus | null>(null);
let narrowMq: MediaQueryList | null = null;
let toastTimer = 0;
let homePoll = 0;

const FILES_MIN = 180;
const CHAT_MIN = 280;
const BOTTOM_MIN = 96;
const filesW = ref(Number(localStorage.getItem("forge.filesW")) || 240);
const chatW = ref(Number(localStorage.getItem("forge.chatW")) || 360);
const bottomH = ref(Number(localStorage.getItem("forge.bottomH")) || 168);
const pluginW = ref(Number(localStorage.getItem("forge.pluginW")) || 400);
let filesStart = filesW.value;
let chatStart = chatW.value;
let bottomStart = bottomH.value;
let pluginStart = pluginW.value;

const editorEl = ref<HTMLDivElement | null>(null);
const termEl = ref<HTMLDivElement | null>(null);
let editor: monaco.editor.IStandaloneCodeEditor | null = null;
let term: Terminal | null = null;
let fit: FitAddon | null = null;
let ignoreEdit = false;

const hasKey = computed(
  () =>
    !!(
      status.value?.has_deepseek ||
      status.value?.has_openai ||
      status.value?.has_anthropic ||
      status.value?.has_zhipu ||
      status.value?.has_dashscope ||
      status.value?.has_xai ||
      status.value?.has_cursor
    ),
);
const activeModel = computed(() => session.value?.model || preferredModel.value);
const workspaceName = computed(() => {
  const p = session.value?.workspace_path || workspace.value;
  return p.split(/[\\/]/).filter(Boolean).pop() || p;
});
const investigatingProject = computed(() => {
  const alert = dutyAlerts.value[0];
  const text = `${alert?.service || ""} ${alert?.title || ""} ${alert?.summary || ""}`;
  if (/kb-qa|知识库|ledger/i.test(text)) return "Ledger · 知识库问答 Agent";
  if (/roomcheck|客房/i.test(text)) return "RoomCheck · 客房验收 Agent";
  if (alert?.service) return alert.service;
  if (session.value) return workspaceName.value;
  return "";
});
const isMarkdown = computed(() => /\.(md|markdown)$/i.test(filePath.value));
const isTex = computed(() => /\.(tex|ltx|latex)$/i.test(filePath.value));
const isOverlay = computed(() =>
  ["image", "pdf", "spreadsheet", "csv", "document", "media", "binary"].includes(fileKind.value),
);
const readOnlyKind = computed(() =>
  ["image", "pdf", "media", "binary"].includes(fileKind.value),
);
const showPreview = computed(
  () => isMarkdown.value && mdMode.value === "preview" && !isOverlay.value && !!filePath.value,
);
const showEditor = computed(
  () => !isOverlay.value && (!isMarkdown.value || mdMode.value === "markdown" || !filePath.value),
);
const renderedMd = computed(() => (showPreview.value ? renderMarkdown(fileContent.value) : ""));
const canEdit = computed(() => {
  if (!filePath.value || readOnlyKind.value) return false;
  if (fileKind.value === "spreadsheet" || fileKind.value === "csv" || fileKind.value === "document")
    return true;
  if (isMarkdown.value) return mdMode.value === "markdown";
  return fileKind.value === "editor" || fileKind.value === "markdown" || fileKind.value === "";
});
const sheetRows = computed(() => sheetMap.value[activeSheet.value] || []);
const sheetCols = computed(() => {
  const rows = fileKind.value === "csv" ? csvRows.value : sheetRows.value;
  return Math.max(1, ...rows.map((r) => r.length), 1);
});
const kindLabel = computed(() => {
  const map: Record<string, string> = {
    spreadsheet: "Excel",
    pdf: "PDF",
    csv: "CSV",
    document: "Word",
    media: "媒体",
    image: "图片",
    binary: "二进制",
  };
  return map[fileKind.value] || "";
});
const isDutyMobile = computed(() => isNarrow.value);
const canWrite = computed(() => meUser.value?.role !== "viewer");
const isAdmin = computed(() => meUser.value?.role === "admin");

function showToast(text: string, kind: "ok" | "err" = "ok") {
  toast.value = { text, kind };
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.value = null;
    toastTimer = 0;
  }, 1400);
}

function failFeedback(e: unknown) {
  const msg = e instanceof Error ? e.message : String(e);
  error.value = msg;
  showToast(msg, "err");
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function canarySettled(t: TargetStatus | null) {
  if (!t) return true;
  if (t.fixed || !t.running) return true;
  return (t.canary?.status_code ?? 0) > 0;
}

async function settleTarget() {
  for (let i = 0; i < 6; i++) {
    if (canarySettled(targetInfo.value)) return;
    await sleep(800);
    try {
      targetInfo.value = await getTargetStatus();
    } catch {
      return;
    }
  }
}

async function withHomeBusy<T>(label: string, work: () => Promise<T>): Promise<T> {
  feedbackBusy.value = label;
  dutyBusy.value = true;
  try {
    return await work();
  } finally {
    dutyBusy.value = false;
    feedbackBusy.value = "";
  }
}

function startHomePoll() {
  stopHomePoll();
  homePoll = window.setInterval(() => {
    if (dutyBusy.value || feedbackBusy.value) return;
    void loadDuty();
  }, 8000);
}

function stopHomePoll() {
  if (!homePoll) return;
  window.clearInterval(homePoll);
  homePoll = 0;
}

function onHomeVisible() {
  if (document.visibilityState !== "visible") return;
  if (!meUser.value || session.value) return;
  void loadDuty();
}

function cancelOnUnloadPage() {
  const id = session.value?.id;
  if (!id || (!streaming.value && !pendingApproval.value)) return;
  cancelOnUnload(id);
}

onMounted(async () => {
  window.addEventListener("keydown", onGlobalSave);
  window.addEventListener("beforeunload", cancelOnUnloadPage);
  document.addEventListener("visibilitychange", onHomeVisible);
  narrowMq = window.matchMedia("(max-width: 768px)");
  isNarrow.value = narrowMq.matches;
  narrowMq.addEventListener("change", onNarrowChange);
  const q = new URLSearchParams(location.search);
  dutyMode.value = q.get("duty") === "1" || !!q.get("alert");
  highlightAlert.value = q.get("alert") || "";
  if (isDutyMobile.value) mobilePane.value = "chat";
  try {
    const auth = await authStatus();
    needsSetup.value = auth.needs_setup;
    if (!auth.needs_setup) {
      try {
        meUser.value = await me();
      } catch {
        meUser.value = null;
      }
    }
    if (!meUser.value) return;
    await enterWorkbench();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});

async function enterWorkbench() {
  status.value = await health();
  preferredModel.value =
    status.value.model || localStorage.getItem("forge.preferredModel") || preferredModel.value;
  await refreshSessions();
  await loadDuty();
  void installAllPlugins().catch(() => undefined);
  const q = new URLSearchParams(location.search);
  const sid = q.get("session");
  if (sid) {
    const found = sessions.value.find((s) => s.id === sid);
    await hydrate(
      found || {
        id: sid,
        title: "值班会话",
        workspace_path: "",
        model: preferredModel.value,
        mode: "build",
      },
    );
  } else if (!dutyMode.value) {
    const saved = localStorage.getItem("forge.session");
    const found = sessions.value.find((s) => s.id === saved);
    if (found) await hydrate(found);
  }
}

async function onAuthed(user: AuthUser) {
  meUser.value = user;
  needsSetup.value = false;
  error.value = "";
  try {
    await enterWorkbench();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function onLogout() {
  await logoutApi();
  meUser.value = null;
  session.value = null;
  usersOpen.value = false;
}

onBeforeUnmount(() => {
  narrowMq?.removeEventListener("change", onNarrowChange);
});

function onNarrowChange(ev: MediaQueryListEvent) {
  isNarrow.value = ev.matches;
}

watch(session, async (value) => {
  if (!value) return;
  await nextTick();
  ensureEditor();
  ensureAgentTerm();
});

watch(mobilePane, async () => {
  await nextTick();
  fit?.fit();
});

function blurEditor() {
  if (editor && typeof editor.blur === "function") editor.blur();
}

watch([canEdit, isOverlay], () => {
  if (!editor) return;
  if (typeof editor.updateOptions === "function") {
    editor.updateOptions({ readOnly: !canEdit.value || isOverlay.value });
  }
  if (isOverlay.value) blurEditor();
});

watch([showEditor, mdMode, filesW, chatW, bottomH], async () => {
  await nextTick();
  layoutEditor();
  if (mdMode.value === "markdown" && isMarkdown.value) {
    editor?.updateOptions({ readOnly: false });
    editor?.focus();
  }
});

watch(
  () => messages.length,
  async () => {
    await nextTick();
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight;
  },
);

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onGlobalSave);
  window.removeEventListener("beforeunload", cancelOnUnloadPage);
  document.removeEventListener("visibilitychange", onHomeVisible);
  stopHomePoll();
  if (toastTimer) window.clearTimeout(toastTimer);
  editor?.dispose();
  term?.dispose();
  clearPreviewUrls();
});

watch(
  () => !!meUser.value && !session.value,
  (onHome) => {
    if (onHome) startHomePoll();
    else stopHomePoll();
  },
);

watch(pendingApproval, async (ev) => {
  writeDiff.value = "";
  if (ev && isDutyMobile.value) mobilePane.value = "chat";
  if (!ev || ev.tool !== "write_file" || !session.value) return;
  try {
    const args = JSON.parse(ev.arguments || "{}") as { path?: string; content?: string };
    if (args.path && typeof args.content === "string") {
      const preview = await previewWrite(session.value.id, args.path, args.content);
      writeDiff.value = preview.diff;
    }
  } catch {
    writeDiff.value = "";
  }
});

function layoutEditor() {
  requestAnimationFrame(() => {
    if (!editor || !editorEl.value) return;
    const r = editorEl.value.getBoundingClientRect();
    if (r.width > 8 && r.height > 8) {
      editor.layout({ width: Math.floor(r.width), height: Math.floor(r.height) });
    }
    fit?.fit();
  });
}

function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n));
}

function persistLayout() {
  localStorage.setItem("forge.filesW", String(filesW.value));
  localStorage.setItem("forge.chatW", String(chatW.value));
  localStorage.setItem("forge.bottomH", String(bottomH.value));
  localStorage.setItem("forge.pluginW", String(pluginW.value));
}

function onFilesStart() {
  filesStart = filesW.value;
}
function onFilesDrag(d: number) {
  filesW.value = clamp(filesStart + d, FILES_MIN, 480);
  persistLayout();
  layoutEditor();
}
function onChatStart() {
  chatStart = chatW.value;
}
function onChatDrag(d: number) {
  chatW.value = clamp(chatStart - d, CHAT_MIN, 640);
  persistLayout();
  layoutEditor();
}
function onBottomStart() {
  bottomStart = bottomH.value;
}
function onBottomDrag(d: number) {
  bottomH.value = clamp(bottomStart - d, BOTTOM_MIN, 480);
  persistLayout();
  layoutEditor();
}
function onPluginStart() {
  pluginStart = pluginW.value;
}
function onPluginDrag(d: number) {
  pluginW.value = clamp(pluginStart - d, 320, 640);
  persistLayout();
}

function ensureEditor() {
  if (!editorEl.value || editor) return;
  monaco.editor.defineTheme("forge-ink", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6f7664" },
      { token: "keyword", foreground: "e39b4a" },
      { token: "string", foreground: "9db892" },
    ],
    colors: {
      "editor.background": "#10120d",
      "editor.foreground": "#d7dacd",
      "editorLineNumber.foreground": "#5c6354",
      "editorLineNumber.activeForeground": "#e39b4a",
      "editor.lineHighlightBackground": "#1a1e16",
      "editorCursor.foreground": "#e39b4a",
      "editor.selectionBackground": "#e39b4a33",
      "editorGutter.background": "#10120d",
      "editorWidget.background": "#161913",
      "editorWidget.border": "#2c3228",
    },
  });
  editor = monaco.editor.create(editorEl.value, {
    value: "// 从左侧打开一个文件\n// 默认可打开 Markdown 与 TeX，其它格式请安装插件",
    language: "plaintext",
    theme: "forge-ink",
    readOnly: true,
    minimap: { enabled: false },
    fontFamily: "IBM Plex Mono, ui-monospace, monospace",
    fontSize: 13,
    lineHeight: 22,
    padding: { top: 12, bottom: 12 },
    automaticLayout: true,
    scrollBeyondLastLine: false,
    renderLineHighlight: "line",
    smoothScrolling: true,
  });
  editor.onDidChangeModelContent(() => {
    if (ignoreEdit) return;
    fileContent.value = editor?.getValue() ?? "";
    dirty.value = true;
  });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    void saveCurrent();
  });
  layoutEditor();
}

function ensureAgentTerm() {
  if (!termEl.value || term) return;
  term = new Terminal({
    convertEol: true,
    fontFamily: "IBM Plex Mono, ui-monospace, monospace",
    fontSize: 12,
    theme: {
      background: "#0c0d0b",
      foreground: "#c9e0b8",
      cursor: "#e39b4a",
      selectionBackground: "#e39b4a44",
    },
  });
  fit = new FitAddon();
  term.loadAddon(fit);
  term.open(termEl.value);
  fit.fit();
  term.writeln("agent sandbox log · /workspace");
}

async function refreshSessions() {
  try {
    sessions.value = await listSessions();
  } catch {
    sessions.value = [];
  }
}

async function loadDuty() {
  try {
    dutyInfo.value = await dutyStatus();
    dutyAlerts.value = await listDutyAlerts();
    targetInfo.value = await getTargetStatus();
  } catch {
    dutyInfo.value = dutyInfo.value || null;
  }
}

async function onDutyRefresh() {
  error.value = "";
  try {
    await withHomeBusy("正在刷新…", async () => {
      await loadDuty();
      await settleTarget();
    });
    showToast("刷新成功");
  } catch (e) {
    failFeedback(e);
  }
}

async function onTargetStart() {
  error.value = "";
  try {
    await withHomeBusy("正在启动业务…", async () => {
      targetInfo.value = await startTarget();
      await settleTarget();
    });
    showToast("已启动");
  } catch (e) {
    failFeedback(e);
  }
}

async function onTargetRestart() {
  error.value = "";
  try {
    await withHomeBusy("正在重启业务…", async () => {
      targetInfo.value = await restartTarget();
      await settleTarget();
    });
    showToast("已重启");
  } catch (e) {
    failFeedback(e);
  }
}

async function onNextBug() {
  error.value = "";
  try {
    await withHomeBusy("正在切换下一题…", async () => {
      targetInfo.value = await nextTargetBug();
      await loadDuty();
      await settleTarget();
    });
    showToast("已切到下一题");
  } catch (e) {
    failFeedback(e);
  }
}

async function startInvestigate(id: string) {
  dutyBusy.value = true;
  opening.value = true;
  feedbackBusy.value = "正在打开排查会话…";
  error.value = "";
  try {
    const out = await investigateAlert(id);
    if (out.resumed) {
      await hydrate(out.session);
      if (out.relocated && out.prompt) {
        messages.push({
          role: "system",
          text: `工作区已改到 ${out.session.workspace_path}`,
        });
        await dispatchPrompt(out.prompt);
      }
    } else {
      session.value = out.session;
      messages.splice(0, messages.length);
      await refreshTree(".");
      localStorage.setItem("forge.session", out.session.id);
      messages.push({
        role: "system",
        text: `值班排查 · ${out.session.workspace_path}`,
      });
    }
    await refreshSessions();
    await loadDuty();
    mobilePane.value = "chat";
    dutyMode.value = true;
    if (!out.resumed && out.prompt) await dispatchPrompt(out.prompt);
  } catch (e) {
    failFeedback(e);
  } finally {
    dutyBusy.value = false;
    opening.value = false;
    feedbackBusy.value = "";
  }
}

async function onDemoAlert() {
  error.value = "";
  try {
    await withHomeBusy("正在模拟告警…", async () => {
      const alert = await createDemoAlert();
      highlightAlert.value = alert.id;
      await loadDuty();
    });
    showToast("已加入收件箱");
  } catch (e) {
    failFeedback(e);
  }
}

async function onAckAlert(id: string) {
  error.value = "";
  try {
    await withHomeBusy("正在签收…", async () => {
      await ackDutyAlert(id);
      await loadDuty();
    });
    showToast("已签收");
  } catch (e) {
    failFeedback(e);
  }
}

async function onAssignAlert(id: string) {
  error.value = "";
  try {
    await withHomeBusy("正在指派…", async () => {
      await assignDutyAlert(id);
      await loadDuty();
    });
    showToast("已指派给我");
  } catch (e) {
    failFeedback(e);
  }
}

async function onResolveAlert(id: string) {
  error.value = "";
  try {
    await withHomeBusy("正在关闭告警…", async () => {
      await resolveDutyAlert(id);
      await loadDuty();
    });
    showToast("已关闭");
  } catch (e) {
    failFeedback(e);
  }
}

async function onTestNotify() {
  error.value = "";
  try {
    const out = await withHomeBusy("正在测推送…", () => testDutyNotify());
    showToast(out.sent.length ? `已推送到：${out.sent.join("、")}` : "未配置推送通道");
  } catch (e) {
    failFeedback(e);
  }
}

async function hydrate(s: Session) {
  session.value = await attachSession(s.id);
  const hist = await listMessages(s.id);
  messages.splice(0, messages.length, ...hist);
  workspace.value = session.value.workspace_path || s.workspace_path;
  if (session.value.pending_tool) {
    pendingApproval.value = {
      type: "approval_needed",
      tool: session.value.pending_tool,
      arguments: session.value.pending_arguments || "",
    };
  } else {
    pendingApproval.value = null;
  }
  await refreshTree(".");
  localStorage.setItem("forge.session", s.id);
  lastSeq = 0;
  if (session.value.live_run && session.value.run_status === "running") {
    streaming.value = true;
    void followRun(session.value.id);
  }
}

async function start() {
  if (!canWrite.value) return;
  error.value = "";
  opening.value = true;
  feedbackBusy.value = "正在挂载沙箱…";
  try {
    const created = await createSession(workspace.value, "build", preferredModel.value);
    session.value = created;
    messages.splice(0, messages.length);
    await refreshTree(".");
    messages.push({
      role: "system",
      text: `工作区已选定 · ${created.workspace_path}`,
    });
    localStorage.setItem("forge.session", created.id);
    await refreshSessions();
    changeWs.value = false;
  } catch (e) {
    failFeedback(e);
  } finally {
    opening.value = false;
    feedbackBusy.value = "";
  }
}

async function openExisting(s: Session) {
  error.value = "";
  opening.value = true;
  feedbackBusy.value = "正在打开会话…";
  try {
    await hydrate(s);
  } catch (e) {
    failFeedback(e);
  } finally {
    opening.value = false;
    feedbackBusy.value = "";
  }
}

async function closeWorkbench() {
  session.value = null;
  messages.splice(0);
  filePath.value = "";
  pluginNeed.value = null;
  pendingApproval.value = null;
  writeDiff.value = "";
  termOpen.value = false;
  pluginsOpen.value = false;
  rollbackOpen.value = false;
  localStorage.removeItem("forge.session");
  error.value = "";
  try {
    await withHomeBusy("正在同步值班台…", async () => {
      await refreshSessions();
      await loadDuty();
      await settleTarget();
    });
  } catch (e) {
    failFeedback(e);
  }
}

async function removeSession(s: Session, ev: Event) {
  ev.stopPropagation();
  await deleteSession(s.id);
  if (session.value?.id === s.id) await closeWorkbench();
  else await refreshSessions();
}

async function refreshTree(path: string) {
  if (!session.value) return;
  treePath.value = path;
  const data = await listTree(session.value.id, path);
  entries.value = data.entries;
  treeParent.value = data.parent;
  if (selected.value && !data.entries.some((e) => e.path === selected.value?.path && e.name === selected.value?.name)) {
    selected.value = null;
  }
}

function joinRel(name: string) {
  const base = treePath.value === "." ? "" : treePath.value.replace(/\\/g, "/");
  return base ? `${base}/${name}` : name;
}

function validName(name: string) {
  const n = name.trim();
  return !!n && !/[\\/:*?"<>|]/.test(n) && n !== "." && n !== "..";
}

async function onRestored(paths: string[]) {
  if (!session.value) return;
  await refreshTree(treePath.value);
  const current = filePath.value.replace(/\\/g, "/");
  if (current && paths.some((p) => p.replace(/\\/g, "/") === current)) {
    try {
      await loadFile(current);
    } catch {
      closeOpenFile();
    }
  }
}

async function goUp() {
  if (treeParent.value == null) return;
  selected.value = null;
  await refreshTree(treeParent.value);
}

function onEntryClick(entry: FileEntry) {
  if (entry.name === "..") {
    void goUp();
    return;
  }
  selected.value = entry;
  if (!entry.dir) {
    userPickedRel.value = normRel(entry.path);
    void loadFile(entry.path);
  }
}

function onEntryDblclick(entry: FileEntry) {
  if (entry.dir && entry.name !== "..") void refreshTree(entry.path);
}

function openCreate(kind: "file" | "folder") {
  fsPrompt.value = { kind, name: kind === "file" ? "untitled.md" : "新建文件夹" };
}

function askRename() {
  if (!selected.value || selected.value.name === "..") return;
  fsPrompt.value = { kind: "rename", name: selected.value.name };
}

async function confirmCreate() {
  if (!session.value || !fsPrompt.value || !validName(fsPrompt.value.name)) return;
  if (fsPrompt.value.kind === "rename") {
    await confirmRename();
    return;
  }
  fsBusy.value = true;
  error.value = "";
  try {
    const path = joinRel(fsPrompt.value.name.trim());
    if (fsPrompt.value.kind === "folder") {
      await mkdirEntry(session.value.id, path);
    } else {
      await createFileEntry(session.value.id, path);
    }
    const kind = fsPrompt.value.kind;
    fsPrompt.value = null;
    await refreshTree(treePath.value);
    const created = entries.value.find((e) => e.path === path);
    if (created) selected.value = created;
    if (kind === "file") await loadFile(path);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    fsBusy.value = false;
  }
}

async function confirmRename() {
  if (!session.value || !selected.value || !fsPrompt.value || !validName(fsPrompt.value.name)) return;
  fsBusy.value = true;
  error.value = "";
  try {
    const oldPath = selected.value.path;
    const wasDir = selected.value.dir;
    const result = await renameEntry(session.value.id, oldPath, fsPrompt.value.name.trim());
    fsPrompt.value = null;
    await refreshTree(treePath.value);
    const renamed = entries.value.find((e) => e.path === result.path);
    if (renamed) selected.value = renamed;
    if (!wasDir && (filePath.value === oldPath || pendingPath.value === oldPath)) {
      await loadFile(result.path);
    } else if (wasDir && (filePath.value === oldPath || filePath.value.startsWith(`${oldPath}/`))) {
      const next = filePath.value === oldPath ? result.path : result.path + filePath.value.slice(oldPath.length);
      await loadFile(next);
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    fsBusy.value = false;
  }
}

function askDelete() {
  if (!selected.value || selected.value.name === "..") return;
  deleteTarget.value = selected.value;
}

async function confirmDelete() {
  if (!session.value || !deleteTarget.value) return;
  fsBusy.value = true;
  error.value = "";
  try {
    const path = deleteTarget.value.path;
    const wasDir = deleteTarget.value.dir;
    await deleteEntry(session.value.id, path);
    deleteTarget.value = null;
    selected.value = null;
    if (filePath.value === path || (wasDir && filePath.value.startsWith(`${path}/`))) {
      filePath.value = "";
      pluginNeed.value = null;
      fileBinary.value = false;
      setEditorText("");
    }
    await refreshTree(treePath.value);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    fsBusy.value = false;
  }
}

function clearPreviewUrls() {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value);
  imageUrl.value = "";
  pdfUrl.value = "";
}

async function loadFile(path: string) {
  if (!session.value) return;
  const token = ++loadToken;
  error.value = "";
  pluginNeed.value = null;
  pendingPath.value = path;
  sheetNames.value = [];
  sheetMap.value = {};
  csvRows.value = [];
  docHtml.value = "<p></p>";
  clearPreviewUrls();
  try {
    const data = await readFile(session.value.id, path);
    if (token !== loadToken) return;
    filePath.value = data.path;
    fileKind.value = data.kind;
    fileBinary.value = data.binary;
    fileSize.value = data.size || 0;
    dirty.value = false;
    if (data.kind === "image" || data.kind === "pdf" || data.kind === "binary" || data.kind === "media") {
      const blob = await readBlob(session.value.id, path);
      if (token !== loadToken) return;
      const url = URL.createObjectURL(blob);
      if (data.kind === "pdf") pdfUrl.value = url;
      else imageUrl.value = url;
      setEditorText("");
      blurEditor();
    } else if (data.kind === "document") {
      const blob = await readBlob(session.value.id, path);
      if (token !== loadToken) return;
      docHtml.value = await docxToHtml(await blob.arrayBuffer());
      if (token !== loadToken) return;
      setEditorText("");
      blurEditor();
    } else if (data.kind === "spreadsheet") {
      const blob = await readBlob(session.value.id, path);
      if (token !== loadToken) return;
      const book = parseWorkbook(await blob.arrayBuffer());
      const names = book.names.length ? book.names : ["Sheet1"];
      const sheets = { ...book.sheets };
      if (!sheets[names[0]]?.length) sheets[names[0]] = emptyGrid();
      sheetNames.value = names;
      sheetMap.value = sheets;
      activeSheet.value = names[0];
      setEditorText("");
      blurEditor();
    } else if (data.kind === "csv") {
      csvRows.value = parseCsv(data.content, path.toLowerCase().endsWith(".tsv"));
      if (!csvRows.value.length) csvRows.value = emptyGrid();
      fileContent.value = data.content;
      setEditorText("");
      blurEditor();
    } else {
      fileContent.value = data.content;
      setEditorText(data.content);
      const model = editor?.getModel();
      if (model) monaco.editor.setModelLanguage(model, languageFor(data.path));
      if (/\.(md|markdown)$/i.test(data.path)) mdMode.value = "preview";
    }
  } catch (e) {
    if (token !== loadToken) return;
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function setEditorText(value: string) {
  if (!editor) return;
  ignoreEdit = true;
  editor.setValue(value);
  ignoreEdit = false;
  dirty.value = false;
}

async function saveCurrent() {
  if (!session.value || !filePath.value || !canEdit.value) return;
  saving.value = true;
  error.value = "";
  try {
    if (fileKind.value === "spreadsheet") {
      const ext = filePath.value.toLowerCase().endsWith(".xls") ? "xls" : "xlsx";
      const buf = writeWorkbook(
        { names: sheetNames.value, sheets: sheetMap.value },
        ext,
      );
      const out = await saveBlob(session.value.id, filePath.value, buf);
      fileSize.value = out.size;
    } else if (fileKind.value === "csv") {
      const text = stringifyCsv(csvRows.value, filePath.value.toLowerCase().endsWith(".tsv"));
      fileContent.value = text;
      await saveFile(session.value.id, filePath.value, text);
    } else if (fileKind.value === "document") {
      const buf = await htmlToDocx(docHtml.value);
      const out = await saveBlob(session.value.id, filePath.value, buf);
      fileSize.value = out.size;
    } else {
      const content = editor?.getValue() ?? fileContent.value;
      fileContent.value = content;
      await saveFile(session.value.id, filePath.value, content);
    }
    dirty.value = false;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

function onCellInput(ri: number, ci: number, ev: Event) {
  setCell(ri, ci, (ev.target as HTMLInputElement).value);
}

function setCell(ri: number, ci: number, value: string) {
  dirty.value = true;
  if (fileKind.value === "csv") {
    ensureCell(csvRows.value, ri, ci);
    csvRows.value[ri][ci] = value;
    return;
  }
  const name = activeSheet.value || "Sheet1";
  if (!sheetMap.value[name]) {
    sheetMap.value[name] = emptyGrid();
    if (!sheetNames.value.includes(name)) sheetNames.value = [...sheetNames.value, name];
    activeSheet.value = name;
  }
  ensureCell(sheetMap.value[name], ri, ci);
  sheetMap.value[name][ri][ci] = value;
}

function ensureCell(rows: string[][], ri: number, ci: number) {
  while (rows.length <= ri) {
    const cols = rows[0]?.length || ci + 1;
    rows.push(Array.from({ length: cols }, () => ""));
  }
  for (const row of rows) {
    while (row.length <= ci) row.push("");
  }
}

function addSheetRow() {
  dirty.value = true;
  if (fileKind.value === "csv") {
    const cols = Math.max(1, ...csvRows.value.map((r) => r.length), 1);
    csvRows.value = [...csvRows.value, Array.from({ length: cols }, () => "")];
    return;
  }
  const name = activeSheet.value;
  const rows = (sheetMap.value[name] || []).map((r) => [...r]);
  const cols = Math.max(1, ...rows.map((r) => r.length), 1);
  rows.push(Array.from({ length: cols }, () => ""));
  sheetMap.value = { ...sheetMap.value, [name]: rows };
}

function addSheetCol() {
  dirty.value = true;
  if (fileKind.value === "csv") {
    csvRows.value = csvRows.value.map((r) => [...r, ""]);
    return;
  }
  const name = activeSheet.value;
  const rows = (sheetMap.value[name] || []).map((r) => [...r, ""]);
  sheetMap.value = { ...sheetMap.value, [name]: rows };
}

function onGlobalSave(ev: KeyboardEvent) {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
    ev.preventDefault();
    void saveCurrent();
  }
}

async function onPluginInstalled(id: string) {
  if (pluginNeed.value?.id === id && pendingPath.value) {
    await loadFile(pendingPath.value);
  }
}

async function pickModel(id: string) {
  preferredModel.value = id;
  localStorage.setItem("forge.preferredModel", id);
  try {
    await setDefaultModel(id);
  } catch {
    /* viewer cannot persist server default; local + session still switch */
  }
  if (!session.value) return;
  session.value = await setSessionModel(session.value.id, id);
}

async function toggleMode() {
  if (!session.value) return;
  const next = session.value.mode === "plan" ? "build" : "plan";
  session.value = await setMode(session.value.id, next);
}

let fsTimer: ReturnType<typeof setTimeout> | null = null;
const fsQueue: AgentEvent[] = [];

function normRel(path: string) {
  return path.replace(/\\/g, "/").replace(/^\.\/+/, "").replace(/^\/+/, "");
}

function parentDir(path: string) {
  const n = normRel(path);
  const i = n.lastIndexOf("/");
  return i <= 0 ? "." : n.slice(0, i);
}

function sameRel(a: string, b: string) {
  return normRel(a) === normRel(b);
}

function queueFsChange(ev: AgentEvent) {
  fsQueue.push(ev);
  if (fsTimer) clearTimeout(fsTimer);
  fsTimer = setTimeout(() => {
    const batch = fsQueue.splice(0, fsQueue.length);
    void applyFsChanges(batch);
  }, 80);
}

function closeOpenFile() {
  filePath.value = "";
  fileKind.value = "";
  fileContent.value = "";
  dirty.value = false;
  selected.value = null;
  userPickedRel.value = "";
  sheetNames.value = [];
  sheetMap.value = {};
  csvRows.value = [];
  docHtml.value = "<p></p>";
  setEditorText("");
  clearPreviewUrls();
}

async function applyFsChanges(batch: AgentEvent[]) {
  if (!session.value || !batch.length) return;
  const lastWrite = [...batch].reverse().find((e) => e.tool === "write_file" && e.path);
  const lastDelete = [...batch].reverse().find((e) => e.tool === "delete_file" && e.path);
  const hadBash = batch.some((e) => e.tool === "bash");

  if (lastDelete && filePath.value && sameRel(filePath.value, lastDelete.path || "")) {
    closeOpenFile();
  }

  try {
    const openedRel = userPickedRel.value || (filePath.value ? normRel(filePath.value) : "");
    const keepUserFile =
      Boolean(openedRel) &&
      Boolean(lastWrite?.path) &&
      !sameRel(openedRel, lastWrite.path || "");
    if (lastWrite?.path && !keepUserFile && (!dirty.value || sameRel(filePath.value, lastWrite.path))) {
      await refreshTree(parentDir(lastWrite.path));
      await loadFile(lastWrite.path);
      const opened = entries.value.find((e) => !e.dir && sameRel(e.path, lastWrite.path || ""));
      if (opened) selected.value = opened;
    } else {
      await refreshTree(treePath.value);
      if (hadBash && filePath.value && !dirty.value) {
        await loadFile(filePath.value);
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function onEvent(ev: AgentEvent) {
  if (typeof ev.seq === "number") lastSeq = ev.seq;
  if (ev.type === "ping") return;
  if (ev.type === "text_delta" && ev.text) {
    const last = messages[messages.length - 1];
    if (last?.role === "assistant" && !last.kind) last.text += ev.text;
    else messages.push({ role: "assistant", text: ev.text });
  } else if (ev.type === "tool_called") {
    messages.push({ role: "assistant", kind: "md", text: formatToolCall(ev.tool || "tool", ev.arguments ?? "") });
    if (ev.tool === "bash") term?.writeln(`\r\n$ ${ev.arguments ?? ""}`);
  } else if (ev.type === "tool_output") {
    if (ev.output) term?.write(ev.output.replace(/\n/g, "\r\n") + "\r\n");
    messages.push({ role: "assistant", kind: "md", text: formatToolOutput(ev.output?.slice(0, 4000) || "(ok)") });
  } else if (ev.type === "fs_changed") {
    queueFsChange(ev);
  } else if (ev.type === "approval_needed") {
    pendingApproval.value = ev;
    streaming.value = false;
  } else if (ev.type === "cancelled") {
    streaming.value = false;
    if (!userStopping) return;
    lastRunStopped.value = true;
    const last = messages[messages.length - 1];
    if (!(last?.role === "system" && last.text === "已停止")) {
      messages.push({ role: "system", text: ev.message || "已停止" });
    }
  } else if (ev.type === "error") {
    error.value = ev.message || "Agent error";
    streaming.value = false;
  } else if (ev.type === "done") {
    streaming.value = false;
    void refreshTree(treePath.value);
    if (ev.text) {
      const last = messages[messages.length - 1];
      if (!(last?.role === "assistant" && !last.kind && last.text.includes(ev.text))) {
        if (!(last?.role === "assistant" && !last.kind && last.text.length > 0)) {
          messages.push({ role: "assistant", text: ev.text });
        }
      }
    }
  }
}

async function followRun(sessionId: string) {
  while (streaming.value && !pendingApproval.value && !userStopping) {
    try {
      if (!streamCtl || streamCtl.signal.aborted) streamCtl = new AbortController();
      await streamSse(
        `/api/sessions/${sessionId}/events?after=${lastSeq}`,
        null,
        onEvent,
        streamCtl.signal,
      );
    } catch (e) {
      if (isAbort(e)) return;
    }
    if (!streaming.value || pendingApproval.value || userStopping) return;
    await sleep(400);
  }
}

async function dispatchPrompt(text: string, retry = false) {
  if (!session.value || !text.trim() || streaming.value) return;
  if (pendingApproval.value) {
    error.value = "Forge 在等你批准工具。请点允许或拒绝，不要另发一句。";
    return;
  }
  if (!retry) messages.push({ role: "user", text: text.trim() });
  lastRunStopped.value = false;
  userStopping = false;
  lastSeq = 0;
  streaming.value = true;
  error.value = "";
  streamCtl = new AbortController();
  try {
    await streamSse(
      `/api/sessions/${session.value.id}/messages`,
      { content: text.trim(), open_path: filePath.value || "" },
      onEvent,
      streamCtl.signal,
    );
    if (streaming.value && !pendingApproval.value && !userStopping) {
      await followRun(session.value.id);
    }
  } catch (e) {
    if (isAbort(e)) {
      if (userStopping) {
        lastRunStopped.value = true;
        streaming.value = false;
        return;
      }
      if (session.value) await followRun(session.value.id);
      return;
    }
    if (session.value && streaming.value && !userStopping) {
      await followRun(session.value.id);
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
    streaming.value = false;
  }
}

function isAbort(e: unknown) {
  return e instanceof DOMException && e.name === "AbortError";
}

async function stopRun() {
  if (!session.value || (!streaming.value && !pendingApproval.value)) return;
  userStopping = true;
  lastRunStopped.value = true;
  pendingApproval.value = null;
  try {
    await cancelSession(session.value.id);
  } catch {
    /* UI still stops */
  }
  streamCtl?.abort();
  streaming.value = false;
  const last = messages[messages.length - 1];
  if (!(last?.role === "system" && last.text === "已停止")) {
    messages.push({ role: "system", text: "已停止" });
  }
}

function lastUserIndex() {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "user") return i;
  }
  return -1;
}

function showRetry(i: number, m: { role: string }) {
  return m.role === "user" && i === lastUserIndex() && lastRunStopped.value && !streaming.value;
}

async function retryLast() {
  const i = lastUserIndex();
  if (i < 0 || streaming.value) return;
  await dispatchPrompt(messages[i].text, true);
}

async function sendDutyChip(kind: "locate" | "test" | "push" | "restart") {
  const prompts = {
    locate:
      "先不要改代码。根据金丝雀、堆栈和进程日志，指出文件、函数、出现场景、报错内容和可能原因。",
    test: "不要重启正在跑的服务。请 run_tests，只跑业务仓库 tests。把 pytest 输出贴出来。",
    push: "若测试已通过，请 git_commit 然后 git_push origin。不要 force。推送成功后告诉我 Gitee 仓库。",
    restart: "测试已通过后再 restart_service。用返回的 health/canary 判断这题是否修好。",
  };
  await dispatchPrompt(prompts[kind]);
}

function isContinueText(text: string) {
  return /^(请你)?继续$|^continue$/i.test(text.trim());
}

function titleFromPrompt(text: string) {
  const one = text.trim().replace(/\s+/g, " ");
  if (!one) return "";
  return one.length > 40 ? `${one.slice(0, 40).trimEnd()}…` : one;
}

function isPlaceholderTitle(title: string) {
  const t = (title || "").trim();
  const ws = session.value?.workspace_path.replace(/\\/g, "/").split("/").pop() || "";
  return !t || t === "New session" || t === ws || t.startsWith("值班 ·");
}

async function copyMessage(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    showToast("已复制");
  } catch {
    showToast("复制失败", "err");
  }
}

function onPromptKey(ev: KeyboardEvent) {
  if (ev.key !== "Enter" || ev.shiftKey) return;
  ev.preventDefault();
  void send();
}

async function send() {
  if (streaming.value) return;
  const text = prompt.value.trim();
  if (!text) return;
  if (pendingApproval.value && isContinueText(text)) {
    prompt.value = "";
    await decide(true);
    return;
  }
  if (session.value && isPlaceholderTitle(session.value.title)) {
    const titled = titleFromPrompt(text);
    if (titled) session.value.title = titled;
  }
  prompt.value = "";
  await dispatchPrompt(text);
}

async function decide(approved: boolean, scope: "once" | "session" = "once") {
  if (!session.value || !pendingApproval.value) return;
  pendingApproval.value = null;
  lastRunStopped.value = false;
  userStopping = false;
  lastSeq = 0;
  streaming.value = true;
  streamCtl = new AbortController();
  try {
    await streamSse(
      `/api/sessions/${session.value.id}/approvals`,
      { approved, scope },
      onEvent,
      streamCtl.signal,
    );
    if (streaming.value && !pendingApproval.value && !userStopping) {
      await followRun(session.value.id);
    }
  } catch (e) {
    if (isAbort(e)) {
      if (userStopping) {
        lastRunStopped.value = true;
        streaming.value = false;
      } else if (session.value) {
        await followRun(session.value.id);
      }
      return;
    }
    if (session.value && streaming.value && !userStopping) {
      await followRun(session.value.id);
      return;
    }
    error.value = e instanceof Error ? e.message : String(e);
    streaming.value = false;
  }
}
</script>

<template>
  <div
    class="forge-shell relative flex h-full flex-col"
    :class="{
      'duty-mobile': isDutyMobile,
      'pane-chat': isDutyMobile && mobilePane === 'chat',
      'pane-files': isDutyMobile && mobilePane === 'files',
      'pane-log': isDutyMobile && mobilePane === 'log',
    }"
  >
    <header
      class="relative z-50 flex items-center justify-between border-b border-line/80 px-5 py-3 backdrop-blur-sm"
    >
      <div class="flex items-center gap-3">
        <div
          class="flex h-9 w-9 items-center justify-center rounded-lg border border-ember/30 bg-panel shadow-[0_0_24px_rgba(227,155,74,0.18)]"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4 17h16l-3.2-8.5H7.2L4 17Z" fill="#e39b4a" />
            <circle cx="12" cy="6.2" r="1.6" fill="#f3b56a" />
          </svg>
        </div>
        <div>
          <div class="font-serif text-[17px] font-bold tracking-tight text-mist">Forge</div>
          <div class="text-[11px] text-mute">
            {{ dutyMode ? "值班台 · 对话 / diff / 批准" : "本地编码工作室 · Docker 沙箱" }}
          </div>
        </div>
        <div
          v-if="meUser && investigatingProject"
          class="ml-6 hidden border-l border-line pl-6 md:block"
        >
          <div class="text-[11px] text-mute">当前正在排查</div>
          <div class="text-sm font-medium text-mist">{{ investigatingProject }}</div>
        </div>
      </div>
      <div class="flex items-center gap-2 text-xs">
        <span v-if="meUser" class="hidden text-mute sm:inline">{{ meUser.display_name }} · {{ meUser.role }}</span>
        <button
          v-if="isAdmin"
          class="rounded-full border border-line px-3 py-1.5 text-mute hover:text-mist"
          type="button"
          @click="usersOpen = !usersOpen"
        >
          账号
        </button>
        <button
          v-if="meUser"
          class="rounded-full border border-line px-3 py-1.5 text-mute hover:text-mist"
          type="button"
          @click="onLogout"
        >
          退出
        </button>
        <ModelMenu v-if="meUser" :model="activeModel" @pick="pickModel" />
        <button
          v-if="session && !isDutyMobile"
          class="rounded-full border border-line px-3 py-1.5 text-mute transition-colors hover:text-mist"
          :class="termOpen ? 'border-ember/50 text-ember' : ''"
          type="button"
          @click="termOpen = !termOpen"
        >
          终端
        </button>
        <button
          v-if="session && !isDutyMobile"
          class="rounded-full border border-line px-3 py-1.5 text-mute transition-colors hover:text-mist"
          :class="pluginsOpen ? 'border-ember/50 text-ember' : ''"
          type="button"
          @click="pluginsOpen = !pluginsOpen"
        >
          插件
        </button>
        <button
          v-if="session"
          class="rounded-full border border-line px-3 py-1.5 text-mute transition-colors hover:text-mist"
          :class="rollbackOpen ? 'border-sage/50 text-sage' : ''"
          type="button"
          @click="rollbackOpen = true"
        >
          回退
        </button>
        <button
          v-if="session"
          class="rounded-full border border-line px-3 py-1.5 text-mute transition-colors hover:text-mist"
          type="button"
          @click="closeWorkbench"
        >
          返回
        </button>
        <div v-if="session && !isDutyMobile" class="flex rounded-full border border-line bg-panel p-0.5">
          <button
            type="button"
            class="rounded-full px-3 py-1.5 transition-colors duration-200"
            :class="session.mode === 'plan' ? 'bg-ember text-ink' : 'text-mute hover:text-mist'"
            @click="session.mode !== 'plan' && toggleMode()"
          >
            计划
          </button>
          <button
            type="button"
            class="rounded-full px-3 py-1.5 transition-colors duration-200"
            :class="session.mode === 'build' ? 'bg-ember text-ink' : 'text-mute hover:text-mist'"
            @click="session.mode !== 'build' && toggleMode()"
          >
            构建
          </button>
        </div>
      </div>
    </header>

    <AuthGate v-if="!meUser" :needs-setup="needsSetup" @done="onAuthed" />

    <nav
      v-if="meUser && session && isDutyMobile"
      class="duty-tabs z-40 flex border-b border-line bg-panel"
    >
      <button
        type="button"
        class="flex-1 py-3 text-sm"
        :class="mobilePane === 'chat' ? 'text-ember' : 'text-mute'"
        @click="mobilePane = 'chat'"
      >
        对话
      </button>
      <button
        type="button"
        class="flex-1 py-3 text-sm"
        :class="mobilePane === 'files' ? 'text-ember' : 'text-mute'"
        @click="mobilePane = 'files'"
      >
        文件
      </button>
      <button
        type="button"
        class="flex-1 py-3 text-sm"
        :class="mobilePane === 'log' ? 'text-ember' : 'text-mute'"
        @click="mobilePane = 'log'"
      >
        日志
      </button>
    </nav>

    <div v-if="meUser && !session" class="relative min-h-0 flex-1 overflow-auto">
      <div class="forge-grid pointer-events-none absolute inset-0" />
      <div
        class="relative mx-auto grid w-full max-w-5xl items-start gap-8 px-6 py-8 lg:grid-cols-[1.05fr_0.95fr]"
      >
        <div>
          <p class="text-[11px] font-medium uppercase tracking-[0.22em] text-ember">
            on-call coding agent
          </p>
          <h1 class="mt-4 font-serif text-5xl font-extrabold leading-[1.05] tracking-tight text-mist">
            值班机上的<br />编码 agent。
          </h1>
          <p class="mt-5 max-w-md text-[15px] leading-7 text-mute">
            计算留在值班电脑。手机只做对话、看 diff、批准。企微 / 钉钉只负责把人点进这一页。
          </p>
          <ul class="mt-8 space-y-3 text-sm text-mist/85">
            <li class="flex items-center gap-3">
              <span class="h-px w-6 bg-ember/70" />
              告警进收件箱，一点排查就开会话
            </li>
            <li class="flex items-center gap-3">
              <span class="h-px w-6 bg-ember/70" />
              写文件前人工确认，卡片链到值班台
            </li>
            <li class="flex items-center gap-3">
              <span class="h-px w-6 bg-ember/70" />
              命令在 Linux 容器里跑，宿主上改文件
            </li>
          </ul>
        </div>

        <div class="rounded-2xl border border-line bg-panel/90 p-6 shadow-[0_24px_80px_rgba(0,0,0,0.45)] backdrop-blur">
          <p class="text-sm font-medium text-mist">选择工作区</p>
          <p class="mt-1 text-xs leading-5 text-mute">浏览磁盘上的文件夹。C 盘不可用。</p>
          <div class="mt-4">
            <FolderPicker v-model="workspace" @valid="workspaceOk = $event" />
          </div>
          <button
            class="mt-4 w-full rounded-xl bg-ember px-4 py-3 text-sm font-semibold text-ink transition-colors duration-200 hover:bg-ember-hot disabled:opacity-60"
            type="button"
            :disabled="opening || !workspaceOk || !canWrite"
            @click="start"
          >
            {{ opening ? "正在挂载沙箱…" : "打开工作区" }}
          </button>
          <p v-if="error" class="mt-4 text-sm text-danger">{{ error }}</p>
          <p v-else-if="!hasKey" class="mt-4 text-sm text-ember">
            未检测到 API key。请在项目根目录 `.env` 填写 `DEEPSEEK_API_KEY` 后重启 API。
          </p>
          <div v-if="sessions.length" class="mt-4 border-t border-line pt-3">
            <p class="text-sm font-medium text-mist">最近会话</p>
            <ul class="mt-2 space-y-2">
              <li v-for="s in sessions.slice(0, 1)" :key="s.id" class="flex gap-2">
                <button
                  type="button"
                  class="min-w-0 flex-1 rounded-xl border border-line bg-bg-elev px-3 py-2 text-left transition-colors hover:border-ember/50"
                  :disabled="opening"
                  @click="openExisting(s)"
                >
                  <span class="block truncate text-sm text-mist">{{ s.title }}</span>
                  <span class="block truncate font-mono text-[10px] text-mute">{{
                    s.workspace_path
                  }}</span>
                </button>
                <button
                  type="button"
                  class="shrink-0 rounded-xl border border-line px-2 text-[11px] text-danger hover:bg-panel-2"
                  @click="removeSession(s, $event)"
                >
                  删除
                </button>
              </li>
            </ul>
          </div>
        </div>
      </div>
      <div class="relative mx-auto w-full max-w-5xl space-y-4 px-6 pb-8">
        <TargetCard
          :target="targetInfo"
          :latest="dutyAlerts[0] || null"
          :busy="dutyBusy"
          :can-write="canWrite"
          @start="onTargetStart"
          @restart="onTargetRestart"
          @next-bug="onNextBug"
          @refresh="onDutyRefresh"
        />
        <DutyInbox
          :alerts="dutyAlerts"
          :status="dutyInfo"
          :busy="dutyBusy || opening"
          :highlight-id="highlightAlert"
          :can-write="canWrite"
            @investigate="startInvestigate"
            @refresh="onDutyRefresh"
            @demo="onDemoAlert"
            @test-notify="onTestNotify"
            @resolve="onResolveAlert"
            @ack="onAckAlert"
            @assign="onAssignAlert"
        />
      </div>
    </div>

    <div v-else-if="meUser" class="workbench relative flex min-h-0 flex-1 p-3">
      <aside
        class="flex min-h-0 shrink-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel"
        :style="{ width: filesW + 'px' }"
      >
        <div class="flex items-center justify-between border-b border-line px-3 py-2.5">
          <span class="text-[11px] font-medium uppercase tracking-[0.16em] text-mute">文件</span>
          <button
            class="truncate text-[11px] text-ember transition-colors hover:text-ember-hot"
            type="button"
            :title="session.workspace_path"
            @click="changeWs = true"
          >
            {{ workspaceName }}
          </button>
        </div>
        <div class="flex items-center gap-0.5 border-b border-line px-2 py-1.5">
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md text-mute transition-colors hover:bg-panel-2 hover:text-ember disabled:opacity-30"
            title="上一级"
            :disabled="treeParent == null"
            @click="goUp"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M14 18V9.8H18L12 4 6 9.8h4V18h4Z" fill="currentColor" />
            </svg>
          </button>
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md text-mute transition-colors hover:bg-panel-2 hover:text-ember"
            title="添加文件夹"
            @click="openCreate('folder')"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M3 7.5A1.5 1.5 0 0 1 4.5 6h5l1.2 1.6H19.5A1.5 1.5 0 0 1 21 9.1v8.4a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5v-10Z"
                stroke="currentColor"
                stroke-width="1.6"
              />
              <path d="M12 11v6M9 14h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
          </button>
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md text-mute transition-colors hover:bg-panel-2 hover:text-ember"
            title="添加文件"
            @click="openCreate('file')"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M7 3.5h7.2L19 8.2V20a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 6 20V5A1.5 1.5 0 0 1 7.5 3.5H7Z"
                stroke="currentColor"
                stroke-width="1.6"
              />
              <path d="M12 11v6M9 14h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
          </button>
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md text-mute transition-colors hover:bg-panel-2 hover:text-ember disabled:opacity-30"
            title="重命名"
            :disabled="!selected || selected.name === '..'"
            @click="askRename"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M5 19.5h5.2M5 19.5 16.4 8.1a1.6 1.6 0 0 1 2.3 0l.7.7a1.6 1.6 0 0 1 0 2.3L8.2 19.5H5Z"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md text-mute transition-colors hover:bg-panel-2 hover:text-danger disabled:opacity-30"
            title="删除"
            :disabled="!selected || selected.name === '..'"
            @click="askDelete"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M8 7h8m-6.5 3v7m5-7v7M9.2 7l.6-2h4.4l.6 2M6.5 7h11l-.8 12.2A1.5 1.5 0 0 1 15.2 20.5H8.8a1.5 1.5 0 0 1-1.5-1.3L6.5 7Z"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
              />
            </svg>
          </button>
        </div>
        <div class="panel-scroll min-h-0 flex-1 overflow-auto p-2 text-[13px]">
          <p class="mb-2 truncate px-2 font-mono text-[10px] text-mute">
            {{ treePath === "." ? "/" : treePath }}
          </p>
          <button
            v-for="entry in entries"
            :key="entry.path + entry.name"
            type="button"
            class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-150 hover:bg-panel-2"
            :class="
              selected && selected.path === entry.path && selected.name === entry.name
                ? 'bg-ember/12 text-ember'
                : 'text-mist/90'
            "
            @click="onEntryClick(entry)"
            @dblclick="onEntryDblclick(entry)"
            :title="entry.dir && entry.name !== '..' ? '双击打开文件夹' : ''"
          >
            <svg
              v-if="entry.dir"
              class="shrink-0 text-mute"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
            >
              <path
                d="M3 7.5A1.5 1.5 0 0 1 4.5 6h5l1.2 1.6H19.5A1.5 1.5 0 0 1 21 9.1v8.4a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5v-10Z"
                stroke="currentColor"
                stroke-width="1.6"
              />
            </svg>
            <svg
              v-else
              class="shrink-0 text-mute"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
            >
              <path
                d="M7 3.5h7.2L19 8.2V20a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 6 20V5A1.5 1.5 0 0 1 7.5 3.5H7Z"
                stroke="currentColor"
                stroke-width="1.6"
              />
            </svg>
            <span class="truncate">{{ entry.name }}</span>
          </button>
        </div>
      </aside>

      <ResizeHandle v-if="!isDutyMobile" axis="x" @start="onFilesStart" @drag="onFilesDrag" />

      <main class="flex min-h-0 min-w-[280px] flex-1 flex-col overflow-hidden rounded-2xl border border-line bg-panel">
        <div class="flex items-center justify-between gap-3 border-b border-line px-4 py-2">
          <span class="min-w-0 truncate rounded-md bg-bg-elev px-2 py-1 font-mono text-[11px] text-mist/80">
            {{ filePath || "未选择文件" }}
          </span>
          <div class="flex shrink-0 items-center gap-2">
            <div
              v-if="isMarkdown && filePath"
              class="flex rounded-md bg-[#1a1c18] p-[3px] text-[12px]"
            >
              <button
                type="button"
                class="rounded-[5px] px-2.5 py-1 transition-colors"
                :class="mdMode === 'preview' ? 'bg-[#3a3d36] text-white' : 'text-[#8d9280] hover:text-mist'"
                @click="mdMode = 'preview'"
              >
                Preview
              </button>
              <button
                type="button"
                class="rounded-[5px] px-2.5 py-1 transition-colors"
                :class="mdMode === 'markdown' ? 'bg-[#3a3d36] text-white' : 'text-[#8d9280] hover:text-mist'"
                @click="mdMode = 'markdown'"
              >
                Markdown
              </button>
            </div>
            <span v-else-if="kindLabel" class="text-[10px] uppercase tracking-[0.16em] text-mute">
              {{ kindLabel }}{{ canEdit ? " · 可编辑" : " · 只读" }}
            </span>
            <span v-else-if="isTex" class="text-[10px] uppercase tracking-[0.16em] text-mute">LaTeX</span>
            <button
              v-if="canEdit && dirty"
              type="button"
              class="rounded-md bg-ember px-2 py-1 text-[11px] font-medium text-ink"
              :disabled="saving"
              @click="saveCurrent"
            >
              {{ saving ? "保存中" : "保存" }}
            </button>
          </div>
        </div>
        <div class="relative min-h-[160px] flex-1 duty-editor">
          <div
            ref="editorEl"
            class="absolute inset-0"
            :class="showPreview || isOverlay ? 'pointer-events-none' : ''"
          />
          <div
            v-show="showPreview"
            class="md-preview panel-scroll absolute inset-0 z-10 overflow-auto bg-panel px-8 py-6"
            v-html="renderedMd"
          />
          <div
            v-if="fileKind === 'image'"
            class="absolute inset-0 z-30 flex items-center justify-center bg-bg p-6"
          >
            <img v-if="imageUrl" :src="imageUrl" :alt="filePath" class="max-h-full max-w-full rounded-lg" />
          </div>
          <div v-else-if="fileKind === 'pdf'" class="absolute inset-0 z-30 bg-bg">
            <iframe v-if="pdfUrl" :src="pdfUrl" class="h-full w-full border-0" title="PDF" />
          </div>
          <div
            v-else-if="fileKind === 'spreadsheet' || fileKind === 'csv'"
            class="absolute inset-0 z-30 flex flex-col bg-panel"
          >
            <div class="flex items-center gap-2 border-b border-line px-3 py-1.5">
              <div v-if="fileKind === 'spreadsheet' && sheetNames.length" class="flex min-w-0 flex-1 gap-1 overflow-auto">
                <button
                  v-for="name in sheetNames"
                  :key="name"
                  type="button"
                  class="rounded-md px-2 py-1 text-[11px]"
                  :class="activeSheet === name ? 'bg-ember/20 text-ember' : 'text-mute hover:text-mist'"
                  @click="activeSheet = name"
                >
                  {{ name }}
                </button>
              </div>
              <span class="shrink-0 text-[11px] text-mute">点单元格编辑 · Ctrl+S 保存</span>
              <button type="button" class="rounded-md border border-line px-2 py-0.5 text-[11px] text-mute hover:text-mist" @click="addSheetRow">
                + 行
              </button>
              <button type="button" class="rounded-md border border-line px-2 py-0.5 text-[11px] text-mute hover:text-mist" @click="addSheetCol">
                + 列
              </button>
            </div>
            <div class="panel-scroll min-h-0 flex-1 overflow-auto p-3">
              <table class="sheet-table">
                <thead>
                  <tr>
                    <th class="sheet-axis"></th>
                    <th v-for="ci in sheetCols" :key="'c' + ci" class="sheet-axis">{{ colLabel(ci - 1) }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in fileKind === 'csv' ? csvRows : sheetRows" :key="ri">
                    <th class="sheet-axis">{{ ri + 1 }}</th>
                    <td v-for="ci in sheetCols" :key="ci">
                      <input
                        class="sheet-cell"
                        :value="row[ci - 1] ?? ''"
                        autocomplete="off"
                        spellcheck="false"
                        @mousedown.stop
                        @focus="blurEditor()"
                        @input="onCellInput(ri, ci - 1, $event)"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-else-if="fileKind === 'document'" class="absolute inset-0 z-30">
            <DocxEditor
              :key="filePath"
              :html="docHtml"
              @update:html="docHtml = $event"
              @dirty="dirty = true"
              @focus="blurEditor()"
            />
          </div>
          <div
            v-else-if="fileKind === 'media'"
            class="absolute inset-0 z-30 flex items-center justify-center bg-bg p-6"
          >
            <video
              v-if="/\.(mp4|mov|avi|webm)$/i.test(filePath)"
              :src="imageUrl"
              controls
              class="max-h-full max-w-full rounded-lg"
            />
            <audio v-else :src="imageUrl" controls class="w-full max-w-md" />
          </div>
          <div
            v-else-if="fileKind === 'binary'"
            class="absolute inset-0 z-30 flex items-center justify-center bg-panel p-8"
          >
            <div class="max-w-sm text-center">
              <p class="text-sm text-mist">无法在工作台里编辑</p>
              <p class="mt-2 text-[12px] leading-5 text-mute">
                压缩包、安装包、旧版 .doc / .ppt、字体等没有对应编辑器，只能下载。
              </p>
              <p class="mt-2 font-mono text-[12px] text-mute">{{ filePath }} · {{ fileSize }} 字节</p>
              <a
                v-if="imageUrl"
                :href="imageUrl"
                :download="filePath.split(/[\\/]/).pop()"
                class="mt-4 inline-block rounded-xl bg-ember px-4 py-2 text-sm font-medium text-ink"
              >
                下载
              </a>
            </div>
          </div>
        </div>
        <ResizeHandle v-if="!isDutyMobile" axis="y" @start="onBottomStart" @drag="onBottomDrag" />
        <div
          v-show="termOpen && session && !isDutyMobile"
          class="shrink-0 overflow-hidden border-t border-line"
          :style="{ height: bottomH + 'px' }"
        >
          <UserTerminal v-if="termOpen && session" :session-id="session.id" @close="termOpen = false" />
        </div>
        <div
          v-show="!termOpen || isDutyMobile"
          class="duty-log shrink-0 overflow-hidden border-t border-line bg-bg"
          :style="{ height: bottomH + 'px' }"
        >
          <div class="flex items-center justify-between px-3 py-1.5">
            <span class="text-[10px] uppercase tracking-[0.16em] text-sage">agent log</span>
            <span class="font-mono text-[10px] text-mute">network=none · /workspace</span>
          </div>
          <div ref="termEl" class="h-[calc(100%-28px)] px-2 pb-2"></div>
        </div>
      </main>

      <ResizeHandle v-if="!isDutyMobile" axis="x" @start="onChatStart" @drag="onChatDrag" />

      <section
        class="flex min-h-0 shrink-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel"
        :style="{ width: chatW + 'px' }"
      >
        <div class="border-b border-line px-4 py-2.5">
          <span class="text-[11px] font-medium uppercase tracking-[0.16em] text-mute">对话</span>
          <p v-if="session?.title" class="mt-1 truncate text-[12px] text-mist">{{ session.title }}</p>
        </div>
        <div ref="chatEl" class="panel-scroll min-h-0 flex-1 space-y-3 overflow-auto p-4">
          <div v-if="!messages.length && !streaming" class="px-1 py-10 text-[13px] leading-6 text-mute">
            打开左侧文件再提问，Forge 会带上当前路径。计划模式只读，构建模式改文件前会让你批准。
          </div>
          <div
            v-for="(m, i) in messages"
            :key="i"
            :class="m.role === 'user' ? 'ml-6' : m.role === 'system' ? '' : 'mr-4'"
          >
            <div
              v-if="m.role === 'system'"
              class="rounded-lg border border-line bg-bg-elev px-3 py-2 text-center text-[12px] text-mute"
            >
              {{ m.text }}
            </div>
            <div v-else-if="m.kind === 'tool'" class="chat-md font-mono text-[11px] leading-5 text-sage/90">
              <span class="text-ember">▸</span> {{ m.text }}
            </div>
            <div
              v-else
              class="relative rounded-2xl px-3.5 py-2.5 text-[13px] leading-6"
              :class="m.role === 'user' ? 'bg-ember/15 text-mist' : 'bg-bg-elev text-mist/95'"
            >
              <div class="mb-1 flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.14em] text-mute">
                <span>{{ m.role === "user" ? "你" : "Forge" }}</span>
                <button
                  v-if="m.role === 'assistant' && m.text"
                  type="button"
                  class="normal-case tracking-normal text-mute hover:text-mist"
                  @click="copyMessage(m.text)"
                >
                  复制
                </button>
              </div>
              <div
                v-if="m.role === 'assistant' || m.kind === 'md'"
                class="chat-md"
                v-html="renderMarkdown(m.text)"
              />
              <pre v-else class="whitespace-pre-wrap font-sans">{{ m.text }}</pre>
              <button
                v-if="showRetry(i, m)"
                class="absolute -bottom-2 -right-1 flex h-7 w-7 items-center justify-center rounded-full border border-line bg-panel text-mute shadow-sm hover:border-ember hover:text-ember"
                type="button"
                title="重试"
                @click="retryLast"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M4.5 12a7.5 7.5 0 0 1 12.7-5.4M19.5 12a7.5 7.5 0 0 1-12.7 5.4"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"
                  />
                  <path d="M17.2 4.8v3.6h-3.6M6.8 19.2v-3.6h3.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </div>
          </div>
          <div v-if="streaming" class="text-[12px] text-ember">正在思考…</div>
        </div>
        <div v-if="pendingApproval" class="sticky bottom-0 border-t border-ember/40 bg-ember/10 p-3 text-sm">
          <div class="font-medium text-ember">批准 {{ pendingApproval.tool }}？</div>
          <pre class="panel-scroll mt-2 max-h-24 overflow-auto font-mono text-[11px] text-mist/80">{{
            pendingApproval.arguments
          }}</pre>
          <pre
            v-if="writeDiff"
            class="panel-scroll mt-2 max-h-40 overflow-auto rounded-lg bg-bg p-2 font-mono text-[11px] text-sage"
          >{{ writeDiff }}</pre>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              class="min-h-11 flex-1 rounded-lg bg-ember px-3 py-1.5 text-ink transition-colors hover:bg-ember-hot"
              type="button"
              @click="decide(true)"
            >
              允许
            </button>
            <button
              class="min-h-11 rounded-lg border border-line px-3 py-1.5 text-mist transition-colors hover:bg-panel-2"
              type="button"
              @click="decide(true, 'session')"
            >
              本会话允许
            </button>
            <button
              class="min-h-11 rounded-lg border border-line px-3 py-1.5 text-mute transition-colors hover:bg-panel-2"
              type="button"
              @click="decide(false)"
            >
              拒绝
            </button>
          </div>
        </div>
        <form class="border-t border-line p-3" @submit.prevent="send">
          <div v-if="dutyMode && canWrite" class="mb-2 flex flex-wrap gap-1.5">
            <button
              type="button"
              class="rounded-lg border border-line px-2.5 py-1 text-[11px] text-mute hover:text-mist disabled:opacity-40"
              :disabled="streaming"
              @click="sendDutyChip('locate')"
            >
              先定位
            </button>
            <button
              type="button"
              class="rounded-lg border border-line px-2.5 py-1 text-[11px] text-mute hover:text-mist disabled:opacity-40"
              :disabled="streaming"
              @click="sendDutyChip('test')"
            >
              跑测试
            </button>
            <button
              type="button"
              class="rounded-lg border border-line px-2.5 py-1 text-[11px] text-mute hover:text-mist disabled:opacity-40"
              :disabled="streaming"
              @click="sendDutyChip('push')"
            >
              提交并推送
            </button>
            <button
              type="button"
              class="rounded-lg border border-line px-2.5 py-1 text-[11px] text-mute hover:text-mist disabled:opacity-40"
              :disabled="streaming"
              @click="sendDutyChip('restart')"
            >
              重启业务
            </button>
          </div>
          <p v-if="filePath" class="mb-2 truncate font-mono text-[11px] text-mute">
            将带上当前文件 {{ filePath }}
          </p>
          <div class="flex gap-2">
            <textarea
              v-model="prompt"
              :disabled="streaming"
              rows="2"
              class="min-h-11 flex-1 resize-none rounded-xl border border-line bg-bg-elev px-3 py-2.5 text-sm outline-none transition-colors duration-200 placeholder:text-mute/60 focus:border-ember"
              placeholder="让 Forge 查看或修改这个仓库… Enter 发送，Shift+Enter 换行"
              @keydown="onPromptKey"
            />
            <button
              v-if="!runActive"
              class="min-h-11 rounded-xl bg-ember px-4 py-2.5 text-sm font-semibold text-ink transition-colors duration-200 hover:bg-ember-hot"
              type="submit"
            >
              发送
            </button>
            <button
              v-else
              class="min-h-11 rounded-xl border border-danger/70 bg-danger/15 px-4 py-2.5 text-sm font-semibold text-danger transition-colors duration-200 hover:bg-danger/25"
              type="button"
              @click="stopRun"
            >
              停止
            </button>
          </div>
          <p v-if="error" class="mt-2 text-xs text-danger">{{ error }}</p>
        </form>
      </section>

      <div
        v-if="!isDutyMobile"
        class="ml-2 hidden w-12 shrink-0 flex-col items-center gap-3 rounded-2xl border border-line bg-panel py-3 lg:flex"
      >
        <button
          type="button"
          class="flex h-9 w-9 items-center justify-center rounded-xl border transition-colors"
          :class="pluginsOpen ? 'border-ember bg-ember/15 text-ember' : 'border-line text-mute hover:text-mist'"
          title="插件"
          @click="pluginsOpen = !pluginsOpen"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M12 3v6m0 6v6M3 12h6m6 0h6"
              stroke="currentColor"
              stroke-width="1.7"
              stroke-linecap="round"
            />
            <rect x="8.2" y="8.2" width="7.6" height="7.6" rx="1.6" stroke="currentColor" stroke-width="1.7" />
          </svg>
        </button>
      </div>

      <div
        v-if="pluginsOpen"
        class="absolute inset-y-3 right-3 z-40 flex overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.5)]"
        :style="{ width: pluginW + 'px' }"
      >
        <ResizeHandle axis="x" @start="onPluginStart" @drag="onPluginDrag" />
        <PluginPanel
          class="min-w-0 flex-1"
          :highlight-id="highlightPlugin"
          @close="pluginsOpen = false"
          @installed="onPluginInstalled"
        />
      </div>
    </div>

    <div
      v-if="changeWs && session"
      class="absolute inset-0 z-40 flex items-center justify-center bg-black/50 p-6"
      @click.self="changeWs = false"
    >
      <div class="w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-xl">
        <p class="text-sm font-medium text-mist">更换工作区</p>
        <p class="mt-1 text-[12px] text-mute">将创建新会话并挂载沙箱。C 盘与只读目录不可选。</p>
        <div class="mt-4">
          <FolderPicker v-model="workspace" @valid="workspaceOk = $event" />
        </div>
        <div class="mt-4 flex gap-2">
          <button
            type="button"
            class="flex-1 rounded-xl bg-ember py-2.5 text-sm font-medium text-ink disabled:opacity-50"
            :disabled="!workspaceOk || opening"
            @click="start"
          >
            {{ opening ? "正在打开…" : "打开此目录" }}
          </button>
          <button
            type="button"
            class="rounded-xl border border-line px-4 text-sm text-mute"
            @click="changeWs = false"
          >
            取消
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="usersOpen && isAdmin"
      class="absolute inset-y-3 right-3 z-50 w-[min(420px,100%)] overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.5)]"
    >
      <UsersPanel @close="usersOpen = false" />
    </div>

    <Teleport to="body">
      <div
        v-if="fsPrompt"
        class="fixed inset-0 z-[190] flex items-center justify-center bg-black/50 p-6"
        @click.self="fsPrompt = null"
      >
        <form
          class="w-full max-w-sm rounded-2xl border border-line bg-panel p-5 shadow-xl"
          @submit.prevent="confirmCreate"
        >
          <p class="text-sm font-medium text-mist">
            {{
              fsPrompt.kind === "rename"
                ? "重命名"
                : fsPrompt.kind === "folder"
                  ? "添加文件夹"
                  : "添加文件"
            }}
          </p>
          <input
            v-model="fsPrompt.name"
            class="mt-3 w-full rounded-xl border border-line bg-bg-elev px-3 py-2.5 font-mono text-sm outline-none focus:border-ember"
            spellcheck="false"
            autofocus
          />
          <p v-if="error" class="mt-2 text-[12px] text-danger">{{ error }}</p>
          <div class="mt-4 flex gap-2">
            <button
              class="flex-1 rounded-xl bg-ember py-2 text-sm font-medium text-ink disabled:opacity-50"
              :disabled="fsBusy || !validName(fsPrompt.name)"
            >
              {{
                fsBusy
                  ? fsPrompt.kind === "rename"
                    ? "保存中…"
                    : "创建中…"
                  : fsPrompt.kind === "rename"
                    ? "保存"
                    : "创建"
              }}
            </button>
            <button
              type="button"
              class="rounded-xl border border-line px-4 text-sm text-mute"
              @click="fsPrompt = null"
            >
              取消
            </button>
          </div>
        </form>
      </div>
      <div
        v-if="deleteTarget"
        class="fixed inset-0 z-[190] flex items-center justify-center bg-black/50 p-6"
        @click.self="deleteTarget = null"
      >
        <div class="w-full max-w-sm rounded-2xl border border-line bg-panel p-5 shadow-xl">
          <p class="text-sm font-medium text-mist">确认删除</p>
          <p class="mt-2 text-[13px] leading-6 text-mute">
            {{ deleteTarget.dir ? "将删除文件夹及其全部内容：" : "将删除文件：" }}
            <span class="font-mono text-mist">{{ deleteTarget.name }}</span>
          </p>
          <div class="mt-4 flex gap-2">
            <button
              type="button"
              class="flex-1 rounded-xl bg-danger py-2 text-sm font-medium text-white disabled:opacity-50"
              :disabled="fsBusy"
              @click="confirmDelete"
            >
              {{ fsBusy ? "删除中…" : "删除" }}
            </button>
            <button
              type="button"
              class="rounded-xl border border-line px-4 text-sm text-mute"
              @click="deleteTarget = null"
            >
              取消
            </button>
          </div>
        </div>
      </div>
      <RollbackModal
        v-if="rollbackOpen && session"
        :session-id="session.id"
        :can-write="canWrite"
        @close="rollbackOpen = false"
        @restored="onRestored"
      />
    </Teleport>
    <FeedbackLayer :busy="!session ? feedbackBusy : ''" :toast="toast" />
  </div>
</template>
