/**
 * 消息通知 Composable
 * 集成 WebSocket 实时推送、消息和公告 API
 */
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useAccessStore } from '@vben/stores';

import {
  getUnreadAnnouncementCountApi,
  getUserAnnouncementListApi,
  markAnnouncementReadApi,
} from '#/api/core/announcement';
import {
  clearReadMessagesApi,
  getMessageListApi,
  getUnreadCountApi,
  markAllAsReadApi,
  markAsReadApi,
} from '#/api/core/message';

// 通知项类型
export interface NotificationItem {
  id: string;
  avatar: string;
  title: string;
  message: string;
  date: string;
  isRead: boolean;
  linkType?: string;
  linkId?: string;
  priority?: number;
  isTop?: boolean;
}

// 公告项类型
export interface AnnouncementItem {
  id: string;
  title: string;
  summary: string;
  content: string;
  date: string;
  isRead: boolean;
  priority: number;
  isTop: boolean;
  publisherName: string;
}

// WebSocket 连接状态
const wsConnected = ref(false);
const wsInstance = ref<null | WebSocket>(null);

// 消息数据
const notifications = ref<NotificationItem[]>([]);
const messageUnreadCount = ref(0);
const unreadByType = ref<Record<string, number>>({});

// 公告数据
const announcements = ref<AnnouncementItem[]>([]);
const announcementUnreadCount = ref(0);

// 当前激活的 Tab
const activeTab = ref<'announcement' | 'message'>('message');

// 消息类型图标映射
const typeAvatarMap: Record<string, string> = {
  system: 'https://avatar.vercel.sh/system?text=SYS',
  workflow: 'https://avatar.vercel.sh/workflow?text=WF',
  todo: 'https://avatar.vercel.sh/todo?text=TD',
  announcement: 'https://avatar.vercel.sh/announcement?text=AN',
};

