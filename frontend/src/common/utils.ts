/**
 * 中文文案、时间/文本格式化、封面占位配色与图标（纯展示逻辑，无请求、无状态）。
 *
 * statusText / statusTheme 覆盖后端四种状态及其历史别名（pending / running /
 * completed / error），新增状态值时记得同时补这两个映射。
 * paletteFor 用 id 做确定性哈希选色，与后端 cover_theme.compute_theme 同理，
 * 保证同一首歌每次刷新看到的封面一致。
 */
import type { Component } from "vue";
import {
  AiMusicIcon,
  ChartIcon,
  CloudIcon,
  GuitarIcon,
  HeartIcon,
  Music2Icon,
  MusicIcon,
  SoundIcon,
} from "tdesign-icons-vue-next";

import type { GenerationResponse } from "@/interface";

/** 状态中文文案；key 同时包含后端四种标准值与历史别名，保证旧数据不显示英文原值 */
export const statusText: Record<string, string> = {
  queued: "排队中",
  pending: "排队中",
  processing: "生成中",
  running: "生成中",
  ready: "已完成",
  completed: "已完成",
  failed: "生成失败",
  error: "生成失败",
};

export function statusTheme(status?: string): "success" | "danger" | "warning" | "primary" | "default" {
  if (status === "ready" || status === "completed") return "success";
  if (status === "failed" || status === "error") return "danger";
  if (status === "processing" || status === "running") return "primary";
  if (status === "queued" || status === "pending") return "warning";
  return "default";
}

export const taskTypeText: Record<string, string> = {
  text2music: "文生音乐",
  cover: "翻唱",
  repaint: "局部重绘",
};

export const variantText: Record<string, string> = {
  base: "基础版",
  turbo: "极速版",
  shift: "变奏版",
};

export const variantShortText: Record<string, string> = {
  base: "BASE",
  turbo: "TURBO",
  shift: "SHIFT",
};

export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${mins}:${secs}`;
}

export function formatRelative(iso?: string): string {
  if (!iso) return "刚刚";
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return "刚刚";
  const diff = Math.max(0, Date.now() - time);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

/** 保留中文、英文、数字，只清理标点与控制字符 */
export function cleanTitle(value?: string | null, fallback = "未命名作品"): string {
  if (!value) return fallback;
  const cleaned = value.replace(/[^\p{L}\p{N}\s_-]/gu, " ").replace(/\s+/g, " ").trim();
  return cleaned || fallback;
}

export function truncateText(value?: string | null, max = 48, fallback = "未命名作品"): string {
  if (!value) return fallback;
  const text = value.trim().replace(/\s+/g, " ");
  if (!text) return fallback;
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 卡片与详情页的「BPM · 调式 · 拍号 · 时长」摘要，缺字段时用「自动」兜底 */
export function formatMetaLine(
  item: Pick<GenerationResponse, "bpm" | "key" | "time_signature" | "duration_seconds">,
): string {
  const parts = [
    item.bpm ? `${item.bpm} BPM` : "BPM 自动",
    item.key || "调式自动",
    item.time_signature || "拍号自动",
  ];
  if (item.duration_seconds) parts.push(`${item.duration_seconds} 秒`);
  return parts.join(" · ");
}

const ICON_MAP: Record<string, Component> = {
  music: MusicIcon,
  "music-2": Music2Icon,
  guitar: GuitarIcon,
  sound: SoundIcon,
  "ai-music": AiMusicIcon,
  heart: HeartIcon,
  chart: ChartIcon,
  cloud: CloudIcon,
};

/** 封面渐变色板；按 id 哈希取色，改条目数量会影响所有旧作品的封面外观 */
const COVER_PALETTE: Array<{ from: string; to: string; icon: string }> = [
  { from: "#8b5cf6", to: "#4c1d95", icon: "music" },
  { from: "#f472b6", to: "#be185d", icon: "heart" },
  { from: "#38bdf8", to: "#1d4ed8", icon: "sound" },
  { from: "#fb923c", to: "#b45309", icon: "guitar" },
  { from: "#34d399", to: "#047857", icon: "chart" },
  { from: "#a78bfa", to: "#6d28d9", icon: "ai-music" },
  { from: "#60a5fa", to: "#3730a3", icon: "cloud" },
  { from: "#facc15", to: "#b45309", icon: "music-2" },
];

/** 返回可直接用于 <component :is="…"> 的 Vue 图标组件 */
export function getCoverIcon(name?: string): Component {
  return (name && ICON_MAP[name]) || MusicIcon;
}

export function paletteFor(id: string): { from: string; to: string; icon: string } {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return COVER_PALETTE[hash % COVER_PALETTE.length];
}

/** 封面渐变，用标准 CSS 属性避免自定义属性带来的类型问题 */
export function coverGradient(id: string): string {
  const palette = paletteFor(id);
  return `linear-gradient(135deg, ${palette.from}, ${palette.to})`;
}
