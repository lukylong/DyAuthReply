<script setup lang="ts">
import { onMounted, ref, computed } from 'vue';
import { useRouter } from 'vue-router';
import { getHealth } from '../api/client';
import { APP_VERSION, useVersionEasterEgg } from '../composables/useVersionEasterEgg';
import AdminPasswordModal from '../components/AdminPasswordModal.vue';

const router = useRouter();
const showAdminModal = ref(false);
const status = ref<'starting' | 'connecting' | 'ready' | 'failed'>('starting');
const retryCount = ref(0);
const maxRetries = 90; // 首次安装迁移可能较慢，最多约 90 秒
const errorMessage = ref('');

const statusText = computed(() => {
  switch (status.value) {
    case 'starting':
      return '正在启动服务';
    case 'connecting':
      return `正在连接服务 (${retryCount.value}/${maxRetries})`;
    case 'ready':
      return '服务已就绪';
    case 'failed':
      return '服务启动失败';
    default:
      return '';
  }
});

async function checkServiceHealth() {
  try {
    const result = await getHealth();
    if (result.ok) {
      status.value = 'ready';
      // 延迟一下再跳转，让用户看到"已就绪"的状态
      setTimeout(() => {
        router.replace('/home');
      }, 800);
      return true;
    }
    return false;
  } catch (e) {
    return false;
  }
}

async function startConnectionLoop() {
  status.value = 'starting';

  // 先等待1秒，给后端启动的时间
  await new Promise(resolve => setTimeout(resolve, 1000));

  status.value = 'connecting';

  while (retryCount.value < maxRetries) {
    retryCount.value++;

    const connected = await checkServiceHealth();
    if (connected) {
      return;
    }

    // 智能退避：前几次快速重试，后面慢一点
    const delay = retryCount.value < 10 ? 500 : 1000;
    await new Promise(resolve => setTimeout(resolve, delay));
  }

  // 超时失败
  status.value = 'failed';
  errorMessage.value =
    '服务启动超时。请确认未重复打开多个 DyAuthReply 窗口，并查看日志：%APPDATA%\\DyAuthReply\\logs\\launcher.log（Windows）或 ~/Library/Application Support/DyAuthReply/logs/launcher.log（macOS）';
}

async function retry() {
  retryCount.value = 0;
  errorMessage.value = '';
  await startConnectionLoop();
}

onMounted(() => {
  startConnectionLoop();
});

const { onVersionClick } = useVersionEasterEgg(() => {
  showAdminModal.value = true;
});

function onAdminSuccess() {
  showAdminModal.value = false;
  router.push('/admin');
}
</script>

<template>
  <AdminPasswordModal
    :open="showAdminModal"
    @close="showAdminModal = false"
    @success="onAdminSuccess"
  />
  <div class="splash-container">
    <!-- 背景动画 -->
    <div class="liquid-bg-wrapper">
      <div class="liquid-blob blob-1"></div>
      <div class="liquid-blob blob-2"></div>
      <div class="liquid-blob blob-3"></div>
    </div>

    <div class="splash-content glass-panel">
      <!-- Logo区域 -->
      <div class="logo-section">
        <div class="logo-circle">
          <span class="logo-text">Dy</span>
        </div>
        <h1 class="app-title">DyAuthReply</h1>
        <p class="app-subtitle">智能多号自动回复</p>
      </div>

      <!-- 状态指示器 -->
      <div class="status-section">
        <div v-if="status === 'starting' || status === 'connecting'" class="loading-indicator">
          <div class="spinner-ring"></div>
          <div class="status-text">{{ statusText }}</div>
          <div class="status-hint">首次启动可能需要 30–60 秒（数据库初始化）...</div>
        </div>

        <div v-else-if="status === 'ready'" class="success-indicator">
          <div class="success-icon">✓</div>
          <div class="status-text success">{{ statusText }}</div>
        </div>

        <div v-else-if="status === 'failed'" class="error-indicator">
          <div class="error-icon">⚠️</div>
          <div class="status-text error">{{ statusText }}</div>
          <div class="error-message">{{ errorMessage }}</div>
          <button type="button" class="btn-retry" @click="retry">
            重新连接
          </button>
        </div>
      </div>

      <!-- 版本信息（连点 10 次 → 运行日志） -->
      <div class="version-info" @click="onVersionClick">
        <span>{{ APP_VERSION }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.splash-container {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
}

.liquid-bg-wrapper {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 50%, #dbeafe 100%);
}

.liquid-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.3;
  animation: float 20s infinite ease-in-out;
}

