<script setup lang="ts">
/**
 * 主工作台页面：三栏布局（简单创作 / 作品库 / 歌曲详情）+ 底部播放器。
 *
 * 本组件是全局状态中枢，子组件都是「受控组件」：只接收 props、通过 emit
 * 把用户操作交回这里处理，不自己发请求。
 *
 * 数据流：services/api.ts（axios + 离线降级）-> history / pending 两个
 * 响应式数组 -> 三栏组件；每 POLL_INTERVAL 轮询 /api/history 刷新生成状态。
 *
 * 播放：页面内唯一的 <audio ref="audioRef"> 由本组件持有，子组件只发
 * playPause / seek / prev / next / volume 等事件，避免多处同时操作 audio。
 * 播放器显隐与音量持久化在 localStorage（见两个 STORAGE_KEY）。
 *
 * 关键函数：handleCreate（四步简单创作流程）、switchTrack（切歌）、
 * handleDelete / handleDownload / showLibrary。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import { MusicIcon, ViewListIcon } from "tdesign-icons-vue-next";

import { createGeneration, deleteGeneration, detectBackend, listHistory, runLLMTask } from "@/services/api";
import CreatePanel from "@/components/CreatePanel.vue";
import LibraryPanel from "@/components/LibraryPanel.vue";
import SongDetailPanel from "@/components/SongDetailPanel.vue";
import PlaybackBar from "@/components/PlaybackBar.vue";
import MiniPlayer from "@/components/MiniPlayer.vue";
import type { CreateFormValues, GenerationResponse, PendingGeneration } from "@/interface";
import { cleanTitle, truncateText } from "@/common/utils";
import { DEFAULT_SEED_PROMPT, LYRICS_INSTRUCTION, STAGE_LABELS } from "@/common/constants";
import { config } from "@/common/config";

type BackendState = "checking" | "online" | "demo";

const PLAYER_STORAGE_KEY = "ace-studio-player-visible";
const VOLUME_STORAGE_KEY = "ace-studio-volume";

function readStoredBoolean(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : raw === "true";
  } catch {
    return fallback;
  }
}
function readStoredVolume(): number {
  try {
    const raw = Number(localStorage.getItem(VOLUME_STORAGE_KEY));
    return Number.isFinite(raw) && raw >= 0 && raw <= 1 ? raw : 0.8;
  } catch {
    return 0.8;
  }
}

/**
 * 全局状态：backend 为后端连接状态；history 是作品库；pending 是「生成中」
 * 的本地占位卡片；selectedId / playingId 分别是当前选中与正在播放的作品。
 * audioRef 是页面唯一的 <audio> 元素，progress 由它的 timeupdate 事件驱动。
 */
const backend = ref<BackendState>("checking");
const history = ref<GenerationResponse[]>([]);
const pending = ref<PendingGeneration[]>([]);
const selectedId = ref<string>();
const playingId = ref<string | null>(null);
const isPlaying = ref(false);
const progress = ref({ current: 0, duration: 0 });
const flowRunning = ref(false);
const audioRef = ref<HTMLAudioElement | null>(null);
const playerVisible = ref(readStoredBoolean(PLAYER_STORAGE_KEY, true));
const volume = ref(readStoredVolume());

let autoSelected = false;
let autoPlayNext = false;
let pollTimer: number | undefined;

const backendLabel = computed(() =>
  backend.value === "online"
    ? "后端已连接"
    : backend.value === "demo"
      ? "演示模式 · 后端未连接"
      : "连接中…",
);
const selectedSong = computed(() => history.value.find((item) => item.id === selectedId.value));
const selectedAudioUrl = computed(() => selectedSong.value?.audio_url || null);
const currentIndex = computed(() => history.value.findIndex((item) => item.id === selectedId.value));
const hasPrev = computed(() => currentIndex.value > 0);
const hasNext = computed(() => currentIndex.value >= 0 && currentIndex.value < history.value.length - 1);

async function refresh() {
  try {
    history.value = await listHistory();
  } catch (error) {
    console.error("读取作品列表失败", error);
  }
}

/**
 * 原生 audio 事件 -> 播放状态的映射；集中成一张表，便于 onMounted 注册、
 * onUnmounted 成对解绑，避免监听器泄漏。
 */
const audioHandlers: Record<string, () => void> = {
  ended: () => {
    isPlaying.value = false;
    playingId.value = null;
  },
  pause: () => {
    isPlaying.value = false;
  },
  play: () => {
    isPlaying.value = true;
  },
  timeupdate: () => {
    const audio = audioRef.value;
    if (audio) progress.value = { current: audio.currentTime, duration: audio.duration || progress.value.duration };
  },
  loadedmetadata: () => {
    const audio = audioRef.value;
    if (audio) progress.value = { current: 0, duration: audio.duration || 0 };
  },
};

