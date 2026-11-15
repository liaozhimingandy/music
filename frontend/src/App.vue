<script setup lang="ts">
/** 应用外壳：只放路由出口，具体页面由 router 根据 URL 决定。 */
import { RouterView } from "vue-router";
</script>

<template>
  <RouterView />
</template>
