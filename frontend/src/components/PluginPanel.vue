<!-- 插件库面板：列表/详情/安装到 data/plugins。不是 VS Code 市场，.vsix 跑不了。 -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  getPlugin,
  installPlugin,
  listPlugins,
  uninstallPlugin,
  type PluginInfo,
} from "../api";

const props = defineProps<{ highlightId?: string }>();
const emit = defineEmits<{ installed: [string]; close: [] }>();

const q = ref("");
const plugins = ref<PluginInfo[]>([]);
const installRoot = ref("");
const library = ref("");
const busy = ref("");
const error = ref("");
const detail = ref<PluginInfo | null>(null);
const detailLoading = ref(false);

const recommended = computed(() => plugins.value.filter((p) => p.recommended && !p.builtin));
const builtins = computed(() => plugins.value.filter((p) => p.builtin));
const others = computed(() => plugins.value.filter((p) => !p.recommended && !p.builtin));

async function refresh() {
  const data = await listPlugins(q.value);
  plugins.value = data.plugins;
  installRoot.value = data.install_root;
  library.value = data.library;
}

onMounted(() => {
  refresh()
    .then(async () => {
      if (props.highlightId) await openDetail(props.highlightId);
    })
    .catch((e) => {
      error.value = e instanceof Error ? e.message : String(e);
    });
});

watch(q, () => {
  refresh().catch(() => undefined);
});

watch(
  () => props.highlightId,
  (id) => {
    if (id) void openDetail(id);
  },
);

