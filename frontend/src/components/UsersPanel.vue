<!-- 管理员加工程师/只读、改角色。viewer 不能发消息或批准。 -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { createUser, listUsers, setUserRole, type AuthUser } from "../api";

const emit = defineEmits<{ close: [] }>();

const users = ref<AuthUser[]>([]);
const error = ref("");
const busy = ref(false);
const username = ref("");
const password = ref("");
const role = ref("engineer");

async function refresh() {
  users.value = await listUsers();
}

onMounted(() => {
  void refresh().catch((e) => {
    error.value = e instanceof Error ? e.message : String(e);
  });
});

async function add() {
  error.value = "";
  busy.value = true;
  try {
    await createUser({ username: username.value, password: password.value, role: role.value });
    username.value = "";
    password.value = "";
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function changeRole(u: AuthUser, next: string) {
  error.value = "";
  try {
    await setUserRole(u.id, next);
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="flex items-center justify-between border-b border-line px-4 py-3">
      <p class="text-sm text-mist">账号</p>
      <button type="button" class="text-[12px] text-mute" @click="emit('close')">关闭</button>
    </div>
    <div class="panel-scroll min-h-0 flex-1 space-y-3 overflow-auto p-4">
      <p class="text-[12px] text-mute">admin 管人；engineer 排查/批准/git；viewer 只读。</p>
      <ul class="space-y-2">
        <li
          v-for="u in users"
          :key="u.id"
          class="flex items-center justify-between rounded-xl border border-line bg-bg-elev px-3 py-2"
        >
          <span class="text-sm text-mist">{{ u.display_name }} <span class="text-mute">@{{ u.username }}</span></span>
          <select
            class="rounded-md border border-line bg-panel px-2 py-1 text-[12px] text-mist"
            :value="u.role"
            @change="changeRole(u, ($event.target as HTMLSelectElement).value)"
          >
            <option value="admin">admin</option>
            <option value="engineer">engineer</option>
            <option value="viewer">viewer</option>
          </select>
        </li>
      </ul>
      <form class="space-y-2 border-t border-line pt-3" @submit.prevent="add">
        <p class="text-[12px] text-mute">添加账号</p>
        <input
          v-model="username"
          class="w-full rounded-lg border border-line bg-bg-elev px-3 py-2 text-sm"
          placeholder="用户名"
          required
        />
        <input
          v-model="password"
          type="password"
          class="w-full rounded-lg border border-line bg-bg-elev px-3 py-2 text-sm"
          placeholder="密码 ≥ 8 位"
          required
          minlength="8"
        />
        <select v-model="role" class="w-full rounded-lg border border-line bg-bg-elev px-3 py-2 text-sm">
          <option value="engineer">engineer</option>
          <option value="viewer">viewer</option>
          <option value="admin">admin</option>
        </select>
        <button class="w-full rounded-lg bg-ember py-2 text-sm font-medium text-ink" :disabled="busy">
          {{ busy ? "…" : "添加" }}
        </button>
      </form>
      <p v-if="error" class="text-sm text-danger">{{ error }}</p>
    </div>
  </div>
</template>
