<script setup lang="ts">
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

const charCount = computed(() => prompt.value.length);
const nearLimit = computed(() => charCount.value >= MAX_PROMPT_LENGTH * 0.9);

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
  emit("submit", { prompt: clamp(prompt.value), instrumental: instrumental.value });
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
          {{ nearLimit ? `已接近字数上限（${charCount} / ${MAX_PROMPT_LENGTH}）` : "简单创作会自动扩写描述、生成歌词并起标题。" }}
        </p>
      </div>

      <div>
        <TButton
          variant="outline"
          size="small"
          :loading="expanding"
          :disabled="!prompt.trim() || expanding"
          @click="handleExpand"
        >
          <span class="inline-flex items-center gap-1"><EditIcon size="14px" />AI 扩写描述</span>
        </TButton>
      </div>

      <div class="flex items-center justify-between rounded-xl bg-muted px-3 py-2">
        <div>
          <p class="text-sm font-medium">纯音乐</p>
          <p class="text-[11px] text-subtle mt-0.5">开启后不生成人声与歌词</p>
        </div>
        <TSwitch v-model="instrumental" />
      </div>
    </div>

    <div class="px-5 py-4 border-t border-line">
      <TButton block size="large" theme="primary" :loading="isSubmitting" :disabled="isSubmitting" @click="submit">
        {{ isSubmitting ? "生成中…" : "开始生成" }}
      </TButton>
      <p class="text-[11px] text-subtle mt-2 text-center">简单创作 · 自动生成歌词与标题</p>
    </div>
  </aside>
</template>
