<script setup lang="ts">
/**
 * 主工作台页面：三栏布局（简单创作 / 作品库 / 歌曲详情）+ 底部播放器。
 *
 * 本组件是全局状态中枢，子组件都是「受控组件」：只接收 props、通过 emit
 * 把用户操作交回这里处理，不自己发请求。
 *
 * 数据流：services/api.ts（axios 直连真实后端）-> history / pending 两个
 * 响应式数组 -> 三栏组件；每 POLL_INTERVAL 轮询 /v1/history 刷新生成状态。
 *
 * 播放：页面内唯一的 <audio ref="audioRef"> 由本组件持有，子组件只发
 * playPause / seek / prev / next / volume 等事件，避免多处同时操作 audio。
 * 播放器显隐与音量持久化在 localStorage（见两个 STORAGE_KEY）。
 *
 * 关键函数：handleCreate（提交生成任务）、switchTrack（切歌）、
 * handleCancelPending / handleDownload / showLibrary。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { MessagePlugin } from "tdesign-vue-next";
import { MusicIcon, ViewListIcon } from "tdesign-icons-vue-next";

import { createGeneration, fetchHealth, listHistory, resolveAudioUrl, runLLMTask } from "@/services/api";
import { isConnectionError } from "@/services";
import CreatePanel from "@/components/CreatePanel.vue";
import LibraryPanel from "@/components/LibraryPanel.vue";
import SongDetailPanel from "@/components/SongDetailPanel.vue";
import PlaybackBar from "@/components/PlaybackBar.vue";
import MiniPlayer from "@/components/MiniPlayer.vue";
import type { CreateFormValues, GenerationResponse, PendingGeneration } from "@/interface";
import { cleanTitle, truncateText } from "@/common/utils";
import { buildLyricTimeline, findActiveLyricIndex, pickLrc, type LyricItem } from "@/common/lyric";
import { config } from "@/common/config";

type BackendState = "checking" | "online" | "offline";

/** 今日配额快照（来自 /v1/health）；remaining 为 null 表示后端不限额 */
type QuotaState = { limit: number; used: number; remaining: number | null; resetAt: string | null };

const PLAYER_STORAGE_KEY = "ace-studio-player-visible";
const VOLUME_STORAGE_KEY = "ace-studio-volume";
const LYRIC_SHIFT_PREFIX = "ace-studio-lyric-shift:";
/**
 * 手动校准的上下限（秒）。估算时间轴可能整体偏一两句（十几秒），
 * 所以放到 ±20 秒；再大就说明歌词与音频根本不匹配，靠偏移也救不回来。
 */
