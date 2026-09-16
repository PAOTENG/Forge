<!-- 栏宽拖动手柄。对话 | 文件 | 日志 三栏可调。 -->
<script setup lang="ts">
const props = defineProps<{ axis: "x" | "y" }>();
const emit = defineEmits<{ start: []; drag: [number]; end: [] }>();

function onPointerDown(e: PointerEvent) {
  e.preventDefault();
  const origin = props.axis === "x" ? e.clientX : e.clientY;
  emit("start");
  const onMove = (ev: PointerEvent) => {
    emit("drag", (props.axis === "x" ? ev.clientX : ev.clientY) - origin);
  };
  const onUp = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    document.body.classList.remove("is-resizing-x", "is-resizing-y");
    emit("end");
  };
  document.body.classList.add(props.axis === "x" ? "is-resizing-x" : "is-resizing-y");
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}
</script>

<template>
  <div
    class="group relative z-[5] shrink-0"
    :class="axis === 'x' ? 'w-3 cursor-col-resize' : 'h-3 cursor-row-resize'"
    @pointerdown="onPointerDown"
  >
    <div
      class="absolute bg-transparent transition-colors group-hover:bg-ember/55"
      :class="
        axis === 'x'
          ? 'inset-y-3 left-1/2 w-[3px] -translate-x-1/2 rounded-full'
          : 'inset-x-3 top-1/2 h-[3px] -translate-y-1/2 rounded-full'
      "
    />
  </div>
</template>
