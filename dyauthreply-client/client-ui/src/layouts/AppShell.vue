<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import { getHealth } from '../api/client';

const route = useRoute();
const isWide = computed(() => Boolean(route.meta.wide));

const isOnline = ref(false);
const serviceName = ref('core-api');
let healthTimer: ReturnType<typeof setInterval> | null = null;

async function checkHealth() {
  try {
    const res = await getHealth();
    isOnline.value = res.ok;
    serviceName.value = res.service || 'core-api';
  } catch (e) {
    isOnline.value = false;
  }
}

onMounted(() => {
  checkHealth();
  healthTimer = setInterval(checkHealth, 5000);
});

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer);
});
</script>

<template>
  <!-- Liquid glow animated background -->
  <div class="liquid-bg-wrapper">
    <div class="liquid-blob blob-1"></div>
    <div class="liquid-blob blob-2"></div>
    <div class="liquid-blob blob-3"></div>
  </div>

  <div class="app-layout">
    <!-- Sleek glassmorphic sidebar -->
    <aside class="sidebar glass-panel">
      <div class="brand">
        <span class="logo-grad">Dy</span>
        <div class="brand-text">
          <h1>DyAuthReply</h1>
          <p>智能多号自动回复</p>
        </div>
      </div>

      <nav class="nav-links">
        <RouterLink to="/home" class="nav-item">
          <span class="icon">📊</span>概览
        </RouterLink>
        <RouterLink to="/accounts" class="nav-item">
          <span class="icon">📱</span>抖音账号
        </RouterLink>
        <RouterLink to="/chat" class="nav-item">
          <span class="icon">💬</span>私信工作台
        </RouterLink>
        <RouterLink to="/rules" class="nav-item">
          <span class="icon">⚙️</span>自动回复规则
        </RouterLink>
        <RouterLink to="/logs" class="nav-item">
          <span class="icon">📝</span>运行日志
        </RouterLink>
      </nav>

      <!-- Sidebar footer with connection health status -->
      <div class="sidebar-footer">
        <div class="status-badge" :class="{ online: isOnline }">
          <span class="pulse-dot"></span>
          <div class="status-info">
            <span class="status-text">{{ isOnline ? '服务运行中' : '服务未连接' }}</span>
            <span class="service-name">{{ serviceName }}</span>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main Workspace with Rounded Corner Card Style -->
    <main class="main-workspace" :class="{ wide: isWide }">
      <div class="scroll-container">
        <RouterView />
      </div>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  position: relative;
  z-index: 10;
  gap: 16px;
  padding-right: 12px;
}

.sidebar {
  width: 240px;
  height: calc(100vh - 24px);
  margin: 12px 0 12px 12px;
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  border-radius: 20px;
  z-index: 100;
}

.brand {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 36px;
  padding: 0 8px;
}

.logo-grad {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(0, 0, 0, 0.05), rgba(0, 0, 0, 0.01));
  border: 1px solid rgba(0, 0, 0, 0.12);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 1.15rem;
  color: var(--text-primary);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.brand-text h1 {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 800;
  letter-spacing: 0.5px;
  color: var(--text-primary);
}

.brand-text p {
  margin: 2px 0 0;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 500;
}

.nav-links {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-secondary);
  text-decoration: none;
  padding: 12px 14px;
  border-radius: 12px;
  font-size: 0.9rem;
  font-weight: 600;
  transition: var(--transition-quick);
  border: 1px solid transparent;
}

.nav-item .icon {
  font-size: 1.05rem;
}

.nav-item:hover {
  color: var(--text-primary);
  background: rgba(255, 255, 255, 0.35);
  transform: translateX(2px);
}

.nav-item.router-link-active {
  background: rgba(255, 255, 255, 0.75);
  color: var(--text-primary);
  border-color: rgba(255, 255, 255, 0.95);
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.03);
}

.sidebar-footer {
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.02);
  border: 1px solid rgba(0, 0, 0, 0.05);
  transition: var(--transition-smooth);
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: rgba(0, 0, 0, 0.15);
  box-shadow: 0 0 8px rgba(0, 0, 0, 0.05);
}

.online .pulse-dot {
  background-color: #22c55e;
  box-shadow: 0 0 10px #22c55e;
  animation: pulse 2s infinite;
}

.status-info {
  display: flex;
  flex-direction: column;
}

.status-text {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-muted);
}

.online .status-text {
  color: #15803d;
}

.service-name {
  font-size: 0.68rem;
  color: var(--text-muted);
}

@keyframes pulse {
  0% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7);
  }
  70% {
    transform: scale(1);
    box-shadow: 0 0 0 6px rgba(34, 197, 94, 0);
  }
  100% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(34, 197, 94, 0);
  }
}

.main-workspace {
  flex: 1;
  height: calc(100vh - 24px);
  margin: 12px 0;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border-radius: 20px;
  border: 1px solid var(--glass-border);
  box-shadow: 
    0 10px 40px -15px rgba(0, 0, 0, 0.08),
    inset 0 1px 1px 0 rgba(255, 255, 255, 0.5);
  background: var(--glass-bg);
}

.scroll-container {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 32px 48px;
  box-sizing: border-box;
}

.main-workspace.wide .scroll-container {
  padding: 0;
  overflow: hidden;
}
</style>
