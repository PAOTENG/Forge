<!-- 回退弹窗：list_backups，按文件或整会话 restore。对应 backup.py 写前快照。 -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { listBackups, restoreAllBackups, restoreBackup, type FileBackup } from "../api";

const props = defineProps<{
  sessionId: string;
  canWrite?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  restored: [paths: string[]];
}>();

const items = ref<FileBackup[]>([]);
const loading = ref(true);
const error = ref("");
const busyId = ref("");
const confirmAll = ref(false);

function when(iso: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function actionLabel(row: FileBackup) {
  if (!row.existed) return "新建";
  if (row.action === "delete") return "删除";
  return "改写";
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const data = await listBackups(props.sessionId);
    items.value = data.items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function restoreOne(row: FileBackup) {
  if (!props.canWrite || busyId.value) return;
  busyId.value = row.id;
  error.value = "";
  try {
    await restoreBackup(props.sessionId, row.id);
    emit("restored", [row.rel_path]);
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busyId.value = "";
  }
}

async function restoreAll() {
  if (!props.canWrite || busyId.value) return;
  busyId.value = "*";
  error.value = "";
  try {
    await restoreAllBackups(props.sessionId);
    emit("restored", items.value.map((row) => row.rel_path));
    confirmAll.value = false;
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busyId.value = "";
  }
}

onMounted(load);
</script>

<template>
  <div
    class="fixed inset-0 z-[200] flex items-end justify-center bg-black/55 p-3 sm:items-center sm:p-6"
    @click.self="emit('close')"
  >
    <div
      class="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.5)]"
    >
      <div class="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <p class="text-sm font-medium text-mist">回退本次修改</p>
          <p class="mt-1 text-[12px] leading-5 text-mute">
            每个文件恢复到 agent 改之前的内容。已推送到 Gitee 的备份保留 7 天。
          </p>
        </div>
        <button
          type="button"
          class="rounded-full border border-line px-3 py-1 text-[12px] text-mute hover:text-mist"
          @click="emit('close')"
        >
          关闭
        </button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto px-5 py-3">
        <p v-if="loading" class="py-8 text-center text-[13px] text-mute">正在读取备份…</p>
        <p v-else-if="error" class="py-3 text-[13px] text-danger">{{ error }}</p>
        <p v-else-if="!items.length" class="py-8 text-center text-[13px] text-mute">
          这个会话还没有改过文件，没有可回退的备份。
        </p>
        <ul v-else class="divide-y divide-line">
          <li v-for="row in items" :key="row.id" class="flex items-center gap-3 py-3">
            <div class="min-w-0 flex-1">
              <p class="truncate font-mono text-[13px] text-mist">{{ row.rel_path }}</p>
              <p class="mt-1 text-[11px] leading-5 text-mute">
                {{ actionLabel(row) }} · {{ when(row.created_at) }} · {{ row.actor || "local" }} ·
                {{ row.model || "—" }}
                <span v-if="row.versions > 1"> · {{ row.versions }} 次改写</span>
                <span v-if="row.pushed_at"> · 已推送</span>
                <span v-if="row.restored_at"> · 已回退过</span>
              </p>
            </div>
            <button
              type="button"
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-sage/40 bg-sage/15 text-sage hover:bg-sage/25 disabled:opacity-40"
              :disabled="!canWrite || !!busyId"
              :title="canWrite ? '回退此文件' : '只读账号不能回退'"
              @click="restoreOne(row)"
            >
              <span class="text-[16px] leading-none">↵</span>
            </button>
          </li>
        </ul>
      </div>

      <div class="border-t border-line px-5 py-4">
        <div v-if="confirmAll" class="mb-3 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] text-mist">
          将把列表里的全部文件恢复到改前状态。新建的文件会被删掉。
        </div>
        <div class="flex gap-2">
          <button
            v-if="!confirmAll"
            type="button"
            class="flex-1 rounded-xl bg-sage py-2.5 text-sm font-medium text-ink disabled:opacity-40"
            :disabled="!canWrite || !items.length || !!busyId"
            @click="confirmAll = true"
          >
            {{ busyId === "*" ? "全部回退中…" : "全部回退" }}
          </button>
          <template v-else>
            <button
              type="button"
              class="flex-1 rounded-xl bg-danger py-2.5 text-sm font-medium text-white disabled:opacity-40"
              :disabled="!!busyId"
              @click="restoreAll"
            >
              {{ busyId === "*" ? "全部回退中…" : "确认全部回退" }}
            </button>
            <button
              type="button"
              class="rounded-xl border border-line px-4 text-sm text-mute"
              :disabled="!!busyId"
              @click="confirmAll = false"
            >
              取消
            </button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
