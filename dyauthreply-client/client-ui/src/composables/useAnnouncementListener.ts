/**
 * 客户端公告监听
 * 定期轮询获取最新公告并显示系统通知
 */
import { ref, onMounted, onUnmounted, watchEffect } from 'vue';
import { useClientSettings } from './useClientSettings';

export interface ClientAnnouncement {
  id: string;
  title: string;
  content: string;
  level: 'info' | 'warning' | 'urgent';
  publish_time: string | null;
  expire_time: string | null;
}

const announcements = ref<ClientAnnouncement[]>([]);
const unreadCount = ref(0);

// 已读公告 ID 集合（存储在 localStorage）
const SEEN_KEY = 'seen_announcements';
const seenIds = ref<Set<string>>(loadSeenIds());

function loadSeenIds(): Set<string> {
  try {
    const stored = localStorage.getItem(SEEN_KEY);
    if (stored) {
      return new Set(JSON.parse(stored));
    }
  } catch (e) {
    console.error('Failed to load seen announcements:', e);
  }
  return new Set();
}

function saveSeenIds() {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...seenIds.value]));
  } catch (e) {
    console.error('Failed to save seen announcements:', e);
  }
}

function markAsSeen(id: string) {
  seenIds.value.add(id);
  saveSeenIds();
  updateUnreadCount();
}

function updateUnreadCount() {
  unreadCount.value = announcements.value.filter((a) => !seenIds.value.has(a.id)).length;
}

async function fetchAnnouncements(): Promise<void> {
  try {
    // 获取后端地址（从环境变量或配置）
    const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
    const response = await fetch(`${baseUrl}/api/client/v1/announcements?limit=10`);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    announcements.value = data.data || data; // 兼容不同的响应格式

    updateUnreadCount();

    // 显示未读的通知
    showUnreadNotifications();
  } catch (error) {
    console.error('Failed to fetch announcements:', error);
  }
}

async function showUnreadNotifications(): Promise<void> {
  const { settings } = useClientSettings();

  if (!settings.value.notification.system_enabled || !settings.value.notification.announcement_enabled) {
    return;
  }

  // 检测是否在 Tauri 环境
  const isTauri = '__TAURI__' in window;

  for (const announcement of announcements.value) {
    if (seenIds.value.has(announcement.id)) {
      continue;
    }

    try {
      if (isTauri) {
        // Tauri 环境：使用 Tauri Notification API
        const { sendNotification, isPermissionGranted, requestPermission } = await import(
          '@tauri-apps/plugin-notification'
        );

        let permissionGranted = await isPermissionGranted();
        if (!permissionGranted) {
          const permission = await requestPermission();
          permissionGranted = permission === 'granted';
        }

        if (permissionGranted) {
          sendNotification({
            title: announcement.title,
            body: announcement.content,
          });
        }
      } else {
        // 浏览器环境：降级为浏览器 Notification API
        if (Notification.permission === 'granted') {
          new Notification(announcement.title, {
            body: announcement.content,
          });
        } else if (Notification.permission !== 'denied') {
          const permission = await Notification.requestPermission();
          if (permission === 'granted') {
            new Notification(announcement.title, {
              body: announcement.content,
            });
          }
        }
      }

      // 标记为已读
      markAsSeen(announcement.id);
    } catch (error) {
      console.error('Failed to show notification:', error);
    }
  }
}

export function useAnnouncementListener() {
  const { settings } = useClientSettings();
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  // 启动时获取一次
  onMounted(() => {
    if (settings.value.notification.announcement_enabled) {
      fetchAnnouncements();
    }
  });

  // 定期轮询（5分钟一次）
  watchEffect((onCleanup) => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }

    if (!settings.value.notification.announcement_enabled) {
      return;
    }

    pollTimer = setInterval(() => {
      fetchAnnouncements();
    }, 5 * 60 * 1000); // 5分钟

    onCleanup(() => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    });
  });

  onUnmounted(() => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  });

  return {
    announcements,
    unreadCount,
    fetchAnnouncements,
    markAsSeen,
  };
}
