<!-- 右上角模型菜单：调 /api/models，切默认写入 preferred_model.txt。没 Key 标未配。 -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  addCustomModel,
  deleteCustomModel,
  listModelCatalog,
  type ModelProvider,
} from "../api";

const props = defineProps<{ model: string }>();
const emit = defineEmits<{ pick: [string] }>();

const btn = ref<HTMLButtonElement | null>(null);
const providers = ref<ModelProvider[]>([]);
const hovered = ref(false);
const pinned = ref(false);
const adding = ref(false);
const form = ref({ provider: "deepseek", label: "", model_id: "" });
const error = ref("");
const pos = ref({ top: 8, right: 8 });
const open = computed(() => hovered.value || pinned.value || adding.value);
let leaveTimer: ReturnType<typeof setTimeout> | null = null;

const current = computed(() => {
  for (const p of providers.value) {
    const v = p.variants.find((x) => x.id === props.model);
    if (v) return { provider: p, variant: v };
  }
  return null;
});

const headerLabel = computed(() => current.value?.provider.label || "DeepSeek");

function place() {
  const r = btn.value?.getBoundingClientRect();
  if (!r) return;
  pos.value = { top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) };
}

function enter() {
  if (leaveTimer) {
    window.clearTimeout(leaveTimer);
    leaveTimer = null;
  }
  hovered.value = true;
  nextTick(place);
}

function leave() {
  leaveTimer = window.setTimeout(() => {
    hovered.value = false;
    if (!adding.value) pinned.value = false;
  }, 140);
}

async function refresh() {
  const data = await listModelCatalog();
  providers.value = data.providers;
}

onMounted(() => {
  refresh().catch((e) => {
    error.value = e instanceof Error ? e.message : String(e);
  });
  window.addEventListener("resize", place);
});

onUnmounted(() => {
  window.removeEventListener("resize", place);
});

watch(open, (value) => {
  if (value) place();
});

async function submitAdd() {
  error.value = "";
  try {
    await addCustomModel(form.value);
    adding.value = false;
    form.value = { provider: "deepseek", label: "", model_id: "" };
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function removeVariant(customId?: string) {
  if (!customId) return;
  await deleteCustomModel(customId);
  await refresh();
}
</script>

<template>
  <div class="relative" @mouseenter="enter" @mouseleave="leave">
    <button
      ref="btn"
      type="button"
      class="flex items-center gap-1.5 rounded-full border border-line bg-panel px-3 py-1.5 text-xs text-mist transition-colors hover:border-ember/50"
      @click="pinned = !open"
    >
      <span class="h-1.5 w-1.5 rounded-full bg-sage shadow-[0_0_8px_#9db892]" />
      <span class="font-medium">{{ headerLabel }}</span>
      <svg
        class="text-mute transition-transform"
        :class="open ? 'rotate-180' : ''"
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
      >
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-show="open"
        class="fixed z-[200] w-64"
        :style="{ top: pos.top + 'px', right: pos.right + 'px' }"
        @mouseenter="enter"
        @mouseleave="leave"
      >
        <div
          class="overflow-hidden rounded-xl border border-line bg-panel shadow-[0_20px_60px_rgba(0,0,0,0.55)]"
        >
          <div class="max-h-80 overflow-auto py-1">
            <div v-for="p in providers" :key="p.id" class="px-1 py-1">
              <div class="flex items-center justify-between px-3 py-1">
                <span class="text-[10px] font-medium uppercase tracking-[0.16em] text-mute">
                  {{ p.label }}
                </span>
                <span v-if="p.configured === false" class="text-[10px] text-danger">未配密钥</span>
              </div>
              <button
                v-for="v in p.variants"
                :key="v.id"
                type="button"
                class="flex w-full items-center justify-between rounded-lg px-3 py-1.5 text-left text-[13px] hover:bg-panel-2"
                :class="model === v.id ? 'text-ember' : 'text-mist/90'"
                @click="emit('pick', v.id); pinned = false; hovered = false"
              >
                <span>{{ v.label }}</span>
                <span class="flex items-center gap-2">
                  <span v-if="model === v.id" class="text-[10px] text-ember">当前</span>
                  <span
                    v-if="v.custom_id"
                    class="text-[10px] text-danger hover:underline"
                    @click.stop="removeVariant(v.custom_id)"
                  >
                    删除
                  </span>
                </span>
              </button>
            </div>
          </div>
          <div class="border-t border-line p-2">
            <button
              v-if="!adding"
              type="button"
              class="w-full rounded-lg px-3 py-1.5 text-left text-[12px] text-ember hover:bg-ember/10"
              @click="adding = true"
            >
              + 添加其他模型
            </button>
            <form v-else class="space-y-2 px-1 py-1" @submit.prevent="submitAdd">
              <input
                v-model="form.provider"
                class="w-full rounded-lg border border-line bg-bg-elev px-2 py-1.5 text-[12px] outline-none focus:border-ember"
                placeholder="提供商 id，如 deepseek"
              />
              <input
                v-model="form.label"
                class="w-full rounded-lg border border-line bg-bg-elev px-2 py-1.5 text-[12px] outline-none focus:border-ember"
                placeholder="显示名，如 Flash"
                required
              />
              <input
                v-model="form.model_id"
                class="w-full rounded-lg border border-line bg-bg-elev px-2 py-1.5 font-mono text-[12px] outline-none focus:border-ember"
                placeholder="deepseek/deepseek-chat"
                required
              />
              <div class="flex gap-2">
                <button class="rounded-lg bg-ember px-3 py-1 text-[12px] text-ink" type="submit">
                  保存
                </button>
                <button
                  class="rounded-lg border border-line px-3 py-1 text-[12px] text-mute"
                  type="button"
                  @click="adding = false"
                >
                  取消
                </button>
              </div>
            </form>
            <p v-if="error" class="mt-1 px-1 text-[11px] text-danger">{{ error }}</p>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
