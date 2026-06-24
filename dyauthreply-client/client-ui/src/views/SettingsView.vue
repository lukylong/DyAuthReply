<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useClientSettings } from '../composables/useClientSettings';
import { useVersionUpdate } from '../composables/useVersionUpdate';
import { openExternalUrl } from '../api/client';

const { settings, resetSettings } = useClientSettings();
const { checkUpdate, isChecking, updateInfo, checkError } = useVersionUpdate();

const activeTab = ref<'version' | 'notification' | 'runtime'>('version');

// 运行设置：开机自启状态反馈
const autoStartBusy = ref(false);
const autoStartMessage = ref('');
const autoStartMessageType = ref<'info' | 'error' | 'success'>('info');

function isTauriEnv(): boolean {
  return (
    typeof window !== 'undefined' &&
    ('__TAURI__' in window || '__TAURI_INTERNALS__' in window)
  );
}

const checkFrequencyOptions = [
  { value: 'startup', label: '仅启动时' },
  { value: '6h', label: '每 6 小时' },
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
  { value: 'manual', label: '手动检查' },
];

async function handleCheckUpdate() {
  await checkUpdate();
}

async function handleOpenDownload() {
  const url = updateInfo.value?.download_url || updateInfo.value?.release_page;
  if (url) {
    await openExternalUrl(url);
  }
}

// 进入页面时，把开关同步为系统真实的自启状态，避免与本地缓存不一致
onMounted(async () => {
  if (!isTauriEnv()) return;
  try {
    const { isEnabled } = await import('@tauri-apps/plugin-autostart');
    const enabled = await isEnabled();
    settings.value.runtime.auto_start = enabled;
  } catch (error) {
    console.warn('Failed to read auto-start state:', error);
  }
});

function setAutoStartMessage(type: 'info' | 'error' | 'success', text: string) {
  autoStartMessageType.value = type;
  autoStartMessage.value = text;
}

async function onAutoStartChange(event: Event) {
  const target = event.target as HTMLInputElement;
  await handleToggleAutoStart(target.checked);
  // 强制把 DOM 同步为最终状态，避免回滚到原值时 Vue 因 diff 无变化而不更新复选框
  target.checked = settings.value.runtime.auto_start;
}

