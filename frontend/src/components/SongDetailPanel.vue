<script setup lang="ts">
/**
 * 右栏作品详情：封面、同步歌词、元信息、风格描述与下载。
 *
 * 纯展示组件：song / 歌词时间轴 / 当前行下标都由 StudioPage 传入，
 * 本组件不碰 <audio>，只把「点歌词」「微调校准」这类操作 emit 回去。
 *
 * 歌词是重点功能，所以不再塞进折叠面板：固定展示同步滚动区
 * （LyricScroller），高亮行由父组件的 activeLyricIndex 驱动。
 * 时间轴没有真时间戳时（后端只给纯文本歌词），右上角会标注「按节拍估算」。
 *
 * 两种校准方式（都按作品存 localStorage，见 StudioPage 的 lyricShift）：
 * 1. 「提前 / 延后」±0.3 秒微调，用于整体偏一点点的场景；
 * 2. 「对齐当前句」模式：播放中唱到哪句就点那句话，父组件据此一次性算出
 *    偏移（shift = 该句 start - currentTime），比反复点按钮快得多。
 *
 * 不提供删除入口：作品由服务器定时清理，用户需自行下载保存；
 * 复制走 navigator.clipboard（失败时提示手动选择文本）。
 */
import { Button as TButton, MessagePlugin, Tag as TTag } from "tdesign-vue-next";
import { CloseIcon, DownloadIcon, FileCopyIcon, InfoCircleIcon } from "tdesign-icons-vue-next";

import type { GenerationResponse } from "@/interface";
import type { LyricItem } from "@/common/lyric";
import LyricScroller from "@/components/LyricScroller.vue";
import {
  coverGradient,
  formatMetaLine,
  formatRelative,
  getCoverIcon,
  paletteFor,
  statusText,
  statusTheme,
  taskTypeText,
  variantLabel,
} from "@/common/utils";

defineProps<{
  song: GenerationResponse;
  lyricItems: LyricItem[];
  activeLyricIndex: number;
  /** true = 时间轴按字重估算（后端没有 LRC），UI 上提示可微调 */
  lyricEstimated: boolean;
  /** 校准偏移：>0 表示歌词提前亮 */
  lyricShift: number;
  /** 「对齐当前句」模式：点歌词不跳转，而是把这句对齐到当前播放位置 */
  lyricAligning: boolean;
}>();

const emit = defineEmits<{
  close: [];
  download: [];
  seekLyric: [time: number];
  shiftLyric: [delta: number];
  toggleAlign: [];
  alignLyric: [index: number];
}>();

/** 复制文本到剪贴板；非安全上下文或权限被拒时降级为提示手动复制 */
async function copy(text?: string | null) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    MessagePlugin.success("已复制到剪贴板");
  } catch {
    MessagePlugin.info("复制失败，请手动选择文本");
  }
}

