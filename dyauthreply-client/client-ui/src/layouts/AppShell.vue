<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';
import { checkAppUpdate, getHealth, openExternalUrl, type AppUpdateInfo } from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';
import { APP_VERSION, useHiddenAdminEntry } from '../composables/useHiddenAdminEntry';
import { useVersionUpdate } from '../composables/useVersionUpdate';
import { useAnnouncementListener } from '../composables/useAnnouncementListener';
import AppModal from '../components/AppModal.vue';

const route = useRoute();
const router = useRouter();
const isWide = computed(() => Boolean(route.meta.wide));

const isOnline = ref(false);
const serviceName = ref('core-api');
const { licenseStatus, ensureStatus } = useClientLicense();
let healthTimer: ReturnType<typeof setInterval> | null = null;

// 集成版本更新和公告监听
const { hasUpdate: hasVersionUpdate, updateInfo: versionUpdateInfo } = useVersionUpdate();
const { unreadCount: unreadAnnouncementCount } = useAnnouncementListener();

const updateInfo = ref<AppUpdateInfo | null>(null);
const updateModalOpen = ref(false);
const checkingUpdate = ref(false);
const updateHint = ref('');
const openingDownload = ref(false);
const updateNoteLines = computed(() =>
  (updateInfo.value?.notes || '').split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
);

async function checkHealth() {
  try {
    const res = await getHealth();
    isOnline.value = res.ok;
    serviceName.value = res.service || 'core-api';
  } catch (e) {
    isOnline.value = false;
  }
}

async function runUpdateCheck(manual: boolean) {
  if (checkingUpdate.value) return;
  checkingUpdate.value = true;
  if (manual) updateHint.value = '正在检查更新…';
  try {
    const info = await checkAppUpdate(APP_VERSION);
    updateInfo.value = info;
    if (info.has_update) {
      updateModalOpen.value = true;
      updateHint.value = '';
    } else if (manual) {
      updateHint.value = '已是最新版本';
      window.setTimeout(() => {
        updateHint.value = '';
      }, 2500);
    }
  } catch (e) {
    if (manual) {
      updateHint.value = '检查更新失败';
      window.setTimeout(() => {
        updateHint.value = '';
      }, 2500);
    }
  } finally {
    checkingUpdate.value = false;
  }
}

async function goDownloadUpdate() {
  const url = updateInfo.value?.download_url || updateInfo.value?.release_page || '';
  if (!url) return;
  openingDownload.value = true;
  try {
    await openExternalUrl(url);
  } finally {
    openingDownload.value = false;
  }
}

function dismissUpdate() {
  if (updateInfo.value?.mandatory) return;
  updateModalOpen.value = false;
}

onMounted(() => {
  checkHealth();
  ensureStatus();
  healthTimer = setInterval(checkHealth, 5000);
  window.setTimeout(() => runUpdateCheck(false), 3000);
});

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer);
});

