<script setup lang="ts">
/**
 * 左栏「简单创作」表单。
 * props:
 * - isSubmitting：提交中禁用；
 * - expandPrompt：AI 扩写回调；
 * - quota：今日生成配额（来自 /v1/health），remaining 为 null 表示后端不限额。
 * emits: submit(formValues)
 *
 * 描述上限 MAX_PROMPT_LENGTH = 500：模板用 maxlength 拦键盘输入，
 * 另用 watch + clamp() 兜底程序赋值 / 粘贴的超长文本（原生属性能被绕过）。
 *
 * 描述为空 / 过短不允许提交：canSubmit 控制按钮 disabled，submit() 再校验
 * 一次，后端 GenerationCreate 还有第三道 422 校验（防绕过 UI 直接调接口）。
 * 额度用完（remaining === 0）时按钮同样禁用，并提示每天的上限与重置时间；
 * 后端仍有 429 兜底，这里只是提前把话说清楚，别让用户白点。
 * 键盘：Enter 直接提交、Shift+Enter 换行（见 onEnterDown，已处理输入法
 * 组合态与提交中重复触发）。
 * AI 扩写按钮 / 纯音乐开关当前未挂载在模板里，相关 props 与回调保留备用。
 */
import { computed, ref, watch } from "vue";
import { Button as TButton, MessagePlugin, Switch as TSwitch, Textarea as TTextarea } from "tdesign-vue-next";
import { EditIcon, SoundIcon } from "tdesign-icons-vue-next";

import type { CreateFormValues } from "@/interface";
import { MAX_PROMPT_LENGTH } from "@/common/constants";

const props = defineProps<{
  isSubmitting: boolean;
  expandPrompt: (prompt: string, instrumental: boolean) => Promise<string>;
  /** 今日配额快照；remaining 为 null = 后端不限额 */
  quota?: { limit: number; remaining: number | null } | null;
}>();

const emit = defineEmits<{
  submit: [values: CreateFormValues];
}>();

const prompt = ref("");
const instrumental = ref(false);
const expanding = ref(false);

/** 提交门槛：描述至少 6 字（>5）。按钮与 submit() 共用，改这一处即可 */
const MIN_PROMPT_LENGTH = 5;

/** 字数计数与「接近上限」提示：达到 90% 时输入框与提示文字转为警示态 */
const charCount = computed(() => prompt.value.length);
const nearLimit = computed(() => charCount.value >= MAX_PROMPT_LENGTH * 0.9);
/** 今日剩余额度；null = 不限额 / 还没取到 */
const remaining = computed(() => props.quota?.remaining ?? null);
/** 额度用完：按钮禁用并给出明确文案 */
const quotaExhausted = computed(() => remaining.value === 0);
/** 描述过短、正在提交、额度用完时禁用「开始生成」 */
const canSubmit = computed(
  () => prompt.value.trim().length > MIN_PROMPT_LENGTH && !props.isSubmitting && !quotaExhausted.value,
);

function clamp(value: string): string {
  return value.slice(0, MAX_PROMPT_LENGTH);
}

// 原生 maxlength 只拦截键盘输入，程序赋值/粘贴超长内容时仍做一次兜底截断
watch(prompt, (value) => {
  if (value.length > MAX_PROMPT_LENGTH) prompt.value = clamp(value);
});

async function handleExpand() {
  if (!prompt.value.trim()) return;
  expanding.value = true;
  try {
    prompt.value = clamp(await props.expandPrompt(prompt.value, instrumental.value));
    MessagePlugin.success("描述已扩写");
  } catch {
    MessagePlugin.error("扩写失败，请检查后端服务");
  } finally {
    expanding.value = false;
  }
}

function submit(warn = true) {
  const value = clamp(prompt.value).trim();
  // 额度用完：按钮已禁用，这里兜底回车 / 程序化触发
  if (quotaExhausted.value) {
    if (warn) MessagePlugin.warning(`今日生成额度已用完（每天 ${props.quota?.limit ?? 0} 首），明天恢复`);
    return;
  }
  // 按钮已禁用，这里兜底程序化触发与全空格输入；阈值与 canSubmit 保持一致
  if (value.length <= MIN_PROMPT_LENGTH) {
    // 回车触发时静默返回：用户还在打字，弹出的警告反而打断输入
    if (warn) MessagePlugin.warning(`请至少输入 ${MIN_PROMPT_LENGTH + 1} 个字的歌词或歌曲描述`);
    return;
  }
  emit("submit", { prompt: value, instrumental: instrumental.value });
}