async function handleToggleAutoStart(enabled: boolean) {
  autoStartBusy.value = true;
  autoStartMessage.value = '';

  if (!isTauriEnv()) {
    setAutoStartMessage('error', '开机自启动仅在桌面客户端中可用');
    settings.value.runtime.auto_start = false; // 回滚
    autoStartBusy.value = false;
    return;
  }

  try {
    const { enable, disable, isEnabled } = await import('@tauri-apps/plugin-autostart');
    if (enabled) {
      await enable();
    } else {
      await disable();
    }
    // 以系统真实状态为准回写，确保设置生效
    const actual = await isEnabled();
    settings.value.runtime.auto_start = actual;
    setAutoStartMessage('success', actual ? '已开启开机自启动' : '已关闭开机自启动');
  } catch (error) {
    console.error('Failed to toggle auto-start:', error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    setAutoStartMessage('error', `设置失败：${errorMessage}`);
    settings.value.runtime.auto_start = !enabled; // 回滚
  } finally {
    autoStartBusy.value = false;
  }
}

function handleReset() {
  if (confirm('确定要恢复默认设置吗？')) {
    resetSettings();
  }
}

const updateStatusText = computed(() => {
  if (isChecking.value) return '检查中...';
  if (checkError.value) return `检查失败：${checkError.value}`;
  if (!updateInfo.value) return '未检查';
  if (updateInfo.value.has_update) {
    return `发现新版本 ${updateInfo.value.latest_version}（当前 ${updateInfo.value.current_version}）`;
  }
  return `已是最新版本（当前 ${updateInfo.value.current_version}）`;
});

const updateStatusType = computed<'info' | 'error' | 'success' | 'update'>(() => {
  if (isChecking.value) return 'info';
  if (checkError.value) return 'error';
  if (!updateInfo.value) return 'info';
  if (updateInfo.value.has_update) return 'update';
  return 'success';
});
</script>

<template>
  <div class="settings-page">
    <!-- 页面标题 -->
    <div class="page-header glass-panel">
      <div class="header-icon">🎛️</div>
      <div class="header-content">
        <h1 class="page-title">客户端设置</h1>
        <p class="page-subtitle">管理客户端的版本更新、通知和运行设置</p>
      </div>
    </div>

    <!-- 设置内容容器 -->
    <div class="settings-content glass-panel">
      <!-- 标签导航 -->
      <nav class="settings-nav">
        <button
          :class="['nav-tab', { active: activeTab === 'version' }]"
          @click="activeTab = 'version'"
        >
          <span class="tab-icon">🔄</span>
          <span class="tab-label">版本更新</span>
        </button>
        <button
          :class="['nav-tab', { active: activeTab === 'notification' }]"
          @click="activeTab = 'notification'"
        >
          <span class="tab-icon">🔔</span>
          <span class="tab-label">通知设置</span>
        </button>
        <button
          :class="['nav-tab', { active: activeTab === 'runtime' }]"
          @click="activeTab = 'runtime'"
        >
          <span class="tab-icon">⚡</span>
          <span class="tab-label">运行设置</span>
        </button>
      </nav>

      <!-- 标签内容 -->
      <div class="tab-content">
        <!-- 版本更新 -->
        <div v-if="activeTab === 'version'" class="settings-section">
          <div class="section-header">
            <h2 class="section-title">版本更新设置</h2>
            <p class="section-subtitle">自动检测并提示最新版本</p>
          </div>

          <div class="settings-group">
            <div class="setting-item">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    v-model="settings.version_update.enabled"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">启用版本更新检查</span>
                    <span class="setting-desc">自动检测新版本并在应用中提示更新</span>
                  </div>
                </label>
              </div>
            </div>

            <div v-if="settings.version_update.enabled" class="setting-item nested">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    v-model="settings.version_update.auto_download"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">自动下载更新</span>
                    <span class="setting-desc">检测到新版本后自动下载安装包（暂未实现）</span>
                  </div>
                </label>
              </div>
            </div>

            <div v-if="settings.version_update.enabled" class="setting-item nested">
              <div class="setting-main">
                <div class="setting-info">
                  <label class="setting-name" for="check-frequency">检查频率</label>
                  <span class="setting-desc">设置自动检查新版本的频率</span>
                </div>
                <select
                  id="check-frequency"
                  v-model="settings.version_update.check_frequency"
                  class="select-glass setting-select"
                >
                  <option v-for="opt in checkFrequencyOptions" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </select>
              </div>
            </div>
          </div>

          <div class="settings-action">
            <div class="action-row">
              <button
                class="btn-glass btn-action"
                @click="handleCheckUpdate"
                :disabled="isChecking"
              >
                <span class="btn-icon">🔍</span>
                <span>{{ isChecking ? '检查中...' : '立即检查更新' }}</span>
              </button>
              <button
                v-if="updateStatusType === 'update' && (updateInfo?.download_url || updateInfo?.release_page)"
                class="btn-glass btn-action btn-primary"
                @click="handleOpenDownload"
              >
                <span class="btn-icon">⬇️</span>
                <span>前往下载</span>
              </button>
            </div>
            <p :class="['action-status', `status-${updateStatusType}`]">{{ updateStatusText }}</p>
            <p v-if="updateStatusType === 'update' && updateInfo?.notes" class="update-notes">
              {{ updateInfo.notes }}
            </p>
          </div>
        </div>

        <!-- 通知设置 -->
        <div v-if="activeTab === 'notification'" class="settings-section">
          <div class="section-header">
            <h2 class="section-title">通知设置</h2>
            <p class="section-subtitle">管理系统通知和公告推送</p>
          </div>

          <div class="settings-group">
            <div class="setting-item">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    v-model="settings.notification.system_enabled"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">启用系统通知</span>
                    <span class="setting-desc">允许客户端发送操作系统原生通知（需用户授权）</span>
                  </div>
                </label>
              </div>
            </div>

            <div v-if="settings.notification.system_enabled" class="setting-item nested">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    v-model="settings.notification.announcement_enabled"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">启用公告推送</span>
                    <span class="setting-desc">接收管理后台发布的客户端公告</span>
                  </div>
                </label>
              </div>
            </div>
          </div>
        </div>

        <!-- 运行设置 -->
        <div v-if="activeTab === 'runtime'" class="settings-section">
          <div class="section-header">
            <h2 class="section-title">运行设置</h2>
            <p class="section-subtitle">管理客户端运行时行为</p>
          </div>

          <div class="settings-group">
            <div class="setting-item">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    :disabled="autoStartBusy"
                    :checked="settings.runtime.auto_start"
                    @change="onAutoStartChange"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">开机自启动</span>
                    <span class="setting-desc">系统启动时自动运行客户端（需系统授权）</span>
                    <span
                      v-if="autoStartMessage"
                      :class="['inline-status', `status-${autoStartMessageType}`]"
                    >{{ autoStartMessage }}</span>
                  </div>
                </label>
              </div>
            </div>

            <div class="setting-item">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    v-model="settings.runtime.minimize_to_tray"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">最小化到托盘</span>
                    <span class="setting-desc">关闭窗口时最小化到系统托盘而不是退出</span>
                  </div>
                </label>
              </div>
            </div>

            <div v-if="settings.runtime.minimize_to_tray" class="setting-item nested">
              <div class="setting-main">
                <label class="setting-label">
                  <input
                    type="checkbox"
                    class="setting-checkbox"
                    v-model="settings.runtime.start_minimized"
                  />
                  <span class="checkbox-icon"></span>
                  <div class="setting-info">
                    <span class="setting-name">启动时最小化</span>
                    <span class="setting-desc">启动时直接最小化到托盘，不显示主窗口</span>
                  </div>
                </label>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部操作 -->
        <div class="settings-footer">
          <button class="btn-glass btn-danger" @click="handleReset">
            <span class="btn-icon">↻</span>
            <span>恢复默认设置</span>
          </button>
          <p class="footer-hint">所有设置将恢复为默认值</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-height: 100%;
}

