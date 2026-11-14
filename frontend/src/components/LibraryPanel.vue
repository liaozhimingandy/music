<script setup lang="ts">
/**
 * 中间栏「作品库」卡片列表。
 *
 * 渲染两类卡片：pendingItems（本地「生成中」占位，展示分阶段进度）与
 * items（后端返回的真实作品）。卡片 DOM id 为 `song-<id>`，供顶栏的
 * showLibrary() 用 scrollIntoView 定位到当前选中作品。
 * 右上角更多菜单靠全局 click 监听关闭（onMounted / onUnmounted 成对解绑）。
 */
import { onMounted, onUnmounted, ref } from "vue";
import { Button as TButton, Progress as TProgress, Tag as TTag } from "tdesign-vue-next";
import { DeleteIcon, DownloadIcon, MoreIcon, PauseIcon, PlayIcon } from "tdesign-icons-vue-next";

import type { GenerationResponse, PendingGeneration } from "@/interface";
import {
  coverGradient,
  formatMetaLine,
  formatRelative,
  getCoverIcon,
  paletteFor,
  statusText,
  statusTheme,
  taskTypeText,
  truncateText,
  variantShortLabel,
} from "@/common/utils";

defineProps<{
  items: GenerationResponse[];
  pendingItems: PendingGeneration[];
  selectedId?: string;
  currentlyPlayingId?: string;
  isPlaying: boolean;
}>();

const emit = defineEmits<{
  select: [id: string];
  delete: [id: string];
  togglePlay: [item: GenerationResponse];
  download: [item: GenerationResponse];
}>();

/** 同一时刻只允许一个卡片展开更多菜单；点击空白处由全局监听统一关闭 */
const menuOpenId = ref<string | null>(null);

function closeMenu() {
  menuOpenId.value = null;
}
onMounted(() => document.addEventListener("click", closeMenu));
onUnmounted(() => document.removeEventListener("click", closeMenu));

function toggleMenu(id: string) {
  menuOpenId.value = menuOpenId.value === id ? null : id;
}

function downloadItem(item: GenerationResponse) {
  emit("download", item);
  closeMenu();
}

function deleteItem(id: string) {
  emit("delete", id);
  closeMenu();
}
</script>

