<!-- 标题栏终端：xterm + WebSocket /sessions/{id}/pty → 沙箱 bash。和 Agent 的 bash 工具日志分开。 -->
<script setup lang="ts">
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps<{ sessionId: string }>();
const emit = defineEmits<{ close: [] }>();

const termEl = ref<HTMLDivElement | null>(null);
let term: Terminal | null = null;
let fit: FitAddon | null = null;
let ws: WebSocket | null = null;
let ro: ResizeObserver | null = null;

function connect() {
  if (!termEl.value) return;
  term = new Terminal({
    cursorBlink: true,
    convertEol: false,
    fontFamily: "IBM Plex Mono, ui-monospace, monospace",
    fontSize: 13,
    theme: {
      background: "#0c0d0b",
      foreground: "#d7dacd",
      cursor: "#e39b4a",
      selectionBackground: "#e39b4a44",
    },
  });
  fit = new FitAddon();
  term.loadAddon(fit);
  term.open(termEl.value);
  fit.fit();
  ro = new ResizeObserver(() => fit?.fit());
  ro.observe(termEl.value);

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/api/sessions/${props.sessionId}/pty`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    if (!term) return;
    if (typeof ev.data === "string") term.write(ev.data);
    else term.write(new Uint8Array(ev.data as ArrayBuffer));
  };
  ws.onerror = () => term?.writeln("\r\n[pty 连接失败]");
  ws.onclose = () => term?.writeln("\r\n[已断开]");
  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
  });
}

onMounted(async () => {
  await nextTick();
  connect();
});

onBeforeUnmount(() => {
  ro?.disconnect();
  try {
    ws?.close();
  } catch {
    /* ignore */
  }
  term?.dispose();
});
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-bg">
    <div class="flex items-center justify-between border-b border-line px-3 py-1.5">
      <span class="text-[10px] uppercase tracking-[0.16em] text-sage">终端 · sandbox bash</span>
      <button type="button" class="text-[11px] text-mute hover:text-mist" @click="emit('close')">
        关闭
      </button>
    </div>
    <div ref="termEl" class="min-h-0 flex-1 px-2 py-1"></div>
  </div>
</template>