.blob-1 {
  width: 500px;
  height: 500px;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  top: -10%;
  left: -10%;
  animation-delay: 0s;
}

.blob-2 {
  width: 400px;
  height: 400px;
  background: linear-gradient(135deg, #10b981, #06b6d4);
  bottom: -10%;
  right: -10%;
  animation-delay: 5s;
}

.blob-3 {
  width: 350px;
  height: 350px;
  background: linear-gradient(135deg, #f59e0b, #ef4444);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  animation-delay: 10s;
}

@keyframes float {
  0%, 100% {
    transform: translate(0, 0) scale(1);
  }
  25% {
    transform: translate(30px, -30px) scale(1.1);
  }
  50% {
    transform: translate(-20px, 20px) scale(0.9);
  }
  75% {
    transform: translate(20px, 30px) scale(1.05);
  }
}

.splash-content {
  position: relative;
  z-index: 10;
  width: 440px;
  padding: 48px 40px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 36px;
  border-radius: 24px;
  animation: fadeInUp 0.6s ease-out;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.logo-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.logo-circle {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(139, 92, 246, 0.1));
  border: 2px solid rgba(59, 130, 246, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 32px rgba(59, 130, 246, 0.2);
  animation: pulse-logo 2s infinite;
}

@keyframes pulse-logo {
  0%, 100% {
    box-shadow: 0 8px 32px rgba(59, 130, 246, 0.2);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 12px 40px rgba(59, 130, 246, 0.3);
    transform: scale(1.05);
  }
}

.logo-text {
  font-size: 2rem;
  font-weight: 900;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.app-title {
  margin: 0;
  font-size: 1.8rem;
  font-weight: 800;
  color: var(--text-primary);
  letter-spacing: -0.5px;
}

.app-subtitle {
  margin: 0;
  font-size: 0.9rem;
  color: var(--text-secondary);
  font-weight: 500;
}

.status-section {
  width: 100%;
  min-height: 140px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.loading-indicator,
.success-indicator,
.error-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  animation: fadeIn 0.4s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.spinner-ring {
  width: 48px;
  height: 48px;
  border: 4px solid rgba(59, 130, 246, 0.1);
  border-radius: 50%;
  border-top-color: #3b82f6;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.success-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #10b981, #059669);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5rem;
  color: white;
  font-weight: bold;
  animation: scaleIn 0.4s ease-out;
}

@keyframes scaleIn {
  from {
    transform: scale(0);
  }
  to {
    transform: scale(1);
  }
}

.error-icon {
  font-size: 3rem;
  animation: shake 0.5s ease-out;
}

@keyframes shake {
  0%, 100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-10px);
  }
  75% {
    transform: translateX(10px);
  }
}

.status-text {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
  text-align: center;
}

.status-text.success {
  color: #059669;
}

.status-text.error {
  color: #dc2626;
}

.status-hint {
  font-size: 0.85rem;
  color: var(--text-muted);
  text-align: center;
}

.error-message {
  font-size: 0.85rem;
  color: #dc2626;
  text-align: center;
  background: rgba(220, 38, 38, 0.05);
  padding: 12px 20px;
  border-radius: 8px;
  border: 1px solid rgba(220, 38, 38, 0.1);
  max-width: 320px;
}

.btn-retry {
  margin-top: 8px;
  padding: 10px 28px;
  border-radius: 12px;
  border: none;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: white;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
}

.btn-retry:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(59, 130, 246, 0.4);
}

.btn-retry:active {
  transform: translateY(0);
}

.version-info {
  font-size: 0.75rem;
  color: var(--text-muted);
  opacity: 0.6;
  cursor: default;
  user-select: none;
}

.glass-panel {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 20px 60px rgba(0, 0, 0, 0.1),
    inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
</style>
