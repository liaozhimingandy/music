/** 离线演示数据：后端未启动时，界面与生成流程仍可完整预览 */
import type { GenerationCreate, GenerationResponse, LLMTaskResponse } from "@/interface";

const now = Date.now();
const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();

/**
 * 生成一段柔和的合成和弦 WAV（浏览器内 Blob URL），仅用于演示播放：
 * 让播放器的进度条、时间、唱片旋转有真实反馈，不依赖后端与网络。
 */
function createDemoAudioUrl(seed: number, seconds = 12): string | null {
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return null;
  const sampleRate = 11025;
  const frames = sampleRate * seconds;
  const buffer = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(buffer);
  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + frames * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, frames * 2, true);

  const base = [196, 220, 246.94, 261.63][seed % 4];
  const ratios = [1, 1.25, 1.5, 2];
  for (let i = 0; i < frames; i += 1) {
    const t = i / sampleRate;
    const fade = Math.min(1, t * 1.5, (seconds - t) * 1.5);
    const pulse = 0.85 + 0.15 * Math.sin(2 * Math.PI * 0.5 * t);
    let sample = 0;
    ratios.forEach((ratio, index) => {
      sample += Math.sin(2 * Math.PI * base * ratio * t) * (0.5 / (index + 1));
    });
    view.setInt16(44 + i * 2, Math.round(sample * pulse * fade * 0.16 * 32767), true);
  }
  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

const DEMO_TRACKS = [0, 1, 2, 3].map((seed) => createDemoAudioUrl(seed));

let songs: GenerationResponse[] = [
  {
    id: "demo-evening-wind",
    title: "晚风经过站台",
    task_type: "text2music",
    mode: "simple",
    model_variant: "turbo",
    status: "ready",
    prompt: "华语独立流行，温柔的女声，木吉他与弦乐铺底，讲述夏末站台告别的心情，副歌逐渐明亮。",
    lyrics:
      "[主歌]\n末班车的灯 亮在潮湿的窗\n你把沉默折好 放进我的行李箱\n\n[副歌]\n晚风经过站台 把名字轻轻吹散\n我们都没回头 怕看见对方红的眼眶\n\n[桥段]\n如果有一天 城市换了方向\n愿你还记得 那年的月光",
    instrumental: false,
    duration_seconds: 186,
    bpm: 92,
    key: "A minor",
    time_signature: "4/4",
    cover_color: "#8b5cf6",
    cover_icon: "music",
    cover_image_url: null,
    audio_url: DEMO_TRACKS[0],
    metadata: {},
    created_at: iso(12),
    updated_at: iso(12),
  },
  {
    id: "demo-misty-city",
    title: "山城雾雨",
    task_type: "text2music",
    mode: "simple",
    model_variant: "base",
    status: "ready",
    prompt: "纯音乐，城市氛围电子，缓慢的钢琴琶音叠加远处电车声与雨声，克制而略带孤独。",
    lyrics: "[Instrumental]",
    instrumental: true,
    duration_seconds: 212,
    bpm: 76,
    key: "D Major",
    time_signature: "4/4",
    cover_color: "#38bdf8",
    cover_icon: "cloud",
    cover_image_url: null,
    audio_url: DEMO_TRACKS[1],
    metadata: {},
    created_at: iso(48),
    updated_at: iso(48),
  },
  {
    id: "demo-neon",
    title: "霓虹练习曲",
    task_type: "text2music",
    mode: "simple",
    model_variant: "shift",
    status: "processing",
    prompt: "复古合成器浪潮，鼓机干脆，霓虹色调的夜奔画面，副歌加入萨克斯即兴。",
    lyrics: "",
    instrumental: false,
    duration_seconds: 150,
    bpm: 118,
    key: "F# minor",
    time_signature: "4/4",
    cover_color: "#f472b6",
    cover_icon: "sound",
    cover_image_url: null,
    audio_url: null,
    metadata: {},
    created_at: iso(3),
    updated_at: iso(3),
  },
  {
    id: "demo-summer",
    title: "失重的夏天",
    task_type: "text2music",
    mode: "simple",
    model_variant: "turbo",
    status: "failed",
    prompt: "轻快的 city pop，电吉他切分与铜管，描写海边公路与汽水味的午后。",
    lyrics: "[主歌]\n汽水冒泡 把阳光切成两半",
    instrumental: false,
    duration_seconds: 164,
    bpm: 112,
    key: "C Major",
    time_signature: "4/4",
    cover_color: "#fb923c",
    cover_icon: "guitar",
    cover_image_url: null,
    audio_url: null,
    error_message: "未检测到 ACE-Step 模型权重",
    metadata: {},
    created_at: iso(96),
    updated_at: iso(95),
  },
];

const LLM_SAMPLES: Record<string, string> = {
  prompt: "华语独立流行，温柔的女声与主歌木吉他，副歌加入弦乐与轻鼓点，整体情绪从克制走向释然，结尾留一段钢琴尾奏。",
  lyrics:
    "[主歌]\n路灯把影子 拉成一条旧街\n我把没说完的话 收进外套口袋\n\n[副歌]\n就让风替我 把这一页轻轻翻过\n明天的列车 会载着新的名字经过\n\n[桥段]\n别回头 也别急着遗忘",
  title: "晚风经过站台",
};

/** 模拟排队任务在几秒后完成 */
export function demoHistory(): GenerationResponse[] {
  const timestamp = Date.now();
  songs = songs.map((song) => {
    const queuedAt = Number((song.metadata as Record<string, unknown> | undefined)?._queuedAt || 0);
    if (song.status === "queued" && queuedAt && timestamp - queuedAt > 3500) {
      return {
        ...song,
        status: "ready",
        audio_url: "https://modelscope-studios.oss-cn-zhangjiakou.aliyuncs.com/aigc/text-to-image/6582ef1a-6e7c-4bec-a769-5fc0a160d43e.mp3",
        duration_seconds: 12,
        updated_at: new Date().toISOString(),
      };
    }
    return song;
  });
  return [...songs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

export function demoCreate(payload: GenerationCreate): GenerationResponse {
  const id = `demo-${Date.now()}`;
  const song: GenerationResponse = {
    id,
    title: payload.title || "未命名作品",
    task_type: payload.task_type,
    mode: payload.mode,
    model_variant: payload.model_variant,
    status: "queued",
    prompt: payload.inputs.prompt,
    lyrics: payload.inputs.lyrics,
    instrumental: payload.inputs.instrumental,
    duration_seconds: null,
    bpm: null,
    key: null,
    time_signature: null,
    cover_color: "#a78bfa",
    cover_icon: "ai-music",
    cover_image_url: null,
    audio_url: null,
    metadata: { _queuedAt: Date.now() },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  songs = [song, ...songs];
  return { ...song };
}

export function demoDelete(id: string): void {
  songs = songs.filter((song) => song.id !== id);
}

export function demoLLM(task: string): LLMTaskResponse {
  return {
    task,
    output: LLM_SAMPLES[task] || LLM_SAMPLES.prompt,
    provider: "ace",
    metadata: { mode: "demo" },
  };
}
