<!--
  启动门：打 /api/health 与 authStatus。
  没账号 → 首次建管理员；未登录 → AuthGate；已登录 → 异步加载 App.vue。
-->
<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref } from "vue";
import AuthGate from "./components/AuthGate.vue";
import { authStatus, me, type AuthUser } from "./api";

const Workbench = defineAsyncComponent(() => import("./App.vue"));

const phase = ref<"boot" | "gate" | "app">("boot");
const needsSetup = ref(false);
const error = ref("");

onMounted(async () => {
  try {
    const auth = await authStatus();
    needsSetup.value = auth.needs_setup;
    if (!auth.needs_setup) {
      try {
        await me();
        phase.value = "app";
        return;
      } catch {
        /* cookie or token missing */
      }
    }
    phase.value = "gate";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    phase.value = "gate";
  }
});

function onAuthed(_user: AuthUser) {
  phase.value = "app";
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <p v-if="phase === 'boot'" class="p-8 text-center text-sm text-mute">正在打开值班台…</p>
    <AuthGate v-else-if="phase === 'gate'" :needs-setup="needsSetup" @done="onAuthed" />
    <Workbench v-else />
    <p v-if="error && phase === 'gate'" class="px-6 pb-6 text-center text-sm text-danger">{{ error }}</p>
  </div>
</template>
