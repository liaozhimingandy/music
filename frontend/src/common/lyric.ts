/**
 * 歌词时间轴：把一段纯文本歌词（或 LRC）变成「每行从第几秒唱到第几秒」。
 *
 * 为什么需要它：后端五阶段只产出**纯文本**歌词（prompts/lyrics 与 polish 都
 * 不含时间戳），而音乐生成接口也不回传对齐信息。因此前端只能自己造时间轴：
 *
 * 1. 歌词里带 LRC 时间戳（`[00:12.34]`）时直接用真时间戳 —— 未来后端若补上
 *    对齐结果、或用户粘贴 LRC，这里零改动即可生效；`song.metadata.lrc` 同样支持。
 * 2. 否则按「字重」把总时长切成若干段：中文 1 个字记 1 份，英文单词按长度折算，
 *    标点略微加权（唱到句末会拖一点尾音）。这是**估算**，不是强制对齐。
 *    因此 UI 给了两级校准（见 StudioPage 的 lyricShift）：
 *    - 「提前 / 延后」±0.3s 微调整体偏移（范围 ±20s）；
 *    - 「对齐当前句」：播到某句时点那句歌词，一次性算出偏移，一次到位。
 *
 * 维护提示：本文件是纯函数、无 Vue 依赖，方便单测；改算法只需保证返回的
 * items 按 start 升序、且相邻行不重叠。前奏 / 尾奏的预留比例是「听起来偏早
 * 还是偏晚」的唯一旋钮（见下方 INTRO_RATIO / OUTRO_RATIO 的注释，改之前先读）。
 */

/** 时间轴上的一个元素：可高亮的歌词行，或段落标签（副歌 / 主歌 / 间奏…） */
export interface LyricItem {
  /** 渲染 key，形如 `text-3` / `section-1` */
  id: string;
  kind: "text" | "section";
  text: string;
  /** 开始 / 结束时间（秒，相对音频 0 点） */
  start: number;
  end: number;
  /** true = 时间来自真实时间戳；false = 按字重估算 */
  exact: boolean;
}

/** LRC 行首的一到多个 `[mm:ss.xx]` 时间戳（parseLrcTimes / stripLrcTags 共用） */
const LRC_TAG = /\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;
/** 段落标记：`[Verse 1]`、`【副歌】`、`(Chorus)`、`[间奏]` 等，不参与演唱 */
const SECTION_LINE =
  /^[\[【(（]\s*(?:verse|chorus|pre[- ]?chorus|post[- ]?chorus|bridge|hook|intro|outro|interlude|instrumental|refrain|rap|副歌|主歌|导歌|预副歌|桥段|间奏|前奏|尾奏|尾声|说唱|哼唱|纯音乐)[^\]】)）]*[\]】)）]\s*$/i;
/** 完全不唱的占位符（纯音乐作品会给 `[Instrumental]`） */
const INSTRUMENTAL_ONLY = /^\s*[\[【(（]?\s*(?:instrumental|纯音乐|无歌词)\s*[\]】)）]?\s*$/i;

/**
 * 估算用的前奏 / 尾奏预留。
 *
 * 这里踩过一次坑：早期按 8% / 6% 预留（226 秒的作品 = 10 秒前奏 + 8 秒尾奏），
 * 但 ACE-Step 的输出其实是「音乐立刻进」（实测 226 秒的作品 0.5 秒就有能量、
 * 人声几秒内就进），结果第一句晚了约 6 秒，用户感知就是「唱完这一句，
 * 歌词才亮这一句」。现在收紧到 1.5% / 1%（并限幅），同时把整首歌的
 * 可用时长还给歌词，误差压到半句以内。
 */
const INTRO_RATIO = 0.015;
const MIN_INTRO = 0.8;
const MAX_INTRO = 4;
const OUTRO_RATIO = 0.01;
const MIN_OUTRO = 0.5;
const MAX_OUTRO = 3;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * 一行的「字重」：决定这一行分到多少时长。
 * 中文/日文/韩文按字计 1，英文按词长折算（3 字母约等于 1 个音节），
 * 数字 0.6，标点 1.1（句末停顿 + 拖音），最后兜底 2 防止极短行一闪而过。
 */
export function lineWeight(text: string): number {
  const cjk = (text.match(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g) || []).length;
  const words = (text.match(/[A-Za-z]+/g) || []).reduce(
    (sum, word) => sum + Math.max(1, Math.ceil(word.length / 3)),
    0,
  );
  const digits = (text.match(/[0-9]/g) || []).length;
  const pauses = (text.match(/[，。！？、,.!?;；:：…—~～]/g) || []).length;
  return Math.max(2, cjk + words + digits * 0.6 + pauses * 1.1);
}

/** 拆分歌词文本为原始行，去掉空行与首尾空白 */
function splitLines(raw: string): string[] {
  return raw
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/\u00a0/g, " ").trim())
    .filter((line) => line.length > 0);
}

/**
 * 取一行里所有 LRC 时间戳（秒）。
 * 一行可能挂多个时间戳（同一句在不同时间重复），全部返回并按升序排列。
 */