onMounted(() => {
  void (async () => {
    backend.value = (await detectBackend()) ? "demo" : "online";
    await refresh();
  })();
  pollTimer = window.setInterval(() => void refresh(), config.POLL_INTERVAL);
  const audio = audioRef.value;
  if (audio) {
    audio.volume = volume.value;
    Object.entries(audioHandlers).forEach(([event, handler]) => audio.addEventListener(event, handler));
  }
});

onUnmounted(() => {
  window.clearInterval(pollTimer);
  const audio = audioRef.value;
  if (audio) {
    Object.entries(audioHandlers).forEach(([event, handler]) => audio.removeEventListener(event, handler));
  }
});

// 首次加载默认选中一首可播放的作品（优先），让用户能直接看到播放器效果
watch(history, () => {
  if (autoSelected || !history.value.length || selectedId.value) return;
  autoSelected = true;
  const playable = history.value.find((item) => item.audio_url);
  selectedId.value = (playable || history.value[0]).id;
});

watch(selectedAudioUrl, (url) => {
  const audio = audioRef.value;
  if (!audio) return;
  if (url) {
    audio.src = url;
    audio.load();
    if (autoPlayNext) {
      autoPlayNext = false;
      void audio.play().catch(() => undefined);
    }
  } else {
    audio.removeAttribute("src");
  }
  isPlaying.value = false;
});

watch(volume, (value) => {
  if (audioRef.value) audioRef.value.volume = value;
  try {
    localStorage.setItem(VOLUME_STORAGE_KEY, String(value));
  } catch {
    /* 隐私模式下忽略 */
  }
});

watch(playerVisible, (value) => {
  try {
    localStorage.setItem(PLAYER_STORAGE_KEY, String(value));
  } catch {
    /* 隐私模式下忽略 */
  }
});

function selectSong(id: string) {
  selectedId.value = id;
}

function handlePlayPause() {
  const audio = audioRef.value;
  if (!audio || !selectedAudioUrl.value) return;
  if (audio.paused) void audio.play().catch(() => undefined);
  else audio.pause();
}

function handlePlay(song: GenerationResponse) {
  if (!song.audio_url) {
    selectedId.value = song.id;
    return;
  }
  playingId.value = song.id;
  if (selectedId.value === song.id) void audioRef.value?.play().catch(() => undefined);
  else {
    autoPlayNext = true;
    selectedId.value = song.id;
  }
}

function handleCardTogglePlay(song: GenerationResponse) {
  if (playingId.value === song.id) handlePlayPause();
  else handlePlay(song);
}

/** 上一首 / 下一首：沿作品库顺序切换并自动播放 */
function switchTrack(step: -1 | 1) {
  const nextIndex = currentIndex.value + step;
  const next = history.value[nextIndex];
  if (!next) return;
  playingId.value = next.id;
  if (next.audio_url) {
    autoPlayNext = true;
    selectedId.value = next.id;
  } else {
    selectedId.value = next.id;
  }
}

function showLibrary() {
  const id = selectedId.value || history.value[0]?.id;
  if (!id) {
    MessagePlugin.info("作品库还没有歌曲");
    return;
  }
  const target = document.getElementById(`song-${id}`);
  if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
  else MessagePlugin.info("作品库就在中间栏");
}

