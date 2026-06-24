/**
 * 版本更新检测
 * 支持启动检查、定时检查、手动检查
 */
import { ref, onMounted, watchEffect, onUnmounted } from 'vue';
import { useClientSettings } from './useClientSettings';
import { checkAppUpdate } from '../api/client';

export interface UpdateInfo {
  current_version: string;
  latest_version: string;
  has_update: boolean;
  mandatory: boolean;
  notes: string;
  download_url: string;
  release_page: string;
}

const hasUpdate = ref(false);
const latestVersion = ref('');
const updateInfo = ref<UpdateInfo | null>(null);
const isChecking = ref(false);
const checkError = ref('');
const lastCheckedAt = ref('');

function getIntervalMs(frequency: string): number {
  switch (frequency) {
    case 'startup':
      return 0; // 仅启动时检查
    case '6h':
      return 6 * 60 * 60 * 1000; // 6小时
    case 'daily':
      return 24 * 60 * 60 * 1000; // 24小时
    case 'weekly':
      return 7 * 24 * 60 * 60 * 1000; // 7天
    case 'manual':
      return 0; // 不自动检查
    default:
      return 6 * 60 * 60 * 1000;
  }
}

async function checkUpdate(): Promise<void> {
  const { settings } = useClientSettings();

  if (!settings.value.version_update.enabled) {
    return;
  }

  isChecking.value = true;
  checkError.value = '';

  try {
    const result = await checkAppUpdate();
    updateInfo.value = result;
    hasUpdate.value = result.has_update;
    latestVersion.value = result.latest_version;

    // 更新最后检查时间
    const now = new Date().toISOString();
    settings.value.version_update.last_check_time = now;
    lastCheckedAt.value = now;

    // 如果有更新且不是用户已忽略的版本，显示提示
    if (
      result.has_update &&
      settings.value.version_update.dismissed_version !== result.latest_version
    ) {
      // 显示更新提示（由调用方处理）
      console.log('New version available:', result.latest_version);
    }
  } catch (error) {
    console.error('Failed to check update:', error);
    checkError.value = error instanceof Error ? error.message : String(error);
    updateInfo.value = null;
    hasUpdate.value = false;
  } finally {
    isChecking.value = false;
  }
}

function dismissUpdate(version: string): void {
  const { settings } = useClientSettings();
  settings.value.version_update.dismissed_version = version;
  hasUpdate.value = false;
}

export function useVersionUpdate() {
  const { settings } = useClientSettings();
  let intervalTimer: ReturnType<typeof setInterval> | null = null;

  // 启动时检查
  onMounted(() => {
    if (settings.value.version_update.enabled) {
      checkUpdate();
    }
  });

  // 定时检查
  watchEffect((onCleanup) => {
    if (intervalTimer) {
      clearInterval(intervalTimer);
      intervalTimer = null;
    }

    if (!settings.value.version_update.enabled) {
      return;
    }

    const frequency = settings.value.version_update.check_frequency;
    if (frequency === 'manual' || frequency === 'startup') {
      return;
    }

    const interval = getIntervalMs(frequency);
    if (interval > 0) {
      intervalTimer = setInterval(() => {
        checkUpdate();
      }, interval);

      onCleanup(() => {
        if (intervalTimer) {
          clearInterval(intervalTimer);
          intervalTimer = null;
        }
      });
    }
  });

  onUnmounted(() => {
    if (intervalTimer) {
      clearInterval(intervalTimer);
      intervalTimer = null;
    }
  });

  return {
    hasUpdate,
    latestVersion,
    updateInfo,
    isChecking,
    checkError,
    lastCheckedAt,
    checkUpdate,
    dismissUpdate,
  };
}