/**
 * 回车直接提交，Shift + Enter 换行。
 *
 * 三个必须挡掉的场景：
 * 1. 中文输入法组合输入时按回车只是「上屏」，`isComposing` / keyCode 229 时忽略，
 *    否则选词回车会误触发生成；
 * 2. 正在提交中（isSubmitting）不重复提交，避免双击式重复建任务；
 * 3. 空内容 / 字数不足时静默返回，不弹提示打断打字。
 */
function onEnterDown(event: KeyboardEvent) {
  if (event.key !== "Enter") return;
  // 只处理输入框里的回车：焦点在按钮上时交给按钮自己的 click，
  // 否则回车会被「按钮 click + 本函数」执行两次
  if (!(event.target instanceof HTMLTextAreaElement)) return;
  if (event.isComposing || event.keyCode === 229) return;
  if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
  event.preventDefault();
  if (!canSubmit.value) return;
  submit(false);
}
</script>

<template>
  <!--
    面板宽度交给外层栅格（t-col）决定，这里只用 w-full h-full 撑满所在列。
    早期版本写死 w-[360px] min-w-[320px]，窄屏下三栏加起来远超视口宽度，
    右边的详情栏被挤出屏幕完全点不到——那正是这次改成栅格 + 单栏的原因。
  -->
  <aside class="w-full h-full bg-surface border-r border-line flex flex-col shadow-panel z-10">
    <div class="px-4 md:px-5 py-3 md:py-4 border-b border-line flex-shrink-0">
      <div class="flex items-center gap-2">
        <SoundIcon size="20px" class="text-brand-600" />
        <h2 class="text-base font-semibold">简单创作</h2>
      </div>
      <p class="text-xs text-subtle mt-1">描述你的想法，AI 帮你写成一首歌</p>
    </div>

    <div class="ace-scroll flex-1 min-h-0 overflow-y-auto px-4 md:px-5 py-4 md:py-5 space-y-5" @keydown="onEnterDown">

      <div>
        <div class="flex items-center justify-between mb-2">
          <label class="text-sm font-medium">歌词 / 歌曲描述</label>
          <span class="text-[11px]" :class="nearLimit ? 'text-brand-600 font-medium' : 'text-subtle'">
            最多 {{ MAX_PROMPT_LENGTH }} 字
          </span>
        </div>
        <TTextarea
          v-model="prompt"
          placeholder="例如：一首关于夏末告别的华语独立民谣，木吉他和温柔女声"
          :autosize="{ minRows: 6, maxRows: 12 }"
          :maxlength="MAX_PROMPT_LENGTH"
          :status="nearLimit ? 'warning' : 'default'"
        />
        <p class="text-xs mt-2" :class="nearLimit ? 'text-brand-600' : 'text-subtle'">
          {{ nearLimit ? `已接近字数上限（${charCount} / ${MAX_PROMPT_LENGTH}）` : "提交后由后端自动完成策划、写词与编曲。" }}
        </p>
        <!-- 快捷键提示只在有物理键盘时才有意义，窄屏（触屏）隐藏 -->
        <p class="hidden md:flex items-center gap-1 text-[11px] text-subtle mt-1.5">
          <kbd class="ace-kbd">Enter</kbd> 直接生成
          <span class="text-line">·</span>
          <kbd class="ace-kbd">Shift</kbd>+<kbd class="ace-kbd">Enter</kbd> 换行
        </p>
      </div>
    </div>
    <div class="px-4 md:px-5 py-3 md:py-4 border-t border-line flex-shrink-0">
      <div :class="{ 'ace-sweep': isSubmitting }">
        <TButton block size="large" theme="primary" :loading="isSubmitting" :disabled="!canSubmit" @click="submit()">
          {{ isSubmitting ? "排队生成中…" : "开始生成" }}
        </TButton>
      </div>
      <p class="text-[11px] mt-2 text-center leading-4" :class="{ 'ace-shimmer-text font-medium': isSubmitting, 'text-amber-600': quotaExhausted }">
        {{
          isSubmitting
            ? "已提交，正在排队生成，请稍候…"
            : quotaExhausted
              ? `今日生成额度已用完（每天 ${quota?.limit ?? 0} 首），明天 00:00 后恢复`
              : canSubmit
                ? remaining !== null
                  ? `简单创作 · 今日还剩 ${remaining} 首`
                  : "简单创作 · 自动生成歌词与标题"
                : `请先输入歌词或歌曲描述（至少 ${MIN_PROMPT_LENGTH + 1} 字）`
        }}
      </p>
    </div>
  </aside>
</template>
