<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';

const route = useRoute();
const isWide = computed(() => Boolean(route.meta.wide));
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="logo">Dy</span>
        <div>
          <h1>DyAuthReply</h1>
          <p>抖音私信自动回复</p>
        </div>
      </div>
      <nav class="nav">
        <RouterLink to="/">概览</RouterLink>
        <RouterLink to="/accounts">我的抖音号</RouterLink>
        <RouterLink to="/chat">私信</RouterLink>
        <RouterLink to="/rules">规则</RouterLink>
        <RouterLink to="/logs">记录</RouterLink>
      </nav>
    </header>
    <main class="main" :class="{ wide: isWide }">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  height: 100vh;
  min-height: 100vh;
  overflow: hidden;
  background: linear-gradient(160deg, #0f172a 0%, #1e293b 45%, #0b1220 100%);
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 28px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.15);
  flex-shrink: 0;
}

.brand {
  display: flex;
  gap: 14px;
  align-items: center;
}

.logo {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, #fe2c55, #ff6b35);
  display: grid;
  place-items: center;
  font-weight: 800;
  color: #fff;
}

.brand h1 {
  margin: 0;
  font-size: 1.2rem;
}

.brand p {
  margin: 2px 0 0;
  color: #94a3b8;
  font-size: 0.82rem;
}

.nav {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.nav a {
  color: #94a3b8;
  text-decoration: none;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 0.88rem;
}

.nav a.router-link-active {
  background: rgba(254, 44, 85, 0.15);
  color: #fda4af;
}

.main {
  max-width: 960px;
  width: 100%;
  margin: 0 auto;
  padding: 24px 28px 48px;
  flex: 1;
  box-sizing: border-box;
}

.main.wide {
  max-width: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
</style>
