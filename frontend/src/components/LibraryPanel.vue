<script setup lang="ts">
/**
 * 中间栏「作品库」卡片列表。
 *
 * 渲染三类卡片：
 * - pendingItems：本地「生成中」占位（刚提交、还没拿到后端记录）；
 * - items 中 status=queued/processing 的作品：真正在跑的生成任务，
 *   卡片带流光描边 + 封面频谱条 + 阶段进度条（current_stage 驱动）；
 * - items 中已完成的作品：封面（左侧）hover 会出现播放遮罩；右上角更多菜单可下载。
 *
 * 交互约定（容易改错，注意）：**点卡片本体只发 select（看详情），不播放**。
 * 播放必须点「封面上的播放按钮」——浏览作品库是高频随手动作，误点就切歌会很难受。
 * 播放 / 暂停统一发 togglePlay，由 StudioPage 决定是播当前还是切到这一首。
 *
 * **仅生成中的卡片不可点击**（isUnclickable）：生成中误点会打断正在听的歌，
 * 因此禁掉选中。生成失败的歌**允许点击**——点开详情面板看「失败原因」，它本身没有
 * 音频、点它也不会触发播放，所以放行不会带来误停风险（播放按钮本就不渲染）。
 *
 * 卡片 DOM id 为 `song-<id>`，供顶栏的 showLibrary() 用 scrollIntoView 定位。
 * 菜单靠全局 click 监听关闭（onMounted / onUnmounted 成对解绑）。
 * 刚生成的卡会加一次 `ace-ready-flash` 高亮（statusMap 对比前后状态得到）。
 * 不提供删除入口：作品由服务器定时清理，用户需自行下载保存。
 */
import { onMounted, onUnmounted, ref, watch } from "vue";
import { Button as TButton, Tag as TTag } from "tdesign-vue-next";
import { DownloadIcon, MoreIcon, PauseIcon, PlayIcon } from "tdesign-icons-vue-next";

import type { GenerationResponse, PendingGeneration } from "@/interface";
import { STAGE_ORDER, completedStageCount, stageLabel } from "@/common/constants";
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

const props = defineProps<{
  items: GenerationResponse[];
  pendingItems: PendingGeneration[];
  selectedId?: string;
  currentlyPlayingId?: string;
  isPlaying: boolean;
  /** 首次拉取作品列表期间显示骨架屏 */
  loading?: boolean;
}>();

const emit = defineEmits<{
  select: [id: string];
  /** 取消一条本地「生成中」占位卡片（不调后端，仅移除本地状态） */
  cancel: [tempId: string];
  togglePlay: [item: GenerationResponse];
  download: [item: GenerationResponse];
}>();

/** 同一时刻只允许一个卡片展开更多菜单；点击空白处由全局监听统一关闭 */
const menuOpenId = ref<string | null>(null);
/** 刚完成的作品 id 集合：用于播放一次「出歌」高亮，1.2s 后自动移除 */
const flashed = ref<string[]>([]);
/** 上一次轮询时的状态快照，用于识别 queued/processing -> ready 的瞬间 */
const statusMap: Record<string, string> = {};

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

/** queued / processing 都算「生成中」，卡片用动效态渲染 */
function isGenerating(item: GenerationResponse): boolean {
  return item.status === "queued" || item.status === "processing" || item.status === "pending";
}

/**
 * 不可点击的卡片：仅「生成中」（queued/processing/pending）。
 * 生成中误点会打断正在播放的歌，所以禁掉选中（@click 不 emit select），
 * 并给 cursor-not-allowed + 半透明提示不可交互。
 *
 * 注意：**生成失败的歌允许点击**。失败卡片没有可播放音频、点它也不会触发任何
 * 播放，但用户需要点开详情面板看「失败原因」——所以这里特意放行 failed/error，
 * 只把真正在跑的任务挡住（避免误点停掉正在听的音乐）。失败歌的播放按钮本来就不
 * 渲染（没有 audio_url），因此「可点查看、只是没有播放按钮」的体验自然成立。
 */