/** 校准值展示成 +0.3s / -0.3s */
function shiftText(value: number): string {
  if (!value) return "0s";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}s`;
}
</script>

<template>
  <aside class="ace-scroll w-[340px] min-w-[300px] overflow-y-auto bg-surface border-l border-line flex flex-col shadow-panel z-10">
    <div class="px-5 py-4 border-b border-line flex items-start justify-between gap-2">
      <div class="min-w-0">
        <h2 class="text-base font-semibold truncate">{{ song.title || "未命名作品" }}</h2>
        <div class="flex items-center gap-1.5 mt-2">
          <TTag size="small" variant="light" :theme="statusTheme(song.status)">
            {{ statusText[song.status] || song.status }}
          </TTag>
          <TTag size="small" variant="light">{{ taskTypeText[song.task_type] || "文生音乐" }}</TTag>
        </div>
      </div>
      <TButton size="small" variant="text" shape="square" @click="emit('close')">
        <CloseIcon />
      </TButton>
    </div>

    <div class="px-5 py-4 space-y-3.5">
      <!-- 概览：小封面 + 关键元信息。刻意压缩高度，把首屏空间让给歌词 -->
      <div class="flex gap-3">
        <div
          class="relative w-[88px] h-[88px] rounded-xl overflow-hidden flex-shrink-0 flex items-center justify-center ace-cover"
          :style="{ backgroundImage: coverGradient(song.id) }"
        >
          <img
            v-if="song.cover_image_url"
            :src="song.cover_image_url"
            :alt="song.title || '封面'"
            class="w-full h-full object-cover absolute inset-0"
          />
          <component
            :is="getCoverIcon(song.cover_icon || paletteFor(song.id).icon)"
            v-else
            size="32px"
            class="text-white/90"
          />
        </div>
        <div class="flex-1 min-w-0 flex flex-col justify-center gap-1.5">
          <div class="flex items-baseline gap-1.5 min-w-0">
            <span class="text-[11px] text-subtle shrink-0">模型</span>
            <span class="text-xs font-medium truncate">{{ variantLabel(song.model_variant) }}</span>
          </div>
          <p class="text-[11px] text-subtle truncate" :title="formatMetaLine(song)">{{ formatMetaLine(song) }}</p>
          <p class="text-[11px] text-subtle">
            {{ formatRelative(song.created_at) }} · {{ song.instrumental ? "纯音乐" : "含人声" }}
          </p>
          <TButton size="small" variant="outline" :disabled="!song.audio_url" class="mt-0.5" @click="emit('download')">
            <span class="inline-flex items-center gap-1"><DownloadIcon size="14px" />下载音频</span>
          </TButton>
        </div>
      </div>

      <p v-if="song.error_message" class="text-[11px] text-red-500 leading-5">失败原因：{{ song.error_message }}</p>

      <!-- 同步歌词：整块展示，播放时自动滚到当前行 -->
      <section class="rounded-xl border border-line overflow-hidden">
        <header class="flex items-center justify-between gap-2 px-3 py-2 border-b border-line bg-muted/60">
          <div class="flex items-center min-w-0 gap-1.5">
            <span class="text-xs font-semibold">歌词</span>
            <span v-if="lyricItems.length" class="text-[10px] text-subtle truncate">
              {{ lyricEstimated ? "按节拍估算 · 可微调" : "LRC 精确对齐" }}
            </span>
          </div>
          <TButton size="small" variant="text" shape="square" title="复制歌词" @click="copy(song.lyrics)">
            <FileCopyIcon size="14px" />
          </TButton>
        </header>

        <LyricScroller
          :items="lyricItems"
          :active-index="activeLyricIndex"
          :exact="!lyricEstimated"
          :align-mode="lyricAligning"
          :idle-hint="song.instrumental ? '纯音乐作品，无歌词' : '暂无歌词'"
          @seek="emit('seekLyric', $event)"
          @align="emit('alignLyric', $event)"
        />

        <footer v-if="lyricItems.length" class="px-3 py-1.5 border-t border-line">
          <div class="flex items-center justify-between gap-2">
            <span class="text-[10px] text-subtle whitespace-nowrap">同步不准？</span>
            <div class="flex items-center gap-0.5">
              <TButton size="small" variant="text" title="歌词提前 0.3 秒" @click="emit('shiftLyric', 0.3)">
                提前
              </TButton>
              <span class="text-[10px] text-subtle tabular-nums w-9 text-center">{{ shiftText(lyricShift) }}</span>
              <TButton size="small" variant="text" title="歌词延后 0.3 秒" @click="emit('shiftLyric', -0.3)">
                延后
              </TButton>
              <TButton
                size="small"
                variant="text"
                :theme="lyricAligning ? 'primary' : 'default'"
                title="播放中听到哪句就点那句话，一次对齐"
                @click="emit('toggleAlign')"
              >
                {{ lyricAligning ? "退出对齐" : "对齐当前句" }}
              </TButton>
              <TButton v-if="lyricShift" size="small" variant="text" @click="emit('shiftLyric', -lyricShift)">
                重置
              </TButton>
            </div>
          </div>
          <p v-if="lyricAligning" class="text-[10px] text-brand-600 mt-0.5 leading-4">
            播放到哪一句就点那句歌词，立即对齐到当前进度（不会再跳转播放位置）
          </p>
        </footer>
      </section>

      <div class="relative rounded-xl border border-line p-3">
        <div class="flex items-center justify-between mb-1">
          <span class="text-xs font-semibold">风格与描述</span>
          <TButton size="small" variant="text" shape="square" title="复制描述" @click="copy(song.prompt)">
            <FileCopyIcon size="14px" />
          </TButton>
        </div>
        <p class="text-xs leading-6 text-ink/80 whitespace-pre-wrap max-h-32 overflow-y-auto ace-scroll pr-1">
          {{ song.prompt || "暂无描述" }}
        </p>
      </div>

      <p class="flex items-start gap-1.5 text-[11px] text-amber-600 leading-5">
        <InfoCircleIcon size="14px" class="mt-0.5 shrink-0" />
        作品由服务器定时清理，不会永久保存，请及时下载备份
      </p>
    </div>
  </aside>
</template>
