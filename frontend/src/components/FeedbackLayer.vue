<!-- 全局忙碌/错误条。Agent 跑、工具失败时 App.vue 往这里推文案。 -->
<script setup lang="ts">
defineProps<{
  busy?: string;
  toast?: { text: string; kind: "ok" | "err" } | null;
}>();
</script>

<template>
  <Teleport to="body">
    <Transition name="forge-fade">
      <div
        v-if="busy"
        class="fixed inset-0 z-[220] flex items-center justify-center bg-bg/72 backdrop-blur-[2px]"
      >
        <div
          class="flex min-w-[240px] items-center gap-3 rounded-2xl border border-line bg-panel px-5 py-4 shadow-[0_24px_80px_rgba(0,0,0,0.45)]"
        >
          <span class="forge-spin" aria-hidden="true" />
          <p class="text-sm text-mist">{{ busy }}</p>
        </div>
      </div>
    </Transition>
    <div class="pointer-events-none fixed inset-x-0 bottom-8 z-[230] flex justify-center px-4">
      <Transition name="forge-toast">
        <div
          v-if="toast"
          class="rounded-full border px-4 py-2 text-[13px] shadow-[0_12px_40px_rgba(0,0,0,0.4)]"
          :class="
            toast.kind === 'err'
              ? 'border-danger/40 bg-panel text-danger'
              : 'border-sage/40 bg-panel text-sage'
          "
        >
          {{ toast.text }}
        </div>
      </Transition>
    </div>
  </Teleport>
</template>

<style scoped>
.forge-spin {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border: 2px solid var(--color-line);
  border-top-color: var(--color-ember);
  border-radius: 50%;
  animation: forge-spin 0.65s linear infinite;
}

@keyframes forge-spin {
  to {
    transform: rotate(360deg);
  }
}

.forge-fade-enter-active,
.forge-fade-leave-active {
  transition: opacity 0.16s ease;
}

.forge-fade-enter-from,
.forge-fade-leave-to {
  opacity: 0;
}

.forge-toast-enter-active,
.forge-toast-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}

.forge-toast-enter-from,
.forge-toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
