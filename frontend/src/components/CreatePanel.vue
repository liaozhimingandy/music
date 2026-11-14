<script setup lang="ts">
/**
 * 左栏「简单创作」表单。
 * props: isSubmitting（提交中禁用）、expandPrompt（AI 扩写回调）
 * emits: submit(formValues)
 *
 * 描述上限 MAX_PROMPT_LENGTH = 500：模板用 maxlength 拦键盘输入，
 * 另用 watch + clamp() 兜底程序赋值 / 粘贴的超长文本（原生属性能被绕过）。
 * 纯音乐开关开启后，父组件的 handleCreate 会跳过写词这一步。
 *
 * 描述为空 / 过短不允许提交：canSubmit 控制按钮 disabled，submit() 再校验
 * 一次，后端 GenerationCreate 还有第三道 422 校验（防绕过 UI 直接调接口）。
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
/** 描述过短或正在提交时禁用「开始生成」 */
const canSubmit = computed(() => prompt.value.trim().length > MIN_PROMPT_LENGTH && !props.isSubmitting);

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

function submit() {
  const value = clamp(prompt.value).trim();
  // 按钮已禁用，这里兜底程序化触发与全空格输入；阈值与 canSubmit 保持一致
  if (value.length <= MIN_PROMPT_LENGTH) {
    MessagePlugin.warning(`请至少输入 ${MIN_PROMPT_LENGTH + 1} 个字的歌词或歌曲描述`);
    return;
  }
  emit("submit", { prompt: value, instrumental: instrumental.value });
}
</script>

<template>
  <aside class="w-[360px] min-w-[320px] bg-surface border-r border-line flex flex-col shadow-panel z-10">
    <div class="px-5 py-4 border-b border-line">
      <div class="flex items-center gap-2">
        <SoundIcon size="20px" class="text-brand-600" />
        <h2 class="text-base font-semibold">简单创作</h2>
      </div>
      <p class="text-xs text-subtle mt-1">描述你的想法，AI 帮你写成一首歌</p>
    </div>

    <div class="ace-scroll flex-1 overflow-y-auto px-5 py-5 space-y-5">
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
      </div>
    </div>
    <div class="px-5 py-4 border-t border-line">
      <TButton block size="large" theme="primary" :loading="isSubmitting" :disabled="!canSubmit" @click="submit">
        {{ isSubmitting ? "生成中…" : "开始生成" }}
      </TButton>
      <p class="text-[11px] text-subtle mt-2 text-center">
        {{ canSubmit || isSubmitting ? "简单创作 · 自动生成歌词与标题" : `请先输入歌词或歌曲描述（至少 ${MIN_PROMPT_LENGTH + 1} 字）` }}
      </p>
    </div>
  </aside>
</template>