<template>
  <section class="ace-scroll flex-1 overflow-y-auto bg-canvas">
    <div class="px-6 py-5">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-lg font-semibold">作品库</h2>
          <p class="text-xs text-subtle mt-0.5">所有生成记录都保存在本地</p>
        </div>
        <div class="flex items-center gap-2">
          <TTag variant="light" theme="primary">共 {{ items.length + pendingItems.length }} 首</TTag>
          <span class="text-xs text-subtle">自动刷新</span>
        </div>
      </div>

      <!-- 生成中的本地占位卡片：阶段进度来自 pendingItems，不来自后端 -->
      <div class="space-y-3">
        <div
          v-for="item in pendingItems"
          :key="item.tempId"
          class="rounded-2xl bg-surface border border-dashed border-brand-200 p-4 shadow-panel"
        >
          <div class="flex items-start justify-between">
            <p class="text-sm font-semibold">{{ truncateText(item.title, 32) }}</p>
            <TButton size="small" variant="text" theme="danger" @click="emit('delete', item.tempId)">取消</TButton>
          </div>
          <p class="text-xs text-subtle mt-2 line-clamp-2">{{ item.prompt || "正在构思…" }}</p>
          <div class="flex items-center gap-3 mt-3">
            <div class="flex-1">
              <TProgress
                :percentage="Math.round(((item.stageIndex + 1) / Math.max(1, item.stageLabels.length)) * 100)"
                :label="false"
                theme="line"
              />
            </div>
            <span class="text-xs text-brand-600 whitespace-nowrap">
              {{ item.error ? "生成失败" : item.stageLabels[Math.min(item.stageIndex, item.stageLabels.length - 1)] || "准备中" }}
            </span>
          </div>
          <p v-if="item.error" class="text-xs text-red-500 mt-2">{{ item.error }}</p>
        </div>

        <div
          v-for="item in items"
          :id="`song-${item.id}`"
          :key="item.id"
          class="group relative rounded-2xl bg-surface border p-4 shadow-panel cursor-pointer transition hover:border-brand-200"
          :class="selectedId === item.id ? 'border-brand-500 ring-2 ring-brand-100' : 'border-line'"
          @click="emit('select', item.id)"
        >
          <div class="flex gap-4">
            <div
              class="w-[88px] h-[88px] rounded-xl flex-shrink-0 relative overflow-hidden flex items-center justify-center ace-cover"
              :style="{ backgroundImage: coverGradient(item.id) }"
            >
              <img
                v-if="item.cover_image_url"
                :src="item.cover_image_url"
                :alt="item.title || '封面'"
                class="w-full h-full object-cover absolute inset-0"
              />
              <component
                :is="getCoverIcon(item.cover_icon || paletteFor(item.id).icon)"
                v-else
                size="36px"
                class="text-white/90"
              />
              <button
                v-if="item.audio_url"
                class="absolute inset-0 flex items-center justify-center bg-black/35 opacity-0 group-hover:opacity-100 transition"
                :aria-label="currentlyPlayingId === item.id && isPlaying ? '暂停' : '播放'"
                @click.stop="emit('togglePlay', item)"
              >
                <PauseIcon v-if="currentlyPlayingId === item.id && isPlaying" size="28px" class="text-white" />
                <PlayIcon v-else size="28px" class="text-white" />
              </button>
            </div>

            <div class="flex-1 min-w-0">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <p class="text-sm font-semibold truncate">{{ truncateText(item.title || item.prompt, 32) }}</p>
                  <div class="flex flex-wrap items-center gap-1.5 mt-1.5">
                    <TTag size="small" variant="light" theme="primary">
                      {{ variantShortLabel(item.model_variant) }}
                    </TTag>
                    <TTag size="small" variant="light">{{ taskTypeText[item.task_type] || "文生音乐" }}</TTag>
                    <TTag size="small" variant="light" :theme="statusTheme(item.status)">
                      {{ statusText[item.status] || item.status }}
                    </TTag>
                  </div>
                </div>
                <div class="shrink-0" @click.stop>
                  <TButton size="small" variant="text" shape="square" @click="toggleMenu(item.id)">
                    <MoreIcon />
                  </TButton>
                </div>
              </div>
              <p class="text-xs text-subtle mt-2">{{ formatMetaLine(item) }}</p>
              <p class="text-xs text-subtle/80 mt-1 truncate">{{ item.prompt || "暂无描述" }}</p>
              <p class="text-[11px] text-subtle/60 mt-1">{{ formatRelative(item.created_at) }}</p>
            </div>
          </div>

          <div
            v-if="menuOpenId === item.id"
            class="absolute right-4 top-12 w-36 rounded-xl bg-surface border border-line shadow-pop z-20 p-1"
            @click.stop
          >
            <TButton
              block
              variant="text"
              size="small"
              :disabled="!item.audio_url"
              @click="downloadItem(item)"
            >
              <span class="inline-flex items-center gap-1"><DownloadIcon size="14px" />下载音频</span>
            </TButton>
            <TButton block variant="text" size="small" theme="danger" @click="deleteItem(item.id)">
              <span class="inline-flex items-center gap-1"><DeleteIcon size="14px" />删除</span>
            </TButton>
          </div>
        </div>

        <div
          v-if="items.length + pendingItems.length === 0"
          class="rounded-2xl border border-dashed border-line bg-surface/60 py-16 text-center"
        >
          <p class="text-sm font-medium">还没有作品</p>
          <p class="text-xs text-subtle mt-2">在左侧描述你的想法，点击「开始生成」</p>
        </div>
      </div>
    </div>
  </section>
</template>
