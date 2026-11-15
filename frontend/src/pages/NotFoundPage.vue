<script setup lang="ts">
/** 404 页面：路由未匹配时渲染（配合托管端把未知路径回退到 404.html 使用）。 */
import { useRouter } from "vue-router";

const router = useRouter();
</script>

<template>
  <div class="nf-wrap">
    <div class="nf-card">
      <div class="nf-code">404</div>
      <h1 class="nf-title">页面走丢了</h1>
      <p class="nf-desc">
        你访问的地址不存在，或链接已失效。<br />
        返回工作台继续创作吧。
      </p>
      <button class="nf-btn" @click="router.push('/')">返回首页</button>
    </div>
  </div>
</template>

<style scoped>
.nf-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f6f8;
  padding: 24px;
}
.nf-card {
  text-align: center;
  background: #fff;
  border: 1px solid #e9ebf0;
  border-radius: 16px;
  padding: 48px 56px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.05);
}
.nf-code {
  font-size: 72px;
  font-weight: 800;
  line-height: 1;
  color: #ec4141;
  letter-spacing: -2px;
}
.nf-title {
  margin: 16px 0 8px;
  font-size: 20px;
  font-weight: 700;
  color: #1d2129;
}
.nf-desc {
  margin: 0 0 24px;
  font-size: 14px;
  line-height: 1.7;
  color: #7a8090;
}
.nf-btn {
  border: none;
  cursor: pointer;
  background: #ec4141;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  padding: 10px 28px;
  border-radius: 999px;
  transition: background 0.15s ease;
}
.nf-btn:hover {
  background: #f05b5b;
}
.nf-btn:active {
  background: #c20c0c;
}
</style>