function parseLrcTimes(line: string): number[] {
  const pattern = new RegExp(LRC_TAG.source, "g");
  const result: number[] = [];
  let match: RegExpExecArray | null = pattern.exec(line);
  while (match !== null) {
    const minutes = Number.parseInt(match[1] ?? "0", 10);
    const seconds = Number.parseInt(match[2] ?? "0", 10);
    const fractionText = match[3] ?? "";
    // `.5` 与 `.50` 都表示半秒：补足到三位再当小数处理
    const fraction = fractionText ? Number(`0.${fractionText.padEnd(3, "0").slice(0, 3)}`) : 0;
    if (Number.isFinite(minutes) && Number.isFinite(seconds)) {
      result.push(minutes * 60 + seconds + fraction);
    }
    match = pattern.exec(line);
  }
  return result.sort((a, b) => a - b);
}

/** 去掉行内的 LRC 时间戳，只留歌词正文 */
function stripLrcTags(line: string): string {
  return line.replace(new RegExp(LRC_TAG.source, "g"), "").trim();
}

/**
 * 构造歌词时间轴。
 *
 * @param lyrics   后端返回的歌词正文（可能为空）
 * @param duration 音频总时长（秒）；未知时用 180 兜底
 * @param lrc      可选的真实 LRC 文本（优先于 lyrics 里的时间戳），
 *                 来源可以是 `song.metadata.lrc` 或用户粘贴
 * @returns 按 start 升序的 items；没有可显示的歌词时返回空数组
 */
export function buildLyricTimeline(
  lyrics?: string | null,
  duration?: number | null,
  lrc?: string | null,
): LyricItem[] {
  const source = (lrc || lyrics || "").trim();
  if (!source || INSTRUMENTAL_ONLY.test(source)) return [];

  const total = Number.isFinite(duration) && (duration ?? 0) > 1 ? Number(duration) : 180;
  const rawLines = splitLines(source);

  // ---- 1. 先尝试 LRC：任何一行带时间戳就整体走真时间戳分支 ----
  const timed = rawLines.map((line) => ({ line, times: parseLrcTimes(line) }));
  if (timed.some((entry) => entry.times.length > 0)) {
    const items: LyricItem[] = [];
    for (const entry of timed) {
      if (!entry.times.length) continue;
      const content = stripLrcTags(entry.line);
      if (!content) continue;
      for (const stamp of entry.times) {
        items.push({
          id: `lrc-${items.length}`,
          kind: SECTION_LINE.test(content) ? "section" : "text",
          text: content,
          start: stamp,
          end: stamp,
          exact: true,
        });
      }
    }
    items.sort((a, b) => a.start - b.start);
    // end 由下一行的 start 决定；末行留 8 秒（LRC 通常不写结束时间）
    items.forEach((item, index) => {
      const next = items[index + 1];
      item.end = next ? next.start : Math.min(total, item.start + 8);
    });
    return items;
  }

  // ---- 2. 估算：按字重把 (总时长 - 前奏 - 尾奏) 按比例分配 ----
  const intro = clamp(total * INTRO_RATIO, MIN_INTRO, MAX_INTRO);
  const outro = clamp(total * OUTRO_RATIO, MIN_OUTRO, MAX_OUTRO);
  const span = Math.max(total - intro - outro, total * 0.5);

  const texts = rawLines.filter((line) => !SECTION_LINE.test(line));
  if (!texts.length) return [];
  const weights = texts.map(lineWeight);
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0) || 1;
  const unit = span / totalWeight;

  const items: LyricItem[] = [];
  let cursor = intro;
  let textIndex = 0;
  let lastSection: LyricItem | null = null;

  for (const line of rawLines) {
    if (SECTION_LINE.test(line)) {
      // 段落标签占位在下一句开始处，本身不消耗时长
      lastSection = { id: `section-${items.length}`, kind: "section", text: line, start: cursor, end: cursor, exact: false };
      items.push(lastSection);
      continue;
    }
    const length = Math.max(0.8, weights[textIndex] * unit);
    const item: LyricItem = {
      id: `text-${textIndex}`,
      kind: "text",
      text: line,
      start: cursor,
      end: Math.min(total, cursor + length),
      exact: false,
    };
    items.push(item);
    if (lastSection) {
      lastSection.start = item.start;
      lastSection.end = item.start;
      lastSection = null;
    }
    cursor = item.end;
    textIndex += 1;
  }
  return items;
}

/**
 * 求「当前正在唱的那一行」在 items 里的下标。
 *
 * 只考虑 kind === "text" 的行；时间早于第一句或晚于末句结束（+1s 缓冲）时
 * 返回 -1，让 UI 收起高亮。行数很少（< 200），线性扫描足够，不必二分。
 */
export function findActiveLyricIndex(items: LyricItem[], time: number): number {
  let active = -1;
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item.kind !== "text") continue; // 段落标签不参与高亮
    if (time < item.start) continue;
    if (time <= item.end) {
      active = index;
      break;
    }
    active = index; // 落在两句之间时保留上一句，避免闪烁
  }
  if (active >= 0 && time > items[active].end + 1) return -1;
  return active;
}

/** 从 metadata 里取可能存在的 LRC（后端暂未产出，留作扩展位） */
export function pickLrc(metadata?: Record<string, unknown> | null): string | null {
  if (!metadata) return null;
  for (const key of ["lrc", "lyrics_lrc", "timed_lyrics", "subtitle"]) {
    const value = metadata[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}
