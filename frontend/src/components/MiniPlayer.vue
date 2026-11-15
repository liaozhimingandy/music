<script setup lang="ts">
/**
 * 播放器收起后的右下角悬浮入口：小唱片 + 播放/暂停 + 展开按钮。
 * 与 PlaybackBar 互斥显示（由 StudioPage 的 playerVisible 切换），
 * 这样收起播放器后仍能控制播放、随时展开。
 */
import { computed } from "vue";
import { ChevronUpIcon, PauseIcon, PlayIcon } from "tdesign-icons-vue-next";

import type { GenerationResponse } from "@/interface";
import { coverGradient, getCoverIcon, paletteFor, truncateText } from "@/common/utils";

const props = defineProps<{
  song?: GenerationResponse;
  isPlaying: boolean;
}>();

const emit = defineEmits<{
  playPause: [];
  show: [];
}>();

const discIcon = computed(() => getCoverIcon(props.song?.cover_icon || paletteFor(props.song?.id || "empty").icon));
</script>

<template>
  <div
    class="fixed bottom-4 right-4 md:bottom-5 md:right-5 z-40 flex items-center gap-2.5 pl-2.5 pr-1.5 py-1.5 rounded-full bg-surface border border-line shadow-pop max-w-[calc(100vw-2rem)]"
  >
    <div class="netease-disc w-9 h-9" :class="{ 'is-playing': isPlaying }">
      <div class="netease-disc-center" :style="{ backgroundImage: coverGradient(song?.id || 'empty') }">
        <img
          v-if="song?.cover_image_url"
          :src="song.cover_image_url"
          :alt="song.title || '封面'"
          class="w-full h-full object-cover absolute inset-0"
        />
        <component :is="discIcon" v-else size="12px" class="text-white/90" />
      </div>
      <span class="netease-disc-hole" />
    </div>
    <span class="text-xs font-medium max-w-[132px] truncate hidden sm:block">
      {{ song ? truncateText(song.title || song.prompt, 12) : "播放器已收起" }}
    </span>
    <button
      class="w-8 h-8 rounded-full bg-brand-500 hover:bg-brand-600 disabled:bg-line disabled:cursor-not-allowed flex items-center justify-center text-white transition-colors"
      :disabled="!song?.audio_url"
      :title="isPlaying ? '暂停' : '播放'"
      @click="emit('playPause')"
    >
      <PauseIcon v-if="isPlaying" size="15px" />
      <PlayIcon v-else size="15px" />
    </button>
    <button
      class="w-8 h-8 rounded-full flex items-center justify-center text-subtle hover:text-brand-600 hover:bg-muted transition-colors"
      title="展开播放器"
      @click="emit('show')"
    >
      <ChevronUpIcon size="18px" />
    </button>
  </div>
</template>