const LYRIC_SHIFT_LIMIT = 20;
/** 校准按钮的单步时长（秒）：够细，连点几次也不难受 */
const LYRIC_SHIFT_STEP = 0.3;

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
/** 读取某首歌的歌词校准值（秒）；0 表示不校准 */
function readLyricShift(songId?: string): number {
  if (!songId) return 0;
  try {
    const raw = Number(localStorage.getItem(`${LYRIC_SHIFT_PREFIX}${songId}`));
    return Number.isFinite(raw) ? Math.min(LYRIC_SHIFT_LIMIT, Math.max(-LYRIC_SHIFT_LIMIT, raw)) : 0;
  } catch {
    return 0;
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
/** 歌词时间轴的手动校准量（秒）：>0 表示歌词提前亮，<0 表示延后 */
const lyricShift = ref(0);
/** 「对齐当前句」模式：开启后点歌词不再跳转，而是把该句对齐到当前播放位置 */
const lyricAligning = ref(false);
/** 今日生成配额（后端 MUSIC_STUDIO_DAILY_LIMIT）；null 表示还没取到 */
const quota = ref<QuotaState | null>(null);

let autoSelected = false;
let autoPlayNext = false;
let pollTimer: number | undefined;
let progressRaf: number | undefined;
let lastProgressPaint = 0;

const backendLabel = computed(() =>
  backend.value === "online"
    ? "后端已连接"
    : backend.value === "offline"
      ? "后端未连接"
      : "连接中…",
);
const selectedSong = computed(() => history.value.find((item) => item.id === selectedId.value));
/**
 * 真正在播放的曲目：由 playingId 驱动，**不是** selectedId。
 * 这样「点卡片只是浏览」就不会触碰 audio，正在放的歌不会被误切/误停
 * （详见 selectSong 的说明）。
 */
const playingSong = computed(() => history.value.find((item) => item.id === playingId.value));
// 后端返回 /v1/... 相对路径；直连后端时 resolveAudioUrl 会补上域名
const playingAudioUrl = computed(() =>
  playingSong.value?.audio_url ? resolveAudioUrl(playingSong.value.audio_url) : null,
);
/**
 * 播放条展示的曲目：优先「正在播放」的；未播放时回落到「当前选中」的，
 * 这样只浏览不播放时，播放条仍显示你正在看的歌，点一下即可起播。
 */
const nowPlaying = computed(() => playingSong.value ?? selectedSong.value);
const currentIndex = computed(() => history.value.findIndex((item) => item.id === selectedId.value));
const hasPrev = computed(() => currentIndex.value > 0);
const hasNext = computed(() => currentIndex.value >= 0 && currentIndex.value < history.value.length - 1);

/**
 * 歌词时间轴：优先用 metadata 里的 LRC（后端若补上对齐结果可直接生效），
 * 否则由 common/lyric.ts 按字重把音频时长分配给每一句（估算）。
 * 时长优先取 audio 的真实 duration（元数据加载后才有），退回后端记录的
 * duration_seconds；两者都没有时 lyric.ts 内部按 180s 兜底。
 */
const lyricItems = computed<LyricItem[]>(() => {
  const song = selectedSong.value;
  if (!song) return [];
  const duration = progress.value.duration || song.duration_seconds || 0;
  return buildLyricTimeline(song.lyrics, duration, pickLrc(song.metadata));
});
/** 时间轴是否为估算（用于在 UI 上提示「可微调」） */
const lyricEstimated = computed(() => lyricItems.value.some((item) => !item.exact));
/** 当前正在唱的那一行下标；-1 表示还没开始或已唱完 */
const activeLyricIndex = computed(() =>
  findActiveLyricIndex(lyricItems.value, progress.value.current + lyricShift.value),
);
/** 当前行文本，播放条上滚动显示 */
const activeLyricText = computed(() => {
  const item = lyricItems.value[activeLyricIndex.value];
  return item && item.kind === "text" ? item.text : "";
});

async function refresh() {
  try {
    history.value = await listHistory();
    backend.value = "online";
  } catch (error) {
    // 连接类错误（后端未启动 / 网络问题）只更新状态栏；业务错误不打扰用户
    if (isConnectionError(error)) backend.value = "offline";
    console.error("读取作品列表失败", error);
  }
}

/**
 * 刷新今日配额：后端每天最多 50 首（MUSIC_STUDIO_DAILY_LIMIT，可配置）。
 * 拉取失败时保持上一次的值，不弹错误——配额只是辅助提示，
 * 真正被拒绝时后端会返回 429 + 中文原因（见 handleCreate）。
 */
async function refreshQuota() {
  try {
    const health = await fetchHealth();
    quota.value = {
      limit: health.daily_limit ?? 0,
      used: health.daily_used ?? 0,
      remaining: typeof health.daily_remaining === "number" ? health.daily_remaining : null,
      resetAt: health.daily_reset_at ?? null,
    };
  } catch (error) {
    console.warn("读取每日配额失败", error);
  }
}

/**
 * 播放进度同步：浏览器 timeupdate 只有 ~4Hz，直接驱动歌词会在换行时明显滞后，
 * 所以播放期间用 requestAnimationFrame 读 currentTime，并限流到约 20fps
 * （60ms 一帧）避免每帧都触发整棵组件树重渲染。暂停时立刻停下 rAF。
 */
function paintProgress() {
  const audio = audioRef.value;
  if (!audio) return;
  progress.value = {
    current: audio.currentTime,
    duration: audio.duration || progress.value.duration,
  };
}
function progressLoop(timestamp: number) {
  const audio = audioRef.value;
  if (!audio || audio.paused) {
    progressRaf = undefined;
    return;
  }
  if (timestamp - lastProgressPaint >= 60) {
    lastProgressPaint = timestamp;
    paintProgress();
  }
  progressRaf = requestAnimationFrame(progressLoop);
}
function startProgressLoop() {
  if (progressRaf === undefined) progressRaf = requestAnimationFrame(progressLoop);
}
function stopProgressLoop() {
  if (progressRaf !== undefined) cancelAnimationFrame(progressRaf);
  progressRaf = undefined;
}

/**
 * 原生 audio 事件 -> 播放状态的映射；集中成一张表，便于 onMounted 注册、
 * onUnmounted 成对解绑，避免监听器泄漏。
 */
const audioHandlers: Record<string, () => void> = {
  ended: () => {
    isPlaying.value = false;
    stopProgressLoop();
    // 不把 playingId 置空：保留「当前曲目」让播放条继续显示它，便于重播；
    // 仅把进度归零，下次点播放从开头来。
    const audio = audioRef.value;
    if (audio) audio.currentTime = 0;
  },
  pause: () => {
    isPlaying.value = false;
    stopProgressLoop();
    paintProgress();
  },
  play: () => {
    isPlaying.value = true;
    startProgressLoop();
  },
  timeupdate: () => paintProgress(),
  loadedmetadata: () => {
    const audio = audioRef.value;
    if (audio) progress.value = { current: 0, duration: audio.duration || 0 };
  },
};

onMounted(() => {
  void (async () => {
    await Promise.all([refresh(), refreshQuota()]);
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
  stopProgressLoop();
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

watch(playingAudioUrl, (url) => {
  const audio = audioRef.value;
  if (!audio) return;
  stopProgressLoop();
  progress.value = { current: 0, duration: 0 };
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

/**
 * 暂停播放并清空「正在播放」标记。
 * 只切换选中作品时必须调用它：浏览作品库是很随意的操作，误点一下就把
 * 正在听的歌掐掉、或直接开始放另一首，体验会很糟。
 */
/**
 * 选中作品（点卡片本体、点标题）。
 *
 * **只改 selectedId，不碰 audio**：浏览作品库是随手动作，误点一下就把正在听的
 * 歌切掉 / 掐掉会很糟。音频由 playingId 驱动（见 playingAudioUrl 的 watch），
 * 这里绝不改它——所以「正在放 B，随手点了 A」的结果是：右侧详情变成 A，但
 * 耳朵里继续放 B，要听 A 得点 A 的播放按钮。
 */
function selectSong(id: string) {
  selectedId.value = id;
}

function handlePlayPause() {
  const audio = audioRef.value;
  if (!audio) return;
  // 正在播放某首：直接续播 / 暂停当前曲目（按钮上的暂停就是暂停这首）
  if (playingId.value && playingAudioUrl.value) {
    if (audio.paused) void audio.play().catch(() => undefined);
    else audio.pause();
    return;
  }
  // 没在播放：若当前选中的歌有音频，就直接播它（播放条上的 ▶ 也能起播）
  if (selectedSong.value?.audio_url) handlePlay(selectedSong.value);
}

function handlePlay(song: GenerationResponse) {
  if (!song.audio_url) {
    selectedId.value = song.id;
    return;
  }
  // playingId 一变就会触发 playingAudioUrl 的 watch：重新加载音频并自动起播
  // （autoPlayNext）。若当前选中的恰好是这首，也会顺手把它变成选中态。
  playingId.value = song.id;
  autoPlayNext = true;
  selectedId.value = song.id;
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
  // 相邻是生成中 / 失败这类没音频的歌：只切详情，不碰正在放的歌
  selectedId.value = next.id;
  if (next.audio_url) {
    playingId.value = next.id;
    autoPlayNext = true;
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
  const response = await fetch(resolveAudioUrl(song.audio_url));
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

function handleCancelPending(tempId: string) {
  // 仅移除本地「生成中」占位卡片；已提交给后端的任务仍会继续，
  // 完成后会出现在作品库里（后端负责定时清理，前端不提供删除入口）。
  pending.value = pending.value.filter((item) => item.tempId !== tempId);
}

function handleSeek(value: number) {
  const audio = audioRef.value;
  if (!audio) return;
  audio.currentTime = value;
  progress.value = { ...progress.value, current: value };
}

/** 切歌时读取该曲目保存过的歌词校准值，避免上一首的偏移带到下一首 */
watch(selectedId, (id) => {
  lyricShift.value = readLyricShift(id);
  lyricAligning.value = false;
});

/**
 * 点击歌词跳转：时间轴上的 start 是「歌词应该亮起的音频位置」，
 * 而高亮判定用的是 currentTime + shift，所以这里要减去 shift 才能落在同一行。
 */
function handleLyricSeek(time: number) {
  handleSeek(Math.max(0, time - lyricShift.value));
}

/** 写入校准值：钳制到 ±LYRIC_SHIFT_LIMIT，并按作品持久化（0 时清掉键） */
function setLyricShift(value: number) {
  const next = Number(Math.min(LYRIC_SHIFT_LIMIT, Math.max(-LYRIC_SHIFT_LIMIT, value)).toFixed(1));
  lyricShift.value = next;
  const id = selectedSong.value?.id;
  if (!id) return;
  try {
    if (next === 0) localStorage.removeItem(`${LYRIC_SHIFT_PREFIX}${id}`);
    else localStorage.setItem(`${LYRIC_SHIFT_PREFIX}${id}`, String(next));
  } catch {
    /* 隐私模式下忽略 */
  }
}

/** 歌词校准微调（±0.3 秒），范围 ±20 秒 */
function adjustLyricShift(delta: number) {
  setLyricShift(lyricShift.value + delta);
}

/** 进入 / 退出「对齐当前句」模式 */
function toggleLyricAlign() {
  lyricAligning.value = !lyricAligning.value;
  if (lyricAligning.value && (audioRef.value?.paused ?? true)) {
    MessagePlugin.info("先点播放，唱到哪一句时点那句话，就能把它对齐到当前进度");
  }
}

/**
 * 「对齐当前句」：把用户点中的那一句对齐到此刻的播放位置。
 *
 * 高亮判定用 ``currentTime + shift``，要让第 N 行**此刻**亮起，需要
 * ``items[N].start + shift'`` 落在当前时间上 ⇒ ``shift' = start - currentTime``。
 * 相比「提前/延后」一点点挪，这是最快的一次性对齐方式：
 * 听到唱到某句就点它，偏差立刻收敛到亚秒级。
 */
function handleLyricAlign(index: number) {
  const audio = audioRef.value;
  const item = lyricItems.value[index];
  if (!audio || !item) return;
  const next = item.start - audio.currentTime;
  setLyricShift(next);
  lyricAligning.value = false;
  MessagePlugin.success(
    `已对齐到「${truncateText(item.text, 12)}」（偏移 ${next > 0 ? "+" : ""}${next.toFixed(1)}s）`,
  );
}

/**
 * 从 axios 错误里取出后端返回的中文原因（429 限流 / 413 请求体过大 /
 * 422 参数校验）。这些是服务端主动拒绝，detail 里写了具体原因与重试建议，
 * 直接展示比笼统的「检查后端服务」更准确；其它错误仍走默认兜底文案。
 */
function serverErrorMessage(error: unknown): string | null {
  const response = (error as { response?: { status?: number; data?: { detail?: string } } } | null)?.response;
  if (response?.status && [413, 422, 429].includes(response.status)) {
    return response.data?.detail ?? null;
  }
  return null;
}

/**
 * 「开始生成」：把用户原始描述直接提交给 POST /v1/generations，由后端
 * WorkflowService 统一编排五阶段（策划 → 写词 → 润色 → 制作方案 → 音频）。
 *
 * 历史事件：早期版本会在这里先串行调 3 次 POST /v1/llm（扩写描述、写词、
 * 起标题）再提交生成。这三次与后端流程重复——后端润色阶段还会覆盖
 * title / lyrics，且把扩写后的文本当成原始输入再分析一遍；同时白耗 3 次
 * LLM 配额（容易撞 20 次/60s 限流）。现已移除，/v1/llm 只保留给「AI 扩写
 * 描述」这类用户主动点击的辅助功能（见 handleExpand）。
 *
 * 提交在途期间插入一条 pending 占位卡片；成功后移除、选中真实记录并刷新，
 * 之后进度完全由后端 status / current_stage 驱动。失败只把占位卡标为错误。
 */
async function handleCreate(values: CreateFormValues) {
  const prompt = values.prompt.trim();
  // 左栏按钮已禁用空输入，这里再挡一次程序化调用（例如绕过 UI 的事件触发）
  if (!prompt) {
    MessagePlugin.warning("请先输入歌词或歌曲描述");
    return;
  }

  const tempId = `temp-${Date.now()}`;
  pending.value = [
    ...pending.value,
    {
      tempId,
      title: truncateText(prompt, 24),
      prompt,
      stageLabels: ["提交生成任务"],
      stageIndex: 0,
    },
  ];
  flowRunning.value = true;

  try {
    // model_variant / title / lyrics 都不传：模型由后端 MUSIC_MODEL 决定，
    // 标题与歌词由后端润色阶段产出，避免前端硬编码与后端配置不一致。
    const created = await createGeneration({
      task_type: "text2music",
      mode: "simple",
      inputs: { prompt, instrumental: values.instrumental },
    });
    pending.value = pending.value.filter((item) => item.tempId !== tempId);
    selectedId.value = created.id;
    await Promise.all([refresh(), refreshQuota()]);
    MessagePlugin.success("生成任务已提交");
  } catch (error) {
    console.error("生成流程失败", error);
    // 429 有两种：今日额度用完（明天恢复）与并发任务已满（稍后再试）。
    // 两种都把后端给的中文说明原样展示，并刷新配额让按钮与提示同步更新。
    const status = (error as { response?: { status?: number } } | null)?.response?.status;
    const reason = serverErrorMessage(error) ?? "生成失败，请检查后端服务或模型配置";
    if (status === 429) void MessagePlugin.error({ content: reason, duration: 6000 });
    void refreshQuota();
    pending.value = pending.value.map((item) => (item.tempId === tempId ? { ...item, error: reason } : item));
  } finally {
    flowRunning.value = false;
  }
}

/**
 * 「AI 扩写描述」辅助功能：用户主动点击时才调用 POST /v1/llm。
 * 左栏模板当前未挂载该按钮（见 CreatePanel.vue），函数保留供恢复时使用，
 * 不参与「开始生成」的主链路。
 */
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
      <CreatePanel
        :is-submitting="flowRunning"
        :expand-prompt="handleExpand"
        :quota="quota"
        @submit="handleCreate"
      />
      <LibraryPanel
        :items="history"
        :pending-items="pending"
        :selected-id="selectedId"
        :currently-playing-id="playingId ?? undefined"
        :is-playing="isPlaying"
        :loading="backend === 'checking'"
        @select="selectSong"
        @cancel="handleCancelPending"
        @toggle-play="handleCardTogglePlay"
        @download="handleDownload"
      />
      <SongDetailPanel
        v-if="selectedSong"
        :song="selectedSong"
        :lyric-items="lyricItems"
        :active-lyric-index="activeLyricIndex"
        :lyric-estimated="lyricEstimated"
        :lyric-shift="lyricShift"
        :lyric-aligning="lyricAligning"
        @close="selectedId = undefined"
        @download="handleDownload(selectedSong)"
        @seek-lyric="handleLyricSeek"
        @shift-lyric="adjustLyricShift"
        @toggle-align="toggleLyricAlign"
        @align-lyric="handleLyricAlign"
      />
    </main>

    <PlaybackBar
      v-if="playerVisible"
      :song="nowPlaying"
      :is-playing="isPlaying"
      :progress="progress"
      :volume="volume"
      :has-prev="hasPrev"
      :has-next="hasNext"
      :active-lyric="activeLyricText"
      @play-pause="handlePlayPause"
      @seek="handleSeek"
      @download="selectedSong && handleDownload(selectedSong)"
      @hide="playerVisible = false"
      @prev="switchTrack(-1)"
      @next="switchTrack(1)"
      @volume="volume = $event"
      @show-library="showLibrary"
    />
    <MiniPlayer
      v-else
      :song="nowPlaying"
      :is-playing="isPlaying"
      @play-pause="handlePlayPause"
      @show="playerVisible = true"
    />
    <audio ref="audioRef" class="hidden" />
  </div>
</template>
