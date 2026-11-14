<script setup lang="ts">
/** 应用外壳：仅挂载主页面；全局状态都在 StudioPage 内，后续接路由时改这里。 */
import StudioPage from "@/pages/StudioPage.vue";
</script>

<template>
  <StudioPage />
</template>