async function openDetail(id: string) {
  detailLoading.value = true;
  error.value = "";
  try {
    detail.value = await getPlugin(id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    detailLoading.value = false;
  }
}

async function install(id: string) {
  busy.value = id;
  error.value = "";
  try {
    await installPlugin(id);
    await refresh();
    if (detail.value?.id === id) detail.value = await getPlugin(id);
    emit("installed", id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = "";
  }
}

async function uninstall(id: string) {
  busy.value = id;
  error.value = "";
  try {
    await uninstallPlugin(id);
    await refresh();
    if (detail.value?.id === id) detail.value = await getPlugin(id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = "";
  }
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex items-center justify-between border-b border-line px-4 py-3">
      <div>
        <div class="text-sm font-medium text-mist">插件库</div>
        <div class="text-[11px] text-mute">查看器随应用内置；安装只是拷贝说明包，不是 VS Code 插件</div>
      </div>
      <button type="button" class="text-[12px] text-mute hover:text-mist" @click="emit('close')">
        关闭
      </button>
    </div>
    <div class="border-b border-line px-4 py-3">
      <input
        v-model="q"
        class="w-full rounded-xl border border-line bg-bg-elev px-3 py-2 text-sm outline-none focus:border-ember"
        placeholder="搜索插件库…"
      />
    </div>
    <div class="panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <p v-if="error && !detail" class="mb-3 text-[12px] text-danger">{{ error }}</p>
      <section v-if="recommended.length" class="mb-6">
        <h3 class="mb-2 text-[10px] font-medium uppercase tracking-[0.16em] text-mute">推荐</h3>
        <ul class="space-y-2">
          <li v-for="p in recommended" :key="p.id">
            <button
              type="button"
              class="w-full rounded-xl border px-3 py-3 text-left transition-colors hover:border-ember/40"
              :class="highlightId === p.id ? 'border-ember/70 bg-ember/10' : 'border-line bg-bg-elev'"
              @click="openDetail(p.id)"
            >
              <div class="flex items-start justify-between gap-2">
                <div>
                  <div class="text-sm text-mist">{{ p.name }}</div>
                  <p class="mt-0.5 text-[12px] leading-5 text-mute">{{ p.summary }}</p>
                </div>
                <span
                  class="shrink-0 rounded-md px-2 py-0.5 text-[10px]"
                  :class="p.installed ? 'bg-sage/15 text-sage' : 'bg-panel text-mute'"
                >
                  {{ p.installed ? "已安装" : "库中" }}
                </span>
              </div>
            </button>
          </li>
        </ul>
      </section>
      <section v-if="others.length" class="mb-6">
        <h3 class="mb-2 text-[10px] font-medium uppercase tracking-[0.16em] text-mute">其他</h3>
        <ul class="space-y-2">
          <li v-for="p in others" :key="p.id">
            <button
              type="button"
              class="w-full rounded-xl border border-line bg-bg-elev px-3 py-3 text-left hover:border-ember/40"
              @click="openDetail(p.id)"
            >
              <div class="text-sm text-mist">{{ p.name }}</div>
              <p class="mt-0.5 text-[12px] text-mute">{{ p.summary }}</p>
            </button>
          </li>
        </ul>
      </section>
      <section v-if="builtins.length">
        <h3 class="mb-2 text-[10px] font-medium uppercase tracking-[0.16em] text-mute">内置</h3>
        <ul class="space-y-2">
          <li v-for="p in builtins" :key="p.id">
            <button
              type="button"
              class="w-full rounded-xl border border-line bg-bg-elev px-3 py-3 text-left hover:border-ember/40"
              @click="openDetail(p.id)"
            >
              <div class="text-sm text-mist">{{ p.name }}</div>
              <p class="mt-0.5 text-[12px] text-mute">{{ p.summary }}</p>
            </button>
          </li>
        </ul>
      </section>
      <p v-if="!plugins.length" class="text-[12px] text-mute">插件库里没有匹配项</p>
    </div>
    <p class="border-t border-line px-4 py-2 font-mono text-[10px] leading-4 text-mute">
      库 {{ library }} · 安装到 {{ installRoot }}
    </p>

    <Teleport to="body">
      <div
        v-if="detail || detailLoading"
        class="fixed inset-0 z-[180] flex items-center justify-center bg-black/55 p-6"
        @click.self="detail = null"
      >
        <article
          class="flex max-h-[min(86vh,720px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.55)]"
        >
          <header class="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
            <div>
              <h2 class="font-serif text-xl text-mist">{{ detail?.name || "…" }}</h2>
              <p class="mt-1 text-[12px] text-mute">
                {{ detail?.publisher }} · {{ detail?.version }}
              </p>
            </div>
            <button type="button" class="text-[12px] text-mute hover:text-mist" @click="detail = null">
              关闭
            </button>
          </header>
          <div class="panel-scroll min-h-0 flex-1 overflow-auto px-5 py-4">
            <p v-if="error" class="mb-3 text-[12px] text-danger">{{ error }}</p>
            <p class="text-[13px] leading-6 text-mist/90">{{ detail?.summary }}</p>
            <div class="mt-3 flex flex-wrap gap-1">
              <span
                v-for="ext in detail?.exts || []"
                :key="ext"
                class="rounded-md bg-bg-elev px-1.5 py-0.5 font-mono text-[10px] text-mute"
              >
                {{ ext }}
              </span>
            </div>
            <pre class="mt-4 whitespace-pre-wrap font-sans text-[13px] leading-6 text-mist/85">{{
              detail?.readme
            }}</pre>
          </div>
          <footer class="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
            <span class="font-mono text-[10px] text-mute">
              {{ detail?.builtin ? "随应用内置" : detail?.installed ? "已下载到本机" : "尚未下载" }}
            </span>
            <button
              v-if="detail?.builtin"
              type="button"
              class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mute"
              disabled
            >
              内置
            </button>
            <button
              v-else-if="detail?.installed"
              type="button"
              class="rounded-lg border border-line px-3 py-1.5 text-[12px] text-danger"
              :disabled="busy === detail?.id"
              @click="detail && uninstall(detail.id)"
            >
              卸载
            </button>
            <button
              v-else
              type="button"
              class="rounded-lg bg-ember px-4 py-1.5 text-[12px] font-medium text-ink disabled:opacity-50"
              :disabled="!detail || busy === detail.id"
              @click="detail && install(detail.id)"
            >
              {{ busy && detail && busy === detail.id ? "正在下载…" : "安装" }}
            </button>
          </footer>
        </article>
      </div>
    </Teleport>
  </div>
</template>
