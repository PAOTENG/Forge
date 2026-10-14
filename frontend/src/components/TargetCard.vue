<!-- 被监控业务卡片：显示 pid/金丝雀/题号，按钮走 /api/duty/target/* 启停切题。 -->
<script setup lang="ts">
import { computed, ref } from "vue";
import type { DutyAlert, TargetStatus } from "../api";

const props = defineProps<{
  target: TargetStatus | null;
  latest?: DutyAlert | null;
  busy?: boolean;
  canWrite?: boolean;
}>();

const emit = defineEmits<{
  start: [];
  restart: [];
  nextBug: [];
  refresh: [];
}>();

const open = ref(false);

const active = computed(() =>
  (props.target?.catalog || []).find((b) => b.id === props.target?.active_bug),
);

const watch = computed(() => {
  const alert = props.latest;
  const text = `${alert?.service || ""} ${alert?.title || ""} ${alert?.summary || ""}`;
  if (/kb-qa|知识库|ledger/i.test(text)) {
    return { kind: "ledger" as const, name: "Ledger", label: "正在监控知识库问答 Agent" };
  }
  if (/roomcheck|客房/i.test(text)) {
    return { kind: "room" as const, name: "RoomCheck", label: "正在监控客房验收 Agent" };
  }
  if (alert) {
    return { kind: "order" as const, name: alert.service || props.target?.name || "order-api", label: "正在监控" };
  }
  return null;
});
</script>

<template>
  <section class="rounded-2xl border border-line bg-panel/90 p-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <button type="button" class="min-w-0 flex-1 text-left" @click="(!watch || watch.kind === 'order') && (open = !open)">
        <p class="text-[11px] font-medium uppercase tracking-[0.18em] text-ember">被监控业务</p>
        <template v-if="watch && watch.kind !== 'order'">
          <p class="mt-1 text-sm text-mist">
            {{ watch.label }}
            ·
            <span class="text-ember">{{ watch.name }}</span>
          </p>
          <p class="mt-1 text-[13px] text-mist">{{ latest?.title }}</p>
          <p class="mt-1 line-clamp-2 text-[12px] leading-5 text-mute">{{ latest?.summary }}</p>
        </template>
        <template v-else>
          <p class="mt-1 text-sm text-mist">
            {{ target?.name || "order-api" }}
            ·
            <span :class="target?.running ? 'text-sage' : 'text-danger'">
              {{ target?.running ? "运行中" : "未启动" }}
            </span>
            · 当前题
            <span class="text-ember">BUG-{{ target?.active_bug ?? "?" }}</span>
            <span v-if="active"> · {{ active.title }}</span>
          </p>
          <p class="mt-1 font-mono text-[11px] text-mute">{{ target?.cwd }}</p>
          <p class="mt-2 text-[12px] leading-5 text-mute">
            健康 {{ target?.health?.status_code ?? "-" }} · 金丝雀
            <span :class="target?.fixed ? 'text-sage' : 'text-danger'">
              {{ target?.fixed ? "已修改" : "未完成" }}
            </span>
            · {{ open ? "收起详情" : "点这里看错误 / 日志 / 十题目录" }}
          </p>
        </template>
      </button>
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
          v-if="canWrite && (!watch || watch.kind === 'order')"
          type="button"
          class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mute hover:text-mist"
          :disabled="busy"
          @click="emit('start')"
        >
          启动
        </button>
        <button
          v-if="canWrite && (!watch || watch.kind === 'order')"
          type="button"
          class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mute hover:text-mist"
          :disabled="busy"
          @click="emit('restart')"
        >
          重启业务
        </button>
        <button
          v-if="canWrite && (!watch || watch.kind === 'order')"
          type="button"
          class="rounded-lg bg-ember px-3 py-1.5 text-[12px] font-semibold text-ink disabled:opacity-50"
          :disabled="busy"
          @click="emit('nextBug')"
        >
          下一题
        </button>
      </div>
    </div>

    <div v-if="open && (!watch || watch.kind === 'order')" class="mt-4 space-y-3 border-t border-line pt-4">
      <p class="text-[12px] leading-5 text-mute">
        这是演示下单服务，端口 8790。看门狗打
        <span class="font-mono">/canary</span>
        ，失败才进收件箱。点收件箱里的告警看值班处理；这里只看业务本身。
      </p>
      <div>
        <p class="text-[11px] uppercase tracking-[0.16em] text-mute">金丝雀错误</p>
        <pre
          class="panel-scroll mt-1 max-h-40 overflow-auto rounded-lg bg-bg p-3 font-mono text-[11px] leading-5 text-sage"
        >{{ target?.canary?.body || "（金丝雀正常或还没有响应）" }}</pre>
      </div>
      <div>
        <p class="text-[11px] uppercase tracking-[0.16em] text-mute">进程日志</p>
        <pre
          class="panel-scroll mt-1 max-h-48 overflow-auto rounded-lg bg-bg p-3 font-mono text-[11px] leading-5 text-mist/80"
        >{{ target?.log_tail || "（还没有日志）" }}</pre>
      </div>
      <div>
        <p class="text-[11px] uppercase tracking-[0.16em] text-mute">十题目录（出题说明，不会发给模型）</p>
        <ul class="mt-2 space-y-1.5">
          <li
            v-for="b in target?.catalog || []"
            :key="b.id"
            class="rounded-lg border px-3 py-2 text-[12px]"
            :class="b.id === target?.active_bug ? 'border-ember bg-ember/10 text-mist' : 'border-line text-mute'"
          >
            <span class="font-mono text-ember">BUG-{{ b.id }}</span>
            {{ b.title }}
            <span v-if="b.id === target?.active_bug && target?.fixed" class="ml-2 text-sage">已修改</span>
            <span v-else-if="b.id === target?.active_bug" class="ml-2 text-danger">当前激活</span>
            <span class="mt-0.5 block text-[11px] text-mute">{{ b.hint }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>