async function handleDownload(song: GenerationResponse) {
  if (!song.audio_url) return;
  const response = await fetch(song.audio_url);
  if (!response.ok) return;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${cleanTitle(song.title || "作品").slice(0, 60)}.wav`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function handleDelete(id: string) {
  if (pending.value.some((item) => item.tempId === id)) {
    pending.value = pending.value.filter((item) => item.tempId !== id);
    return;
  }
  await deleteGeneration(id);
  if (selectedId.value === id) selectedId.value = undefined;
  if (playingId.value === id) {
    playingId.value = null;
    isPlaying.value = false;
  }
  await refresh();
  MessagePlugin.success("作品已删除");
}

function handleSeek(value: number) {
  const audio = audioRef.value;
  if (!audio) return;
  audio.currentTime = value;
  progress.value = { ...progress.value, current: value };
}

/**
 * 「简单创作」四步流程：AI 扩写描述 -> 写词（纯音乐跳过）-> 起标题
 * -> POST /api/generations 提交后台任务。
 *
 * 先插入一条 pending 占位卡片并逐步推进 stageIndex，给用户即时反馈；
 * 提交成功后移除占位、选中真实记录并刷新列表。任一步失败只把该占位
 * 卡片标记为错误，不影响作品库里已有的数据。
 */
async function handleCreate(values: CreateFormValues) {
  const tempId = `temp-${Date.now()}`;
  const stageLabels: string[] = values.instrumental
    ? [...STAGE_LABELS.instrumental]
    : [...STAGE_LABELS.vocal];

  pending.value = [
    ...pending.value,
    { tempId, title: truncateText(values.prompt, 24), prompt: values.prompt, stageLabels, stageIndex: 0 },
  ];
  flowRunning.value = true;

  const patch = (data: Partial<PendingGeneration>) => {
    pending.value = pending.value.map((item) => (item.tempId === tempId ? { ...item, ...data } : item));
  };
  const advance = () => {
    pending.value = pending.value.map((item) =>
      item.tempId === tempId
        ? { ...item, stageIndex: Math.min(item.stageLabels.length - 1, item.stageIndex + 1) }
        : item,
    );
  };

  try {
    const seed = values.prompt.trim() || DEFAULT_SEED_PROMPT;
    const expanded = await runLLMTask({
      task: "prompt",
      seed_prompt: seed,
      instrumental: values.instrumental,
      style_tags: [],
    });
    const prompt = expanded.output;
    patch({ prompt });
    advance();

    let lyrics = "[Instrumental]";
    if (!values.instrumental) {
      const result = await runLLMTask({
        task: "lyrics",
        seed_prompt: `${prompt}\n${LYRICS_INSTRUCTION}`,
        instrumental: false,
        style_tags: [],
      });
      lyrics = result.output;
      advance();
    }

    const titled = await runLLMTask({
      task: "title",
      seed_prompt: `为以下歌曲概念起一个简短的中文标题（不超过 6 个字）：${prompt}`,
      instrumental: values.instrumental,
      style_tags: [],
    });
    const title = cleanTitle(titled.output);
    patch({ title: truncateText(title, 24) });
    advance();

    const created = await createGeneration({
      title,
      task_type: "text2music",
      mode: "simple",
      model_variant: "turbo",
      inputs: { prompt, lyrics, instrumental: values.instrumental },
    });
    advance();
    pending.value = pending.value.filter((item) => item.tempId !== tempId);
    selectedId.value = created.id;
    await refresh();
    MessagePlugin.success("生成任务已提交");
  } catch (error) {
    console.error("生成流程失败", error);
    patch({ error: "生成失败，请检查后端服务或模型配置" });
  } finally {
    flowRunning.value = false;
  }
}

/** 左栏「AI 扩写描述」 */
async function handleExpand(prompt: string, instrumental: boolean): Promise<string> {
  const result = await runLLMTask({
    task: "prompt",
    seed_prompt: prompt,
    instrumental,
    style_tags: [],
  });
  return result.output;
}
</script>

<template>
  <div class="h-screen flex flex-col bg-canvas text-ink font-sans">
    <!-- 网易云风格红色顶栏 -->
    <header class="h-16 flex-shrink-0 flex items-center justify-between px-6 shadow-panel z-30 bg-brand-700">
      <div class="flex items-center gap-3">
        <div class="h-10 w-10 rounded-full bg-white/15 flex items-center justify-center border border-white/25">
          <MusicIcon size="22px" class="text-white" />
        </div>
        <div>
          <h1 class="text-base font-semibold leading-5 text-white">Music Studio · 音乐创作台</h1>
          <p class="text-xs text-white/70 mt-0.5">本地优先的 AI 音乐创作工作台 · 数据留在本机</p>
        </div>
        <span class="netease-status ml-1">{{ backendLabel }}</span>
      </div>
      <button
        class="h-8 px-3 rounded-full text-xs flex items-center gap-1.5 text-white bg-white/15 hover:bg-white/25 border border-white/20 transition-colors"
        :title="playerVisible ? '隐藏底部播放器' : '显示底部播放器'"
        @click="playerVisible = !playerVisible"
      >
        <ViewListIcon size="15px" />
        {{ playerVisible ? "隐藏播放器" : "显示播放器" }}
      </button>
    </header>

    <main class="flex flex-1 overflow-hidden">
      <CreatePanel :is-submitting="flowRunning" :expand-prompt="handleExpand" @submit="handleCreate" />
      <LibraryPanel
        :items="history"
        :pending-items="pending"
        :selected-id="selectedId"
        :currently-playing-id="playingId ?? undefined"
        :is-playing="isPlaying"
        @select="selectSong"
        @delete="handleDelete"
        @toggle-play="handleCardTogglePlay"
        @download="handleDownload"
      />
      <SongDetailPanel
        v-if="selectedSong"
        :song="selectedSong"
        @close="selectedId = undefined"
        @delete="handleDelete(selectedSong.id)"
        @download="handleDownload(selectedSong)"
      />
    </main>

    <PlaybackBar
      v-if="playerVisible"
      :song="selectedSong"
      :is-playing="isPlaying"
      :progress="progress"
      :volume="volume"
      :has-prev="hasPrev"
      :has-next="hasNext"
      @play-pause="handlePlayPause"
      @seek="handleSeek"
      @download="selectedSong && handleDownload(selectedSong)"
      @delete="selectedSong && handleDelete(selectedSong.id)"
      @hide="playerVisible = false"
      @prev="switchTrack(-1)"
      @next="switchTrack(1)"
      @volume="volume = $event"
      @show-library="showLibrary"
    />
    <MiniPlayer
      v-else
      :song="selectedSong"
      :is-playing="isPlaying"
      @play-pause="handlePlayPause"
      @show="playerVisible = true"
    />
    <audio ref="audioRef" class="hidden" />
  </div>
</template>
