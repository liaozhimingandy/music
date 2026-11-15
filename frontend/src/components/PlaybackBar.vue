<script setup lang="ts">
/**
 * 底部完整播放栏（网易云风格）：左旋转黑胶、中控制区与进度条、右音量与菜单。
 *
 * 纯展示 + 事件组件，不持有 <audio>；所有操作 emit 给 StudioPage 处理。
 * 进度 / 音量用原生 range，通过 CSS 变量 --fill 画已播放部分（见 style.css）。
 * 黑胶旋转由 .netease-disc.is-playing 控制；「收起播放器」发 hide 事件。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Button as TButton, Tag as TTag } from "tdesign-vue-next";
import {
  BackwardIcon,
  ChevronDownIcon,
  DownloadIcon,
  ForwardIcon,
  HeartFilledIcon,
  HeartIcon,
  MoreIcon,
  PauseIcon,
  PlayIcon,
  SoundIcon,
  ViewListIcon,
} from "tdesign-icons-vue-next";

import type { GenerationResponse } from "@/interface";
import { coverGradient, formatClock, getCoverIcon, paletteFor, statusText, statusTheme, truncateText } from "@/common/utils";

const props = defineProps<{
  song?: GenerationResponse;
  isPlaying: boolean;
  progress: { current: number; duration: number };
  volume: number;
  hasPrev: boolean;
  hasNext: boolean;
  /** 当前正在唱的那句歌词；空字符串表示还没开始 / 没有歌词 */
  activeLyric?: string;
}>();

const emit = defineEmits<{
  playPause: [];
  seek: [value: number];
  download: [];
  hide: [];
  prev: [];
  next: [];
  volume: [value: number];
  showLibrary: [];
}>();

const menuOpen = ref(false);
const liked = ref(false);

/** 进度由 StudioPage 传入；current 钳制在总时长内，避免拖动越界 */
const duration = computed(() => props.progress.duration || 0);
const current = computed(() => Math.min(props.progress.current, duration.value || 0));
const percent = computed(() => (duration.value > 0 ? (current.value / duration.value) * 100 : 0));
const volumePercent = computed(() => Math.round(props.volume * 100));
const discIcon = computed(() => getCoverIcon(props.song?.cover_icon || paletteFor(props.song?.id || "empty").icon));

function closeMenu() {
  menuOpen.value = false;
}
onMounted(() => document.addEventListener("click", closeMenu));
onUnmounted(() => document.removeEventListener("click", closeMenu));

/** range 的 input 事件把字符串值转成秒 / 音量后，交给父组件处理 */
function onSeek(event: Event) {
  emit("seek", Number((event.target as HTMLInputElement).value));
}
function onVolume(event: Event) {
  emit("volume", Number((event.target as HTMLInputElement).value));
}
function download() {
  emit("download");
  closeMenu();
}
</script>

