<script lang="ts" setup>
import type {
  AnnouncementItem,
  NotificationItem,
} from '#/composables/useNotification';

import { computed } from 'vue';

import { useVbenDrawer } from '@vben/common-ui';
import { Bell } from '@vben/icons';

import { ElBadge } from 'element-plus';

import NotificationDrawer from './NotificationDrawer.vue';

interface Props {
  // 是否显示红点
  dot?: boolean;
  // 消息列表
  notifications?: NotificationItem[];
  // 消息未读数
  messageUnreadCount?: number;
  // 公告列表
  announcements?: AnnouncementItem[];
  // 公告未读数
  announcementUnreadCount?: number;
  // 当前激活的 Tab
  activeTab?: 'announcement' | 'message';
}

defineOptions({ name: 'NotificationPopup' });

const props = withDefaults(defineProps<Props>(), {
  dot: false,
  notifications: () => [],
  messageUnreadCount: 0,
  announcements: () => [],
  announcementUnreadCount: 0,
  activeTab: 'message',
});

const emit = defineEmits<{
  clear: [];
  makeAll: [];
  readAnnouncement: [AnnouncementItem];
  readMessage: [NotificationItem];
  'update:activeTab': ['announcement' | 'message'];
  viewAll: [];
}>();

// 使用 Drawer
const [Drawer, drawerApi] = useVbenDrawer({
  connectedComponent: NotificationDrawer,
});

// 传递给 Drawer 的属性
const drawerAttrs = computed(() => ({
  notifications: props.notifications,
  messageUnreadCount: props.messageUnreadCount,
  announcements: props.announcements,
  announcementUnreadCount: props.announcementUnreadCount,
  activeTab: props.activeTab,
}));

// 传递给 Drawer 的事件
const drawerListeners = {
  clear: () => emit('clear'),
  makeAll: () => emit('makeAll'),
  readMessage: (item: NotificationItem) => emit('readMessage', item),
  readAnnouncement: (item: AnnouncementItem) => emit('readAnnouncement', item),
  viewAll: () => {
    emit('viewAll');
    drawerApi.close();
  },
  'update:activeTab': (tab: 'announcement' | 'message') =>
    emit('update:activeTab', tab),
};

function openDrawer() {
  drawerApi.open();
}
</script>

<template>
  <div>
    <Drawer v-bind="drawerAttrs" v-on="drawerListeners" />

    <div class="notification-trigger" @click="openDrawer">
      <ElBadge :is-dot="dot" :hidden="!dot">
        <Bell
          class="size-5 cursor-pointer text-[var(--el-text-color-regular)] hover:text-[var(--el-color-primary)]"
        />
      </ElBadge>
    </div>
  </div>
</template>

<style scoped>
.notification-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  margin-right: 8px;
}
</style>
