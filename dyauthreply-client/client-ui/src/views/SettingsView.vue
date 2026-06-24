<script setup lang="ts">
import { ref, computed } from 'vue';
import { useClientSettings } from '../composables/useClientSettings';
import { useVersionUpdate } from '../composables/useVersionUpdate';

const { settings, resetSettings } = useClientSettings();
const { checkUpdate, isChecking, updateInfo } = useVersionUpdate();

const activeTab = ref<'version' | 'notification' | 'runtime' | 'privacy'>('version');

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

async function handleOpenDataDir() {
  try {
    // 检测是否在 Tauri 环境
    if ('__TAURI__' in window) {
      const { open } = await import('@tauri-apps/plugin-shell');
      // TODO: 从配置或环境变量获取实际的数据目录路径
      await open('/var/lib/dyauthreply/client');
    } else {
      alert('此功能仅在桌面客户端中可用');
    }
  } catch (error) {
    console.error('Failed to open data directory:', error);
    alert('打开数据目录失败');
  }
}

async function handleToggleAutoStart(enabled: boolean) {
  try {
    // 检测是否在 Tauri 环境
    if ('__TAURI__' in window) {
      const { enable, disable } = await import('@tauri-apps/plugin-autostart');
      if (enabled) {
        await enable();
      } else {
        await disable();
      }
    } else {
      console.warn('Auto-start is only available in Tauri environment');
    }
  } catch (error) {
    console.error('Failed to toggle auto-start:', error);
    settings.value.runtime.auto_start = !enabled; // 回滚
  }
}

function handleReset() {
  if (confirm('确定要恢复默认设置吗？')) {
    resetSettings();
  }
}

const updateStatusText = computed(() => {
  if (isChecking.value) return '检查中...';
  if (!updateInfo.value) return '未检查';
  if (updateInfo.value.has_update) {
    return `有新版本：${updateInfo.value.latest_version}`;
  }
  return '已是最新版本';
});
</script>

<template>
  <div class="settings-page">
    <div class="page-head">
      <div>
        <h2>客户端设置</h2>
        <p class="sub">管理客户端的版本更新、通知、运行和隐私设置</p>
      </div>
    </div>

    <div class="settings-container">
      <!-- 左侧标签页 -->
      <div class="settings-sidebar">
        <button
          :class="['tab-btn', { active: activeTab === 'version' }]"
          @click="activeTab = 'version'"
        >
          版本更新
        </button>
        <button
          :class="['tab-btn', { active: activeTab === 'notification' }]"
          @click="activeTab = 'notification'"
        >
          通知设置
        </button>
        <button
          :class="['tab-btn', { active: activeTab === 'runtime' }]"
          @click="activeTab = 'runtime'"
        >
          运行设置
        </button>
        <button
          :class="['tab-btn', { active: activeTab === 'privacy' }]"
          @click="activeTab = 'privacy'"
        >
          数据与隐私
        </button>
      </div>

      <!-- 右侧内容区 -->
      <div class="settings-content">
        <!-- 版本更新 -->
        <div v-if="activeTab === 'version'" class="settings-section">
          <h3>版本更新设置</h3>
          <div class="form-group">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.version_update.enabled"
              />
              <span>启用版本更新检查</span>
            </label>
            <p class="hint">自动检测新版本并在应用中提示更新</p>
          </div>

          <div class="form-group" v-if="settings.version_update.enabled">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.version_update.auto_download"
              />
              <span>自动下载更新</span>
            </label>
            <p class="hint">检测到新版本后自动下载安装包（暂未实现）</p>
          </div>

          <div class="form-group" v-if="settings.version_update.enabled">
            <label class="form-label">检查频率</label>
            <select v-model="settings.version_update.check_frequency" class="form-select">
              <option v-for="opt in checkFrequencyOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <p class="hint">设置自动检查新版本的频率</p>
          </div>

          <div class="form-group">
            <button
              class="btn btn-primary"
              @click="handleCheckUpdate"
              :disabled="isChecking"
            >
              {{ isChecking ? '检查中...' : '立即检查更新' }}
            </button>
            <p class="status-text">{{ updateStatusText }}</p>
          </div>
        </div>

        <!-- 通知设置 -->
        <div v-if="activeTab === 'notification'" class="settings-section">
          <h3>通知设置</h3>
          <div class="form-group">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.notification.system_enabled"
              />
              <span>启用系统通知</span>
            </label>
            <p class="hint">允许客户端发送操作系统原生通知（需用户授权）</p>
          </div>

          <div class="form-group" v-if="settings.notification.system_enabled">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.notification.announcement_enabled"
              />
              <span>启用公告推送</span>
            </label>
            <p class="hint">接收管理后台发布的客户端公告</p>
          </div>
        </div>

        <!-- 运行设置 -->
        <div v-if="activeTab === 'runtime'" class="settings-section">
          <h3>运行设置</h3>
          <div class="form-group">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.runtime.auto_start"
                @change="handleToggleAutoStart(settings.runtime.auto_start)"
              />
              <span>开机自启动</span>
            </label>
            <p class="hint">系统启动时自动运行客户端（需系统授权）</p>
          </div>

          <div class="form-group">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.runtime.minimize_to_tray"
              />
              <span>最小化到托盘</span>
            </label>
            <p class="hint">关闭窗口时最小化到系统托盘而不是退出</p>
          </div>

          <div class="form-group" v-if="settings.runtime.minimize_to_tray">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.runtime.start_minimized"
              />
              <span>启动时最小化</span>
            </label>
            <p class="hint">启动时直接最小化到托盘，不显示主窗口</p>
          </div>
        </div>

        <!-- 数据与隐私 -->
        <div v-if="activeTab === 'privacy'" class="settings-section">
          <h3>数据与隐私</h3>
          <div class="form-group">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="settings.privacy.analytics_enabled"
                disabled
              />
              <span>匿名数据收集（暂未实现）</span>
            </label>
            <p class="hint">
              帮助我们改进产品，仅收集匿名使用数据，不包含个人信息
            </p>
          </div>

          <div class="form-group">
            <label class="form-label">数据目录</label>
            <p class="path-text">/var/lib/dyauthreply/client</p>
            <button class="btn btn-secondary" @click="handleOpenDataDir">
              打开目录
            </button>
            <p class="hint">客户端数据存储位置</p>
          </div>
        </div>

        <!-- 底部操作 -->
        <div class="settings-footer">
          <button class="btn btn-danger" @click="handleReset">
            恢复默认设置
          </button>
          <p class="hint">所有设置将恢复为默认值</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  padding: 2rem;
  max-width: 1200px;
  margin: 0 auto;
}

