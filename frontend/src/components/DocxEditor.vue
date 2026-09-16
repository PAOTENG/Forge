<!-- Word 富文本编辑：docxToHtml 打开，保存重建 docx。不是完整 Word。 -->
<script setup lang="ts">
import { onMounted, ref, watch } from "vue";

const props = defineProps<{ html: string }>();
const emit = defineEmits<{ "update:html": [string]; dirty: []; focus: [] }>();

const surface = ref<HTMLElement | null>(null);
let skip = false;

onMounted(() => syncHtml(props.html));

watch(
  () => props.html,
  (value) => {
    if (!surface.value || document.activeElement === surface.value) return;
    if (surface.value.innerHTML === value) return;
    syncHtml(value);
  },
);

function syncHtml(value: string) {
  if (!surface.value) return;
  skip = true;
  surface.value.innerHTML = value || "<p></p>";
  skip = false;
}

function onInput() {
  if (skip || !surface.value) return;
  emit("update:html", surface.value.innerHTML);
  emit("dirty");
}

function cmd(command: string, value?: string) {
  surface.value?.focus();
  const arg = command === "formatBlock" && value ? value.toLowerCase() : value;
  document.execCommand(command, false, arg);
  onInput();
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-panel">
    <div class="flex flex-wrap items-center gap-1 border-b border-line px-3 py-1.5">
      <button type="button" class="docx-cmd" title="粗体" @click="cmd('bold')">B</button>
      <button type="button" class="docx-cmd italic" title="斜体" @click="cmd('italic')">I</button>
      <button type="button" class="docx-cmd underline" title="下划线" @click="cmd('underline')">U</button>
      <span class="mx-1 h-4 w-px bg-line" />
      <button type="button" class="docx-cmd" @click="cmd('formatBlock', 'H1')">H1</button>
      <button type="button" class="docx-cmd" @click="cmd('formatBlock', 'H2')">H2</button>
      <button type="button" class="docx-cmd" @click="cmd('formatBlock', 'P')">正文</button>
      <span class="mx-1 h-4 w-px bg-line" />
      <button type="button" class="docx-cmd" @click="cmd('insertUnorderedList')">• 列表</button>
      <button type="button" class="docx-cmd" @click="cmd('insertOrderedList')">1. 列表</button>
      <span class="ml-auto text-[11px] text-mute">点这里打字 · Ctrl+S 保存</span>
    </div>
    <div
      ref="surface"
      class="docx-surface panel-scroll min-h-0 flex-1 overflow-auto px-8 py-6 outline-none"
      contenteditable="true"
      spellcheck="false"
      @input="onInput"
      @focus="$emit('focus')"
    />
  </div>
</template>
