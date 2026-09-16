<!-- 登录或首次创建管理员。成功后 Boot.vue 进入工作台。 -->
<script setup lang="ts">
import { ref } from "vue";
import { login, setupAdmin, type AuthUser } from "../api";

const props = defineProps<{ needsSetup: boolean }>();
const emit = defineEmits<{ done: [user: AuthUser] }>();

const username = ref("");
const password = ref("");
const displayName = ref("");
const remember = ref(true);
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const user = props.needsSetup
      ? await setupAdmin(username.value, password.value, displayName.value)
      : await login(username.value, password.value, remember.value);
    emit("done", user);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-0 flex-1 items-center justify-center p-6">
    <form
      class="w-full max-w-sm rounded-2xl border border-line bg-panel p-6 shadow-[0_24px_80px_rgba(0,0,0,0.45)]"
      @submit.prevent="submit"
    >
      <p class="text-[11px] font-medium uppercase tracking-[0.18em] text-ember">
        {{ needsSetup ? "首次启用" : "登录" }}
      </p>
      <h1 class="mt-2 font-serif text-2xl text-mist">
        {{ needsSetup ? "创建管理员" : "值班台登录" }}
      </h1>
      <p class="mt-2 text-[13px] leading-6 text-mute">
        {{
          needsSetup
            ? "企业用必须先有账号。管理员可以再加工程师和只读账号。"
            : "没有登录不能排查、批准或推送。"
        }}
      </p>
      <label class="mt-5 block text-[12px] text-mute">用户名</label>
      <input
        v-model="username"
        class="mt-1 w-full rounded-xl border border-line bg-bg-elev px-3 py-2.5 text-sm outline-none focus:border-ember"
        autocomplete="username"
        required
      />
      <label v-if="needsSetup" class="mt-3 block text-[12px] text-mute">显示名（可选）</label>
      <input
        v-if="needsSetup"
        v-model="displayName"
        class="mt-1 w-full rounded-xl border border-line bg-bg-elev px-3 py-2.5 text-sm outline-none focus:border-ember"
      />
      <label class="mt-3 block text-[12px] text-mute">密码（至少 8 位）</label>
      <input
        v-model="password"
        type="password"
        class="mt-1 w-full rounded-xl border border-line bg-bg-elev px-3 py-2.5 text-sm outline-none focus:border-ember"
        autocomplete="current-password"
        required
        minlength="8"
      />
      <label v-if="!needsSetup" class="mt-4 flex items-start gap-2 text-[12px] leading-5 text-mute">
        <input v-model="remember" type="checkbox" class="mt-0.5 accent-ember" />
        <span>自动登录 3 天（本浏览器，含重新打开页面）。钉钉内置浏览器要在钉钉里勾一次。</span>
      </label>
      <p v-if="error" class="mt-3 text-sm text-danger">{{ error }}</p>
      <button
        class="mt-5 w-full rounded-xl bg-ember py-2.5 text-sm font-semibold text-ink disabled:opacity-50"
        :disabled="busy"
      >
        {{ busy ? "…" : needsSetup ? "创建并进入" : "登录" }}
      </button>
    </form>
  </div>
</template>
