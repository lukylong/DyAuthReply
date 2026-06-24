/**
 * 客户端设置管理
 * 保存在 localStorage，包含版本更新、通知、运行和隐私设置
 */
import { ref, watch } from 'vue';

export interface ClientSettings {
  version_update: {
    enabled: boolean;
    auto_download: boolean;
    check_frequency: 'startup' | '6h' | 'daily' | 'weekly' | 'manual';
    last_check_time?: string;
    dismissed_version?: string;
  };
  notification: {
    system_enabled: boolean;
    announcement_enabled: boolean;
  };
  runtime: {
    auto_start: boolean;
    minimize_to_tray: boolean;
    start_minimized: boolean;
  };
  privacy: {
    analytics_enabled: boolean;
  };
}

const DEFAULT_SETTINGS: ClientSettings = {
  version_update: {
    enabled: true,
    auto_download: false,
    check_frequency: '6h',
  },
  notification: {
    system_enabled: true,
    announcement_enabled: true,
  },
  runtime: {
    auto_start: false,
    minimize_to_tray: true,
    start_minimized: false,
  },
  privacy: {
    analytics_enabled: true, // 默认开启但暂不实际收集
  },
};

const STORAGE_KEY = 'client_settings';

const settings = ref<ClientSettings>(loadSettings());

function loadSettings(): ClientSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      // 合并默认值，确保新增字段有默认值
      return {
        version_update: { ...DEFAULT_SETTINGS.version_update, ...parsed.version_update },
        notification: { ...DEFAULT_SETTINGS.notification, ...parsed.notification },
        runtime: { ...DEFAULT_SETTINGS.runtime, ...parsed.runtime },
        privacy: { ...DEFAULT_SETTINGS.privacy, ...parsed.privacy },
      };
    }
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
  return JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
}

function saveSettings() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings.value));
  } catch (e) {
    console.error('Failed to save settings:', e);
  }
}

function resetSettings() {
  settings.value = JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
  saveSettings();
}

// 自动保存（防抖 500ms）
let saveTimer: ReturnType<typeof setTimeout> | null = null;
watch(
  settings,
  () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveSettings();
    }, 500);
  },
  { deep: true }
);

export function useClientSettings() {
  return {
    settings,
    saveSettings,
    resetSettings,
    DEFAULT_SETTINGS,
  };
}
