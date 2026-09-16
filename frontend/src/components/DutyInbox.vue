<!-- 值班收件箱：列出告警，签收/指派/关闭，点排查调 duty.investigate 开会话。 -->
<script setup lang="ts">
import { ref } from "vue";
import type { DutyAlert, DutyStatus } from "../api";

defineProps<{
  alerts: DutyAlert[];
  status: DutyStatus | null;
  busy?: boolean;
  highlightId?: string;
  canWrite?: boolean;
}>();

const emit = defineEmits<{
  investigate: [id: string];
  resolve: [id: string];
  ack: [id: string];
  assign: [id: string];
  refresh: [];
  demo: [];
  testNotify: [];
}>();

const expandedId = ref("");

function toggle(id: string) {
  expandedId.value = expandedId.value === id ? "" : id;
}

function severityClass(sev: string) {
  if (sev === "critical" || sev === "error") return "text-danger";
  if (sev === "warning") return "text-ember";
  return "text-mute";
}

function when(iso: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}
</script>

<template>
  <section class="rounded-2xl border border-line bg-panel/90 p-5 shadow-[0_24px_80px_rgba(0,0,0,0.35)]">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="text-[11px] font-medium uppercase tracking-[0.18em] text-ember">值班收件箱</p>
        <p class="mt-1 text-sm text-mist">点标题看错误详情，点「排查」才开会话让模型读仓库。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mute hover:text-mist"
          :disabled="busy"
          @click="emit('refresh')"
        >
          刷新
        </button>
        <button
          v-if="canWrite"
          type="button"
          class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mute hover:text-mist"
          :disabled="busy"
          @click="emit('demo')"
        >
          模拟告警
        </button>
        <button
          v-if="canWrite"
          type="button"
          class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mute hover:text-mist"
          :disabled="busy"
          @click="emit('testNotify')"
        >
          测推送
        </button>
      </div>
    </div>

    <p v-if="status" class="mt-3 text-[11px] leading-5 text-mute">
      通道
      <span :class="status.wecom ? 'text-sage' : ''">企微{{ status.wecom ? "已配" : "未配" }}</span>
      ·
      <span :class="status.dingtalk ? 'text-sage' : ''">钉钉{{ status.dingtalk ? "已配" : "未配" }}</span>
      ·
      <span :class="status.webhook ? 'text-sage' : ''">Webhook{{ status.webhook ? "已配" : "未配" }}</span>
      <span class="mt-1 block truncate font-mono">工作区 {{ status.workspace }}</span>
      <span
        class="mt-1 block truncate font-mono"
        :class="status.public_url.includes('127.0.0.1') ? 'text-danger' : ''"
      >
        卡片地址 {{ status.public_url }}
        <template v-if="status.public_url.includes('127.0.0.1')">（手机打不开，应改成局域网 IP）</template>
      </span>
    </p>

    <ul v-if="alerts.length" class="mt-4 space-y-2">
      <li
        v-for="a in alerts"
        :key="a.id"
        class="rounded-xl border bg-bg-elev px-3 py-3"
        :class="highlightId === a.id ? 'border-ember' : 'border-line'"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <button type="button" class="block w-full truncate text-left text-sm text-mist" @click="toggle(a.id)">
              {{ a.title }}
              <span v-if="a.status === 'resolved'" class="ml-2 text-[11px] text-mute">已关闭</span>
              <span class="ml-2 text-[11px] text-mute">{{ expandedId === a.id ? "收起" : "详情" }}</span>
            </button>
            <p class="mt-1 text-[11px] text-mute">
              <span :class="severityClass(a.severity)">{{ a.severity }}</span>
              <span v-if="a.service"> · {{ a.service }}</span>
              · {{ a.source }} · {{ when(a.created_at) }}
              <span v-if="a.assigned_to"> · 指派 {{ a.assigned_to }}</span>
              <span v-if="a.acked_by"> · {{ a.acked_by }} 已签收</span>
            </p>
            <p v-if="a.summary" class="mt-1 line-clamp-2 text-[12px] leading-5 text-mist/80">
              {{ a.summary }}
            </p>
            <div v-if="expandedId === a.id" class="mt-3 space-y-2">
              <p class="text-[11px] uppercase tracking-[0.14em] text-mute">完整摘要 / 报错</p>
              <pre class="panel-scroll max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-bg p-2 font-mono text-[11px] text-sage">{{
                a.summary || "（无摘要）"
              }}</pre>
              <p class="text-[11px] uppercase tracking-[0.14em] text-mute">堆栈 / 日志</p>
              <pre class="panel-scroll max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-bg p-2 font-mono text-[11px] text-mist/85">{{
                a.stack || "（这条告警没有堆栈，可到上方「被监控业务」看进程日志）"
              }}</pre>
              <p v-if="a.link" class="font-mono text-[11px] text-mute">{{ a.link }}</p>
            </div>
          </div>
          <div v-if="canWrite" class="flex shrink-0 flex-col gap-2">
            <button
              type="button"
              class="rounded-lg bg-ember px-3 py-2 text-[12px] font-semibold text-ink disabled:opacity-50"
              :disabled="busy"
              @click="emit('investigate', a.id)"
            >
              {{ a.session_id ? "继续" : "排查" }}
            </button>
            <button
              v-if="!a.acked_by"
              type="button"
              class="rounded-lg border border-line px-3 py-2 text-[12px] text-mute hover:text-mist disabled:opacity-50"
              :disabled="busy"
              @click="emit('ack', a.id)"
            >
              签收
            </button>
            <button
              type="button"
              class="rounded-lg border border-line px-3 py-2 text-[12px] text-mute hover:text-mist disabled:opacity-50"
              :disabled="busy"
              @click="emit('assign', a.id)"
            >
              指派给我
            </button>
            <button
              v-if="a.status !== 'resolved'"
              type="button"
              class="rounded-lg border border-line px-3 py-2 text-[12px] text-mute hover:text-mist disabled:opacity-50"
              :disabled="busy"
              @click="emit('resolve', a.id)"
            >
              关闭
            </button>
          </div>
        </div>
      </li>
    </ul>
    <p v-else class="mt-4 text-sm text-mute">还没有告警。点「模拟告警」，或把监控 POST 到 /api/duty/alerts。</p>
  </section>
</template>
