<script setup lang="ts">
/**
 * 右栏作品详情：封面、元信息、风格描述 / 歌词折叠面板、下载与删除。
 *
 * 纯展示组件，song 由父组件传入；删除前用 window.confirm 二次确认，
 * 复制走 navigator.clipboard（失败时降级为提示手动选择文本）。
 */
import {
  Button as TButton,
  Collapse as TCollapse,
  CollapsePanel as TCollapsePanel,
  MessagePlugin,
  Tag as TTag,
} from "tdesign-vue-next";
import { CloseIcon, DeleteIcon, DownloadIcon, FileCopyIcon } from "tdesign-icons-vue-next";

import type { GenerationResponse } from "@/interface";
import {
  coverGradient,
  formatMetaLine,
  formatRelative,
  getCoverIcon,
  paletteFor,
  statusText,
  statusTheme,
  taskTypeText,
  variantText,
} from "@/common/utils";

defineProps<{
  song: GenerationResponse;
}>();

const emit = defineEmits<{
  close: [];
  delete: [];
  download: [];
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

function remove() {
  if (window.confirm("确定永久删除这首作品吗？")) emit("delete");
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

    <div class="px-5 py-5 space-y-4">
      <div
        class="relative rounded-2xl aspect-square overflow-hidden ace-cover flex items-center justify-center"
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
          size="64px"
          class="text-white/90"
        />
      </div>

      <TButton block variant="outline" :disabled="!song.audio_url" @click="emit('download')">
        <span class="inline-flex items-center gap-1"><DownloadIcon size="16px" />下载音频</span>
      </TButton>

      <div class="rounded-xl bg-muted p-4 grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-0.5">
          <span class="text-[11px] text-subtle">模型</span>
          <span class="text-sm font-medium">{{ variantText[song.model_variant] }}</span>
        </div>
        <div class="flex flex-col gap-0.5">
          <span class="text-[11px] text-subtle">创建时间</span>
          <span class="text-sm font-medium">{{ formatRelative(song.created_at) }}</span>
        </div>
        <div class="flex flex-col gap-0.5">
          <span class="text-[11px] text-subtle">参数</span>
          <span class="text-sm font-medium">{{ formatMetaLine(song) }}</span>
        </div>
        <div class="flex flex-col gap-0.5">
          <span class="text-[11px] text-subtle">纯音乐</span>
          <span class="text-sm font-medium">{{ song.instrumental ? "是" : "否" }}</span>
        </div>
        <div v-if="song.error_message" class="flex flex-col gap-0.5">
          <span class="text-[11px] text-subtle">失败原因</span>
          <span class="text-sm font-medium">{{ song.error_message }}</span>
        </div>
      </div>

      <TCollapse :default-value="['prompt', 'lyrics']">
        <TCollapsePanel value="prompt" header="风格与描述">
          <div class="relative pt-1">
            <TButton class="!absolute !right-0 !top-0 z-10" size="small" variant="text" @click="copy(song.prompt)">
              <FileCopyIcon size="14px" />
            </TButton>
            <p class="text-xs leading-6 text-ink/80 whitespace-pre-wrap pr-10">{{ song.prompt || "暂无描述" }}</p>
          </div>
        </TCollapsePanel>
        <TCollapsePanel value="lyrics" header="歌词">
          <div class="relative pt-1">
            <TButton class="!absolute !right-0 !top-0 z-10" size="small" variant="text" @click="copy(song.lyrics)">
              <FileCopyIcon size="14px" />
            </TButton>
            <p class="text-xs leading-6 whitespace-pre-wrap max-h-64 overflow-y-auto ace-scroll pr-10">
              {{ song.lyrics || "暂无歌词" }}
            </p>
          </div>
        </TCollapsePanel>
      </TCollapse>

      <TButton block variant="outline" theme="danger" @click="remove">
        <span class="inline-flex items-center gap-1"><DeleteIcon size="16px" />删除作品</span>
      </TButton>
    </div>
  </aside>
</template>
