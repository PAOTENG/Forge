<!-- 选工作区：/api/system/drives 与 browse。C 盘和不可写目录不能选。 -->
<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { browseDir, listDrives, type BrowseResult, type DriveInfo } from "../api";

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ "update:modelValue": [string]; valid: [boolean] }>();

const drives = ref<DriveInfo[]>([]);
const browse = ref<BrowseResult | null>(null);
const jump = ref(props.modelValue);
const loading = ref(false);
const error = ref("");

async function loadDrives() {
  drives.value = await listDrives();
}

async function openPath(raw: string) {
  error.value = "";
  loading.value = true;
  try {
    const data = await browseDir(raw);
    browse.value = data;
    jump.value = data.path;
    emit("update:modelValue", data.path);
    emit("valid", data.writable && !data.blocked);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    emit("valid", false);
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadDrives();
    const start = props.modelValue;
    const cDrive = /^[cC]:/.test(start.trim());
    if (start && !cDrive) {
      await openPath(start);
    } else if (drives.value[0]) {
      await openPath(drives.value[0].path);
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});

watch(
  () => props.modelValue,
  (next) => {
    if (next && next !== browse.value?.path) jump.value = next;
  },
);
</script>

<template>
  <div class="folder-picker">
    <div class="flex flex-wrap gap-1.5">
      <button
        v-for="d in drives"
        :key="d.path"
        type="button"
        class="rounded-lg border px-2.5 py-1 font-mono text-[11px] transition-colors"
        :class="
          browse?.path.toUpperCase().startsWith(d.path.replace(/[\\/]$/, '').toUpperCase())
            ? 'border-ember/60 bg-ember/15 text-ember'
            : 'border-line text-mute hover:text-mist'
        "
        @click="openPath(d.path)"
      >
        {{ d.name }}
      </button>
      <span v-if="!drives.length" class="text-[11px] text-mute">没有可用磁盘（C 盘已排除）</span>
    </div>

    <form class="mt-3 flex gap-2" @submit.prevent="openPath(jump)">
      <input
        v-model="jump"
        class="min-w-0 flex-1 rounded-lg border border-line bg-bg-elev px-2.5 py-2 font-mono text-[12px] text-mist outline-none focus:border-ember"
        spellcheck="false"
      />
      <button
        class="shrink-0 rounded-lg border border-line px-3 text-[12px] text-mist hover:border-ember/50"
        type="submit"
      >
        转到
      </button>
    </form>

    <div class="mt-3 flex items-center justify-between gap-2">
      <button
        type="button"
        class="text-[12px] text-ember disabled:text-mute"
        :disabled="!browse?.parent"
        @click="browse?.parent && openPath(browse.parent)"
      >
        ↑ 上级
      </button>
      <span
        class="rounded-full px-2 py-0.5 text-[10px]"
        :class="
          browse && browse.writable && !browse.blocked
            ? 'bg-sage/15 text-sage'
            : 'bg-danger/15 text-danger'
        "
      >
        {{
          !browse
            ? "选择目录"
            : browse.blocked || !browse.writable
              ? "不可选 · 需管理员或受保护"
              : "可写入"
        }}
      </span>
    </div>

    <div class="panel-scroll mt-2 max-h-52 overflow-auto rounded-xl border border-line bg-bg-elev">
      <p v-if="loading" class="px-3 py-4 text-[12px] text-mute">正在读取…</p>
      <p v-else-if="browse && !browse.entries.length" class="px-3 py-4 text-[12px] text-mute">
        没有子文件夹
      </p>
      <button
        v-for="entry in browse?.entries || []"
        :key="entry.path"
        type="button"
        class="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-mist/90 hover:bg-panel-2"
        @click="openPath(entry.path)"
        @dblclick="openPath(entry.path)"
      >
        <svg class="shrink-0 text-ember/80" width="14" height="14" viewBox="0 0 24 24" fill="none">
          <path
            d="M3 7.5A1.5 1.5 0 0 1 4.5 6h5l1.2 1.6H19.5A1.5 1.5 0 0 1 21 9.1v8.4a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5v-10Z"
            stroke="currentColor"
            stroke-width="1.6"
          />
        </svg>
        <span class="truncate">{{ entry.name }}</span>
      </button>
    </div>
    <p v-if="error" class="mt-2 text-[12px] text-danger">{{ error }}</p>
    <p class="mt-2 text-[11px] leading-5 text-mute">
      C 盘不可选。系统会探测写入权限，需要管理员的目录也不能作为工作区。
    </p>
  </div>
</template>