function isUnclickable(item: GenerationResponse): boolean {
  return isGenerating(item);
}

/** 阶段进度百分比：4/5 的阶段完成会显示 80% */
function stagePercent(item: GenerationResponse): number {
  return Math.round((completedStageCount(item.current_stage, item.status) / STAGE_ORDER.length) * 100);
}

/**
 * 生成中卡片的阶段文案。排队阶段没有 current_stage，直接说「排队中」，
 * 不能拼成「准备中进行中…」这种病句。
 */
function generatingLabel(item: GenerationResponse): string {
  if (item.status === "queued" || !item.current_stage) return "排队中，等待调度…";
  return `${stageLabel(item.current_stage)}进行中…`;
}

/** 轮询刷新后比对状态：变成 ready 的那一张闪一下 */
watch(
  () => props.items,
  (list) => {
    for (const item of list) {
      const previous = statusMap[item.id];
      statusMap[item.id] = item.status;
      if (previous && previous !== "ready" && item.status === "ready") {
        flashed.value = [...flashed.value, item.id];
        window.setTimeout(() => {
          flashed.value = flashed.value.filter((id) => id !== item.id);
        }, 1200);
      }
    }
  },
  { deep: true },
);
</script>

<template>
  <section class="ace-scroll flex-1 overflow-y-auto bg-canvas">
    <div class="px-6 py-5">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-lg font-semibold">作品库</h2>
          <p class="text-xs text-subtle mt-0.5">作品由服务器定时清理，请及时下载保存</p>
        </div>
        <div class="flex items-center gap-2">
          <TTag variant="light" theme="primary">共 {{ items.length + pendingItems.length }} 首</TTag>
          <span class="text-xs text-subtle">自动刷新</span>
        </div>
      </div>

      <div class="space-y-3">
        <!-- 生成中的本地占位卡片：刚提交、后端记录还没回来 -->
        <div
          v-for="item in pendingItems"
          :key="item.tempId"
          class="ace-rise ace-generating rounded-2xl bg-surface border border-dashed border-brand-200 p-4 shadow-panel"
        >
          <div class="flex items-start justify-between gap-2">
            <p class="text-sm font-semibold truncate">{{ truncateText(item.title, 32) }}</p>
            <TButton size="small" variant="text" theme="danger" @click="emit('cancel', item.tempId)">取消</TButton>
          </div>
          <p class="text-xs text-subtle mt-2 line-clamp-2">{{ item.prompt || "正在构思…" }}</p>
          <div class="flex items-center gap-3 mt-3">
            <span v-if="!item.error" class="ace-eq text-brand-500" style="height: 14px" aria-hidden="true">
              <i /><i /><i /><i /><i />
            </span>
            <div class="flex-1 h-1 rounded-full bg-brand-100 overflow-hidden">
              <div class="h-full rounded-full bg-brand-500 ace-shimmer-line" style="width: 35%" />
            </div>
            <span class="text-xs whitespace-nowrap" :class="item.error ? 'text-red-500' : 'ace-shimmer-text font-medium'">
              {{ item.error ? "提交失败" : "提交中…" }}
            </span>
          </div>
          <p v-if="item.error" class="text-xs text-red-500 mt-2">{{ item.error }}</p>
        </div>

        <!-- 首屏骨架屏：后端还没返回第一份列表时避免空白闪跳 -->
        <template v-if="loading && !items.length && !pendingItems.length">
          <div v-for="index in 3" :key="`skeleton-${index}`" class="rounded-2xl bg-surface border border-line p-4">
            <div class="flex gap-4">
              <div class="ace-skeleton w-[88px] h-[88px] rounded-xl" />
              <div class="flex-1 space-y-2 py-1">
                <div class="ace-skeleton h-4 w-2/3" />
                <div class="ace-skeleton h-3 w-1/3" />
                <div class="ace-skeleton h-3 w-1/2" />
              </div>
            </div>
          </div>
        </template>

        <div
          v-for="item in items"
          :id="`song-${item.id}`"
          :key="item.id"
          class="ace-rise group relative rounded-2xl bg-surface border p-4 shadow-panel transition-all duration-300 hover:border-brand-200 hover:shadow-pop"
          :class="[
            selectedId === item.id ? 'border-brand-500 ring-2 ring-brand-100' : 'border-line',
            isGenerating(item) ? 'ace-generating' : '',
            flashed.includes(item.id) ? 'ace-ready-flash' : '',
            isUnclickable(item) ? 'cursor-not-allowed opacity-70' : 'cursor-pointer',
          ]"
          @click="!isUnclickable(item) && emit('select', item.id)"
        >
          <div class="flex gap-4">
            <div
              class="w-[88px] h-[88px] rounded-xl flex-shrink-0 relative overflow-hidden flex items-center justify-center ace-cover"
              :style="{ backgroundImage: coverGradient(item.id) }"
            >
              <img
                v-if="item.cover_image_url && !isGenerating(item)"
                :src="item.cover_image_url"
                :alt="item.title || '封面'"
                class="w-full h-full object-cover absolute inset-0"
              />
              <component
                v-else-if="!isGenerating(item)"
                :is="getCoverIcon(item.cover_icon || paletteFor(item.id).icon)"
                size="36px"
                class="text-white/90"
              />

              <!-- 生成中：封面盖一层频谱条，让「在跑」有一眼可见的活体感。
                   这一层同时替代封面图标，避免图标和频谱条叠在一起看不清。 -->
              <div
                v-if="isGenerating(item)"
                class="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/35"
              >
                <span class="ace-eq text-white" style="height: 26px" aria-hidden="true"><i /><i /><i /><i /><i /></span>
                <span class="text-[9px] text-white/80 tracking-wide">生成中</span>
              </div>
              <button
                v-else-if="item.audio_url"
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
                <div class="shrink-0 flex items-center gap-0.5" @click.stop>
                  <!-- 播放走封面（左侧）的 hover 遮罩按钮，这里只留更多菜单 -->
                  <TButton size="small" variant="text" shape="square" @click="toggleMenu(item.id)">
                    <MoreIcon />
                  </TButton>
                </div>
              </div>

              <!-- 生成中：阶段名 + 进度条（宽度随轮询平滑过渡） -->
              <div v-if="isGenerating(item)" class="mt-2.5">
                <div class="flex items-center justify-between gap-2 mb-1.5">
                  <span class="text-[11px] ace-shimmer-text font-medium">
                    {{ generatingLabel(item) }}
                  </span>
                  <span class="text-[11px] text-subtle tabular-nums">{{ stagePercent(item) }}%</span>
                </div>
                <div class="h-1 rounded-full bg-brand-100 overflow-hidden">
                  <div
                    class="h-full rounded-full bg-brand-500 transition-[width] duration-700 ease-out"
                    :style="{ width: `${Math.max(stagePercent(item), 6)}%` }"
                  />
                </div>
                <p class="text-[11px] text-subtle/80 mt-1.5">音频生成耗时较长，可先去听别的作品</p>
              </div>

              <template v-else>
                <p class="text-xs text-subtle mt-2">{{ formatMetaLine(item) }}</p>
                <p class="text-xs text-subtle/80 mt-1 truncate">{{ item.prompt || "暂无描述" }}</p>
                <p class="text-[11px] text-subtle/60 mt-1">{{ formatRelative(item.created_at) }}</p>
              </template>
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
          </div>
        </div>

        <div
          v-if="!loading && items.length + pendingItems.length === 0"
          class="rounded-2xl border border-dashed border-line bg-surface/60 py-16 text-center"
        >
          <span class="ace-eq text-brand-400" style="height: 20px" aria-hidden="true"><i /><i /><i /><i /><i /></span>
          <p class="text-sm font-medium mt-3">还没有作品</p>
          <p class="text-xs text-subtle mt-2">在左侧描述你的想法，点击「开始生成」</p>
        </div>
      </div>
    </div>
  </section>
</template>