<template>
  <footer class="relative h-[88px] flex-shrink-0 bg-surface border-t border-line flex items-center px-5 gap-5 shadow-panel z-20">
    <!-- 左：旋转黑胶唱片 + 歌曲信息（网易云经典布局） -->
    <div class="flex items-center gap-3 flex-1 min-w-0">
      <div
        class="netease-disc w-[58px] h-[58px]"
        :class="{ 'is-playing': isPlaying }"
        :title="isPlaying ? '正在播放' : '已暂停'"
      >
        <div class="netease-disc-center" :style="{ backgroundImage: coverGradient(song?.id || 'empty') }">
          <img
            v-if="song?.cover_image_url"
            :src="song.cover_image_url"
            :alt="song.title || '封面'"
            class="w-full h-full object-cover absolute inset-0"
          />
          <component :is="discIcon" v-else size="18px" class="text-white/90" />
        </div>
        <span class="netease-disc-hole" />
      </div>
      <div class="min-w-0">
        <p class="text-sm font-semibold truncate">
          {{ song ? truncateText(song.title || song.prompt, 24) : "准备就绪" }}
        </p>
        <!-- 播放中优先显示正在唱的那句歌词（与右侧同步歌词同一时间轴），
             没有歌词时回退到状态标签 + 类型说明 -->
        <div class="flex items-center gap-2 mt-1.5 min-h-[20px]">
          <TTag v-if="song" size="small" variant="light" :theme="statusTheme(song.status)">
            {{ statusText[song.status] || song.status }}
          </TTag>
          <Transition name="lyric-swap" mode="out-in">
            <span
              v-if="activeLyric"
              :key="activeLyric"
              class="text-xs text-brand-600 font-medium truncate"
              :title="activeLyric"
            >
              {{ activeLyric }}
            </span>
            <span v-else key="meta" class="text-xs text-subtle truncate">
              {{ song ? (song.instrumental ? "纯音乐 · AI 生成" : "AI 生成 · 华语") : "从作品库选择一首歌曲" }}
            </span>
          </Transition>
        </div>
      </div>
      <TButton
        size="small"
        variant="text"
        shape="square"
        :title="liked ? '取消喜欢' : '喜欢'"
        @click="liked = !liked"
      >
        <HeartFilledIcon v-if="liked" class="text-brand-600" />
        <HeartIcon v-else />
      </TButton>
    </div>

    <!-- 中：网易云式控制区（上一首 / 播放 / 下一首 + 细进度条） -->
    <div class="w-[420px] max-w-[42%] flex flex-col items-center gap-1.5">
      <div class="flex items-center gap-4">
        <TButton size="small" variant="text" shape="circle" :disabled="!hasPrev" title="上一首" @click="emit('prev')">
          <BackwardIcon />
        </TButton>
        <button
          class="w-9 h-9 rounded-full bg-brand-500 hover:bg-brand-600 active:bg-brand-700 disabled:bg-line disabled:cursor-not-allowed flex items-center justify-center text-white transition-colors shadow-[0_4px_12px_rgba(236,65,65,0.32)]"
          :disabled="!song?.audio_url"
          :title="isPlaying ? '暂停' : '播放'"
          @click="emit('playPause')"
        >
          <PauseIcon v-if="isPlaying" size="18px" />
          <PlayIcon v-else size="18px" />
        </button>
        <TButton size="small" variant="text" shape="circle" :disabled="!hasNext" title="下一首" @click="emit('next')">
          <ForwardIcon />
        </TButton>
      </div>
      <div class="flex items-center gap-2 w-full text-[11px] text-subtle tabular-nums">
        <span class="w-9 text-right">{{ formatClock(current) }}</span>
        <input
          class="netease-range flex-1"
          type="range"
          min="0"
          :max="duration || 1"
          step="0.1"
          :value="current"
          :style="{ '--fill': `${percent}%` }"
          :disabled="!song?.audio_url || !duration"
          :aria-label="'播放进度'"
          @input="onSeek"
        />
        <span class="w-9">{{ formatClock(duration) }}</span>
      </div>
    </div>

    <!-- 右：音量 / 播放列表 / 更多 / 收起 -->
    <div class="flex-1 flex items-center justify-end gap-1">
      <div class="flex items-center gap-1.5 w-28 mr-1">
        <SoundIcon size="16px" class="text-subtle flex-shrink-0" />
        <input
          class="netease-range flex-1"
          type="range"
          min="0"
          max="1"
          step="0.01"
          :value="volume"
          :style="{ '--fill': `${volumePercent}%` }"
          :aria-label="'音量'"
          :title="`音量 ${volumePercent}%`"
          @input="onVolume"
        />
      </div>
      <TButton size="small" variant="text" shape="square" title="作品库" @click="emit('showLibrary')">
        <ViewListIcon />
      </TButton>
      <div @click.stop>
        <TButton size="small" variant="text" shape="square" title="更多" @click="menuOpen = !menuOpen">
          <MoreIcon />
        </TButton>
      </div>
      <TButton size="small" variant="text" shape="square" title="收起播放器" @click="emit('hide')">
        <ChevronDownIcon />
      </TButton>

      <div
        v-if="menuOpen"
        class="absolute right-4 bottom-[76px] w-36 rounded-xl bg-surface border border-line shadow-pop z-30 p-1"
        @click.stop
      >
        <TButton v-if="song" block variant="text" size="small" :disabled="!song.audio_url" @click="download">
          <span class="inline-flex items-center gap-1"><DownloadIcon size="14px" />下载音频</span>
        </TButton>
      </div>
    </div>
  </footer>
</template>

<style scoped>
/* 歌词行切换：轻微上移淡出 / 淡入，避免文本硬闪 */
.lyric-swap-enter-active,
.lyric-swap-leave-active {
  transition:
    opacity 0.22s ease,
    transform 0.22s ease;
}
.lyric-swap-enter-from {
  opacity: 0;
  transform: translateY(5px);
}
.lyric-swap-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}
@media (prefers-reduced-motion: reduce) {
  .lyric-swap-enter-active,
  .lyric-swap-leave-active {
    transition: none;
  }
}
</style>