/* 页面标题 */
.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 24px 28px;
}

.header-icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  font-size: 1.5rem;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.6);
  flex-shrink: 0;
}

.header-content {
  flex: 1;
}

.page-title {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 0.875rem;
  color: var(--text-muted);
  font-weight: 500;
}

/* 设置内容容器 */
.settings-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 500px;
}

/* 标签导航 */
.settings-nav {
  display: flex;
  gap: 6px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}

.nav-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  background: none;
  border: 1px solid transparent;
  border-radius: 12px;
  color: var(--text-secondary);
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition-quick);
  outline: none;
}

.nav-tab:hover {
  background: rgba(255, 255, 255, 0.35);
  color: var(--text-primary);
}

.nav-tab.active {
  background: rgba(255, 255, 255, 0.7);
  border-color: rgba(255, 255, 255, 0.9);
  color: var(--text-primary);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.03);
}

.tab-icon {
  font-size: 1.1rem;
  line-height: 1;
}

.tab-label {
  line-height: 1;
}

/* 标签内容 */
.tab-content {
  flex: 1;
  padding: 28px 32px;
  overflow-y: auto;
}

/* 设置区域 */
.settings-section {
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.section-header {
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}

.section-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.section-subtitle {
  margin: 6px 0 0;
  font-size: 0.875rem;
  color: var(--text-muted);
  font-weight: 500;
}

/* 设置组 */
.settings-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 设置项 */
.setting-item {
  background: rgba(255, 255, 255, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 14px;
  padding: 18px 20px;
  transition: var(--transition-quick);
}

.setting-item:hover {
  background: rgba(255, 255, 255, 0.5);
  border-color: rgba(255, 255, 255, 0.6);
}

.setting-item.nested {
  background: rgba(255, 255, 255, 0.25);
  border-color: rgba(255, 255, 255, 0.3);
}

.setting-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.setting-label {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  cursor: pointer;
  user-select: none;
  flex: 1;
}

/* 自定义复选框 */
.setting-checkbox {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.checkbox-icon {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  border: 2px solid rgba(0, 0, 0, 0.15);
  background: rgba(255, 255, 255, 0.6);
  flex-shrink: 0;
  display: grid;
  place-items: center;
  transition: var(--transition-quick);
  position: relative;
  margin-top: 0;
}

.checkbox-icon::after {
  content: '✓';
  font-size: 0.9rem;
  font-weight: 700;
  color: white;
  opacity: 0;
  transform: scale(0.5);
  transition: var(--transition-quick);
}

.setting-checkbox:checked + .checkbox-icon {
  background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%);
  border-color: #0284c7;
  box-shadow: 0 2px 8px rgba(2, 132, 199, 0.25);
}

.setting-checkbox:checked + .checkbox-icon::after {
  opacity: 1;
  transform: scale(1);
}

.setting-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

/* 没有复选框的设置项（如"检查频率"）：补齐复选框宽度(22px)+间距(14px)，与上方标题左对齐 */
.setting-main > .setting-info {
  padding-left: 36px;
}

.setting-name {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  min-height: 22px;
}

.setting-desc {
  font-size: 0.8125rem;
  color: var(--text-muted);
  line-height: 1.4;
}

/* 下拉选择框 */
.setting-select {
  min-width: 160px;
  flex-shrink: 0;
  align-self: flex-start;
  margin-top: 2px;
}

/* 设置操作 */
.settings-action {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  background: rgba(255, 255, 255, 0.25);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 14px;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.btn-action {
  align-self: flex-start;
}

.btn-primary {
  background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%);
  border-color: #0284c7;
  color: #fff;
}

.btn-primary:hover:not(:disabled) {
  filter: brightness(1.05);
}

.btn-icon {
  font-size: 1rem;
  line-height: 1;
}

.action-status {
  margin: 0;
  font-size: 0.875rem;
  color: var(--text-secondary);
  font-weight: 500;
}

.action-status.status-error {
  color: #dc2626;
}

.action-status.status-success {
  color: #16a34a;
}

.action-status.status-update {
  color: #0284c7;
  font-weight: 600;
}

.update-notes {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--text-muted);
  line-height: 1.5;
  white-space: pre-wrap;
}

.inline-status {
  font-size: 0.8125rem;
  font-weight: 600;
  margin-top: 2px;
}

.inline-status.status-error {
  color: #dc2626;
}

.inline-status.status-success {
  color: #16a34a;
}

.inline-status.status-info {
  color: var(--text-secondary);
}

/* 底部操作 */
.settings-footer {
  margin-top: auto;
  padding-top: 28px;
  border-top: 1px solid rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.btn-danger {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.2);
  color: #dc2626;
  align-self: flex-start;
}

.btn-danger:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.3);
  color: #b91c1c;
}

.footer-hint {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--text-muted);
}
</style>