const { onHiddenAdminClick } = useHiddenAdminEntry(() => {
  router.push('/admin');
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
        <span class="logo-grad" @click="onHiddenAdminClick">D</span>
        <div class="brand-text">
          <h1>D助手</h1>
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
        <RouterLink to="/license" class="nav-item">
          <span class="icon">🔐</span>客户端授权
        </RouterLink>
        <RouterLink to="/chat" class="nav-item">
          <span class="icon">💬</span>私信工作台
        </RouterLink>
        <RouterLink to="/rules" class="nav-item">
          <span class="icon">⚙️</span>自动回复规则
        </RouterLink>
        <RouterLink to="/logs" class="nav-item">
          <span class="icon">📝</span>回复记录
        </RouterLink>
        <RouterLink to="/settings" class="nav-item">
          <span class="icon">⚙️</span>客户端设置
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
        <div v-if="licenseStatus" class="license-badge" :class="licenseStatus.state">
          <span class="license-title">授权</span>
          <span class="license-value">{{ licenseStatus.state_label }}</span>
        </div>
        <div
          class="version-tag"
          :class="{ checking: checkingUpdate, 'has-update': hasVersionUpdate }"
          :title="'点击检查更新'"
          @click="runUpdateCheck(true)"
        >
          {{ updateHint || APP_VERSION }}
          <span v-if="hasVersionUpdate" class="update-badge"></span>
        </div>
      </div>
    </aside>

    <AppModal :open="updateModalOpen" title="发现新版本" dialog-role="alertdialog" @close="dismissUpdate">
      <div class="update-body">
        <div class="update-version">
          <span class="from">{{ updateInfo?.current_version }}</span>
          <span class="arrow">→</span>
          <span class="to">{{ updateInfo?.latest_version }}</span>
        </div>
        <ul v-if="updateNoteLines.length" class="update-notes">
          <li v-for="(line, i) in updateNoteLines" :key="i">{{ line }}</li>
        </ul>
        <p class="update-tip">
          点击「前往下载」将在浏览器中打开安装包，下载完成后直接安装即可覆盖更新（数据不会丢失）。
        </p>
        <p v-if="updateInfo?.mandatory" class="update-mandatory">本次为强制更新，请更新后继续使用。</p>
        <div class="update-actions">
          <button v-if="!updateInfo?.mandatory" type="button" class="btn-ghost" @click="dismissUpdate">
            稍后
          </button>
          <button type="button" class="btn-primary" :disabled="openingDownload" @click="goDownloadUpdate">
            {{ openingDownload ? '正在打开…' : '前往下载' }}
          </button>
        </div>
      </div>
    </AppModal>

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

.version-tag {
  margin-top: 10px;
  text-align: center;
  font-size: 0.68rem;
  color: var(--text-muted);
  opacity: 0.55;
  user-select: none;
  cursor: pointer;
  transition: var(--transition-quick);
}

.version-tag:hover {
  opacity: 0.9;
}

.version-tag.checking {
  opacity: 0.9;
}

.update-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.update-version {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 1.05rem;
}

.update-version .from {
  color: var(--text-muted);
}

.update-version .arrow {
  color: var(--text-muted);
}

.update-version .to {
  color: #15803d;
}

.update-notes {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.update-tip {
  margin: 0;
  font-size: 0.82rem;
  color: var(--text-muted);
  line-height: 1.6;
}

.update-mandatory {
  margin: 0;
  font-size: 0.85rem;
  font-weight: 600;
  color: #b45309;
}

.update-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 4px;
}

.update-actions .btn-ghost {
  border: 1px solid rgba(0, 0, 0, 0.1);
  background: rgba(0, 0, 0, 0.02);
  color: var(--text-secondary);
  padding: 9px 18px;
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition-quick);
}

.update-actions .btn-ghost:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--text-primary);
}

.update-actions .btn-primary {
  border: none;
  background: linear-gradient(135deg, #1f2937, #111827);
  color: #fff;
  padding: 9px 20px;
  border-radius: 10px;
  font-weight: 700;
  cursor: pointer;
  transition: var(--transition-quick);
}

.update-actions .btn-primary:hover {
  filter: brightness(1.15);
}

.update-actions .btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.license-badge {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  background: rgba(0, 0, 0, 0.02);
  border: 1px solid rgba(0, 0, 0, 0.05);
}

.license-title {
  font-size: 0.78rem;
  color: var(--text-muted);
}

.license-value {
  font-size: 0.82rem;
  font-weight: 700;
}

.license-badge.active .license-value {
  color: #15803d;
}

.license-badge.grace .license-value {
  color: #b45309;
}

.license-badge.expired .license-value,
.license-badge.revoked .license-value,
.license-badge.invalid .license-value,
.license-badge.unactivated .license-value {
  color: #b91c1c;
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

/* 版本更新红点 */
.version-tag {
  position: relative;
}

.version-tag.has-update .update-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 8px;
  height: 8px;
  background: #ef4444;
  border-radius: 50%;
  border: 2px solid var(--glass-bg);
  animation: pulse-red 2s infinite;
}

@keyframes pulse-red {
  0% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7);
  }
  70% {
    transform: scale(1);
    box-shadow: 0 0 0 6px rgba(239, 68, 68, 0);
  }
  100% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
  }
}
</style>
