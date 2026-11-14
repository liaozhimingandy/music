<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Button as TButton, Tag as TTag } from "tdesign-vue-next";
import {
  BackwardIcon,
  ChevronDownIcon,
  DeleteIcon,
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
}>();

const emit = defineEmits<{
  playPause: [];
  seek: [value: number];
  download: [];
  delete: [];
  hide: [];
  prev: [];
  next: [];
  volume: [value: number];
  showLibrary: [];
}>();

const menuOpen = ref(false);
const liked = ref(false);

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
function remove() {
  if (window.confirm("确定永久删除这首作品吗？")) emit("delete");
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
        <div class="flex items-center gap-2 mt-1.5">
          <TTag v-if="song" size="small" variant="light" :theme="statusTheme(song.status)">
            {{ statusText[song.status] || song.status }}
          </TTag>
          <span class="text-xs text-subtle truncate">
            {{ song ? (song.instrumental ? "纯音乐 · AI 生成" : "AI 生成 · 华语") : "从作品库选择一首歌曲" }}
          </span>
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
        <TButton v-if="song" block variant="text" size="small" theme="danger" @click="remove">
          <span class="inline-flex items-center gap-1"><DeleteIcon size="14px" />删除作品</span>
        </TButton>
      </div>
    </div>
  </footer>
</template>