.page-head {
  margin-bottom: 2rem;
}

.page-head h2 {
  font-size: 1.75rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
  color: #1a1a1a;
}

.page-head .sub {
  color: #666;
  font-size: 0.9rem;
}

.settings-container {
  display: flex;
  gap: 2rem;
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  min-height: 500px;
}

.settings-sidebar {
  width: 200px;
  border-right: 1px solid #e5e5e5;
  padding: 1.5rem 0;
  flex-shrink: 0;
}

.tab-btn {
  display: block;
  width: 100%;
  padding: 0.75rem 1.5rem;
  text-align: left;
  border: none;
  background: none;
  cursor: pointer;
  color: #666;
  font-size: 0.95rem;
  transition: all 0.2s;
}

.tab-btn:hover {
  background: #f5f5f5;
  color: #333;
}

.tab-btn.active {
  background: #f0f7ff;
  color: #2563eb;
  font-weight: 500;
  border-right: 3px solid #2563eb;
}

.settings-content {
  flex: 1;
  padding: 2rem;
}

.settings-section h3 {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 1.5rem;
  color: #1a1a1a;
}

.form-group {
  margin-bottom: 1.5rem;
}

.form-label {
  display: block;
  font-weight: 500;
  margin-bottom: 0.5rem;
  color: #333;
}

.checkbox-label {
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.checkbox-label input[type='checkbox'] {
  margin-right: 0.5rem;
  width: 18px;
  height: 18px;
  cursor: pointer;
}

.checkbox-label span {
  font-size: 0.95rem;
  color: #333;
}

.form-select {
  width: 100%;
  max-width: 300px;
  padding: 0.5rem;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 0.95rem;
}

.hint {
  margin-top: 0.25rem;
  font-size: 0.85rem;
  color: #999;
}

.path-text {
  font-family: monospace;
  background: #f5f5f5;
  padding: 0.5rem;
  border-radius: 4px;
  margin-bottom: 0.5rem;
  color: #666;
}

.status-text {
  margin-top: 0.5rem;
  font-size: 0.9rem;
  color: #666;
}

.btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 4px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-primary {
  background: #2563eb;
  color: white;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-secondary {
  background: #f5f5f5;
  color: #333;
  border: 1px solid #d1d5db;
}

.btn-secondary:hover {
  background: #e5e5e5;
}

.btn-danger {
  background: #dc2626;
  color: white;
}

.btn-danger:hover {
  background: #b91c1c;
}

.settings-footer {
  margin-top: 3rem;
  padding-top: 2rem;
  border-top: 1px solid #e5e5e5;
}
</style>
