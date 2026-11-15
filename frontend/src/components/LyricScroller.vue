<script setup lang="ts">
/**
 * 同步滚动歌词（Apple Music / 网易云风格）。
 *
 * 只负责「渲染 + 滚动 + 点击定位」三件事，时间轴由 common/lyric.ts 算好传进来，
 * 当前行下标由父组件根据 audio.currentTime 算好传进来（见 StudioPage 的
 * activeLyricIndex），组件不碰 <audio>。
 *
 * 滚动实现：容器固定高度 + overflow:hidden，内部列表用 translateY 把
 * 「当前行」垂直居中。用 transform 而不是 scrollTop，才能配合 CSS 过渡做出
 * 平滑上滑；行高不固定（长句会换行），所以按 DOM 实测 offsetTop / offsetHeight
 * 计算，不用固定行高相乘。窗口尺寸变化时用 ResizeObserver 重算一次。
 *
 * 高亮层级：当前行深色加粗 + 品牌红，相邻行 75% 不透明度，更远的行淡出。
 *
 * 两种点击语义（由 alignMode 决定）：
 * - 默认：点一句就跳到那句（seek）；
 * - alignMode：点一句表示「此刻正在唱这句」，emit align(index) 让父组件
 *   一次性算出偏移。此时行上会出现虚线框提示「可点」。
 */
import { nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { MusicIcon } from "tdesign-icons-vue-next";

import type { LyricItem } from "@/common/lyric";

const props = defineProps<{
  items: LyricItem[];
  /** 当前正在唱的行在 items 里的下标；-1 表示还没开始 / 已结束 */
  activeIndex: number;
  /** true = 时间来自真实时间戳（LRC），false = 按字重估算 */
  exact?: boolean;
  /** 未播放时的提示文案 */
  idleHint?: string;
  /** 对齐模式：点歌词改为「把这句对齐到当前播放位置」 */
  alignMode?: boolean;
}>();

const emit = defineEmits<{
  /** 点击某一行：跳到该行开始时间（秒） */
  seek: [time: number];
  /** 对齐模式下点击某一行：把该行对齐到当前播放位置 */
  align: [index: number];
}>();

/** 点击一行：对齐模式发 align，否则跳转播放位置 */
function onLineClick(item: LyricItem, index: number) {
  if (item.kind !== "text") return; // 段落标签不参与
  if (props.alignMode) emit("align", index);
  else emit("seek", item.start);
}

const containerRef = ref<HTMLElement | null>(null);
const innerRef = ref<HTMLElement | null>(null);
/** 列表向上偏移量（px），正值表示内容整体上移 */
const offset = ref(0);

/** 把第 index 行滚到视口垂直中央 */
function syncScroll() {
  const container = containerRef.value;
  const inner = innerRef.value;
  if (!container || !inner) return;
  const index = props.activeIndex;
  if (index < 0 || index >= inner.children.length) {
    // 未播放：回到顶部，不要残留上一首的滚动位置
    offset.value = 0;
    return;
  }
  const target = inner.children[index] as HTMLElement;
  const center = target.offsetTop + target.offsetHeight / 2;
  offset.value = Math.max(0, center - container.clientHeight / 2);
}

/** 行透明度 / 字重随与当前行的距离变化 */
function lineClass(index: number): string {
  const active = props.activeIndex;
  if (active < 0) return "is-idle";
  const distance = Math.abs(index - active);
  if (distance === 0) return "is-active";
  if (distance === 1) return "is-near";
  if (distance === 2) return "is-far";
  return "is-away";
}

let settleTimer: number | undefined;
watch(
  () => [props.activeIndex, props.items],
  () => {
    // 等 DOM 更新完再测量，否则新歌词的行还没渲染出来
    void nextTick(syncScroll);
    // 兜底再量一次：字体加载、长句换行、样式过渡都会让行高晚一两帧才稳定，
    // 只量一次会让当前行停在容器的偏上/偏下位置（看起来像没滚到中间）。
    window.clearTimeout(settleTimer);
    settleTimer = window.setTimeout(syncScroll, 240);
  },
  { deep: false },
);

let observer: ResizeObserver | null = null;
onMounted(() => {
  void nextTick(syncScroll);
  if (typeof ResizeObserver !== "undefined" && containerRef.value) {
    observer = new ResizeObserver(() => syncScroll());
    observer.observe(containerRef.value);
  }
});
onUnmounted(() => {
  observer?.disconnect();
  window.clearTimeout(settleTimer);
});
</script>

<template>
  <div ref="containerRef" class="lyric-box">
    <div v-if="!items.length" class="lyric-empty">
      <MusicIcon size="26px" class="text-subtle/60" />
      <p class="text-xs mt-2">{{ idleHint || "暂无歌词" }}</p>
    </div>
    <div v-else ref="innerRef" class="lyric-track" :style="{ transform: `translateY(${-offset}px)` }">
      <p
        v-for="(item, index) in items"
        :key="item.id"
        class="lyric-line"
        :class="[
          item.kind === 'section' ? 'lyric-section' : lineClass(index),
          alignMode && item.kind === 'text' ? 'is-alignable' : '',
        ]"
        @click="onLineClick(item, index)"
      >
        {{ item.text }}
      </p>
    </div>
    <!-- 上下渐隐遮罩：让远处歌词自然淡出，不出现生硬的裁切边 -->
    <div class="lyric-mask lyric-mask-top" />
    <div class="lyric-mask lyric-mask-bottom" />
  </div>
</template>