export function useNotification() {
  const router = useRouter();
  const accessStore = useAccessStore();

  // 总未读数量
  const totalUnreadCount = computed(
    () => messageUnreadCount.value + announcementUnreadCount.value,
  );

  // 是否显示红点
  const showDot = computed(() => totalUnreadCount.value > 0);

  // 加载消息列表（排除公告类型，公告单独在公告 Tab 显示）
  async function loadMessages() {
    try {
      const res = await getMessageListApi({ page: 1, pageSize: 10 });
      // 过滤掉公告类型的消息，公告在公告 Tab 单独显示
      const filteredItems = (res.items || []).filter(
        (msg) => msg.msg_type !== 'announcement',
      );
      notifications.value = filteredItems.map((msg) => ({
        id: msg.id,
        avatar: typeAvatarMap[msg.msg_type] ?? typeAvatarMap.system!,
        title: msg.title,
        message: msg.content,
        date: formatDate(msg.created_at),
        isRead: msg.status === 'read',
        linkType: msg.link_type,
        linkId: msg.link_id,
      }));
    } catch (error) {
      console.error('加载消息失败:', error);
    }
  }

  // 加载未读数量（排除公告类型）
  async function loadUnreadCount() {
    try {
      const res = await getUnreadCountApi();
      // 排除公告类型的未读数量，公告未读数单独计算
      const announcementCount = res.by_type?.announcement || 0;
      messageUnreadCount.value = res.total - announcementCount;
      unreadByType.value = res.by_type;
    } catch (error) {
      console.error('加载未读数量失败:', error);
    }
  }

  // 加载公告列表
  async function loadAnnouncements() {
    try {
      const res = await getUserAnnouncementListApi({ page: 1, pageSize: 10 });
      announcements.value = (res.items || []).map((item) => ({
        id: item.id,
        title: item.title,
        summary: item.summary,
        content: item.content,
        date: formatDate(item.publish_time || ''),
        isRead: item.is_read,
        priority: item.priority,
        isTop: item.is_top,
        publisherName: item.publisher_name,
      }));
    } catch (error) {
      console.error('加载公告失败:', error);
    }
  }

  // 加载公告未读数量
  async function loadAnnouncementUnreadCount() {
    try {
      const res = await getUnreadAnnouncementCountApi();
      announcementUnreadCount.value = res.count;
    } catch (error) {
      console.error('加载公告未读数量失败:', error);
    }
  }

  // 标记消息已读
  async function markAsRead(item: NotificationItem) {
    if (item.isRead) {
      handleNavigate(item);
      return;
    }

    try {
      await markAsReadApi(item.id as string);
      item.isRead = true;
      messageUnreadCount.value = Math.max(0, messageUnreadCount.value - 1);
      handleNavigate(item);
    } catch (error) {
      console.error('标记已读失败:', error);
    }
  }

  // 标记公告已读
  async function markAnnouncementAsRead(item: AnnouncementItem) {
    if (item.isRead) {
      viewAnnouncementDetail(item);
      return;
    }

    try {
      await markAnnouncementReadApi(item.id);
      item.isRead = true;
      announcementUnreadCount.value = Math.max(
        0,
        announcementUnreadCount.value - 1,
      );
      viewAnnouncementDetail(item);
    } catch (error) {
      console.error('标记公告已读失败:', error);
    }
  }

  // 标记全部消息已读
  async function markAllAsRead() {
    try {
      await markAllAsReadApi();
      notifications.value.forEach((item) => (item.isRead = true));
      messageUnreadCount.value = 0;
      unreadByType.value = {};
    } catch (error) {
      console.error('标记全部已读失败:', error);
    }
  }

  // 清空已读消息
  async function clearReadMessages() {
    try {
      await clearReadMessagesApi();
      // 重新加载消息列表和未读数量
      await loadMessages();
      await loadUnreadCount();
    } catch (error) {
      console.error('清空已读消息失败:', error);
    }
  }

  // 跳转到消息关联页面
  function handleNavigate(item: NotificationItem) {
    const linkType = item.linkType;
    const linkId = item.linkId;

    if (!linkType || !linkId) return;

    // 根据关联类型跳转
    switch (linkType) {
      case 'announcement': {
        router.push(`/message/announcement-list`);

        break;
      }
      case 'workflow_instance': {
        router.push(`/workflow/instance/${linkId}`);

        break;
      }
      case 'workflow_task': {
        router.push(`/workflow/task/${linkId}`);

        break;
      }
      // No default
    }
  }

  // 查看公告详情
  function viewAnnouncementDetail(_item: AnnouncementItem) {
    // 跳转到公告列表页
    router.push(`/message/announcement-list`);
  }

  // 查看全部（根据当前 Tab 跳转）
  function viewAllMessages() {
    if (activeTab.value === 'announcement') {
      router.push('/message/announcement-list');
    } else {
      router.push('/message/list');
    }
  }

  // 连接 WebSocket
  function connectWebSocket() {
    const token = accessStore.accessToken;
    if (!token) return;

    // 构建 WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/notification/?token=${token}`;

    try {
      wsInstance.value = new WebSocket(wsUrl);

      wsInstance.value.addEventListener('open', () => {
        wsConnected.value = true;
        console.log('WebSocket 已连接');
        // 发送订阅消息
        wsInstance.value?.send(JSON.stringify({ type: 'subscribe' }));
      });

      wsInstance.value.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          handleWebSocketMessage(data);
        } catch (error) {
          console.error('解析 WebSocket 消息失败:', error);
        }
      };

      wsInstance.value.addEventListener('close', () => {
        wsConnected.value = false;
        console.log('WebSocket 已断开');
        // 5秒后重连
        setTimeout(connectWebSocket, 5000);
      });

      wsInstance.value.onerror = (error) => {
        console.error('WebSocket 错误:', error);
      };
    } catch (error) {
      console.error('WebSocket 连接失败:', error);
    }
  }

  // 处理 WebSocket 消息
  function handleWebSocketMessage(data: any) {
    if (data.type === 'notification') {
      // 收到新通知
      const msgData = data.data;
      const newNotification: NotificationItem = {
        id: msgData.id,
        avatar: typeAvatarMap[msgData.msg_type] ?? typeAvatarMap.system!,
        title: msgData.title,
        message: msgData.content,
        date: '刚刚',
        isRead: false,
        linkType: msgData.link_type,
        linkId: msgData.link_id,
      };

      // 添加到列表头部
      notifications.value.unshift(newNotification);
      // 只保留最近10条
      if (notifications.value.length > 10) {
        notifications.value.pop();
      }
      // 更新未读数量
      messageUnreadCount.value += 1;
    }
  }

  // 断开 WebSocket
  function disconnectWebSocket() {
    if (wsInstance.value) {
      wsInstance.value.close();
      wsInstance.value = null;
    }
  }

  // 格式化日期
  function formatDate(dateStr: string): string {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();

    if (diff < 60_000) return '刚刚';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟前`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`;
    if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}天前`;

    return date.toLocaleDateString();
  }

  // 初始化
  function init() {
    loadMessages();
    loadUnreadCount();
    loadAnnouncements();
    loadAnnouncementUnreadCount();
    connectWebSocket();
  }

  // 清理
  function cleanup() {
    disconnectWebSocket();
  }

  return {
    // 消息
    notifications,
    messageUnreadCount,
    unreadByType,
    // 公告
    announcements,
    announcementUnreadCount,
    // 通用
    activeTab,
    totalUnreadCount,
    showDot,
    wsConnected,
    // 方法
    loadMessages,
    loadUnreadCount,
    loadAnnouncements,
    loadAnnouncementUnreadCount,
    markAsRead,
    markAnnouncementAsRead,
    markAllAsRead,
    clearReadMessages,
    viewAllMessages,
    init,
    cleanup,
  };
}
