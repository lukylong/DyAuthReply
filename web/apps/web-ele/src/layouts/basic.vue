<script lang="ts" setup>
import { computed, onMounted, onUnmounted, watch } from 'vue';

import { AuthenticationLoginExpiredModal } from '@vben/common-ui';
import { VBEN_DOC_URL, VBEN_GITHUB_URL } from '@vben/constants';
import { useWatermark } from '@vben/hooks';
import { BookOpenText, CircleHelp, SvgGithubIcon } from '@vben/icons';
import { BasicLayout, LockScreen, UserDropdown } from '@vben/layouts';
import { preferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';
import { openWindow } from '@vben/utils';

import { getFileStreamUrl } from '#/api/core/file';
import NotificationPopup from '#/components/notification/NotificationPopup.vue';
import { useNotification } from '#/composables/useNotification';
import { $t } from '#/locales';
import { useAuthStore } from '#/store';
import LoginForm from '#/views/_core/authentication/login.vue';

// 使用消息通知 composable
const {
  notifications,
  messageUnreadCount,
  announcements,
  announcementUnreadCount,
  activeTab,
  showDot,
  markAsRead,
  markAnnouncementAsRead,
  markAllAsRead,
  clearReadMessages,
  viewAllMessages,
  init: initNotification,
  cleanup: cleanupNotification,
} = useNotification();

const userStore = useUserStore();
const authStore = useAuthStore();
const accessStore = useAccessStore();
const { destroyWatermark, updateWatermark } = useWatermark();

// 初始化消息通知
onMounted(() => {
  initNotification();
});

onUnmounted(() => {
  cleanupNotification();
});

const menus = computed(() => [
  {
    handler: () => {
      openWindow(VBEN_DOC_URL, {
        target: '_blank',
      });
    },
    icon: BookOpenText,
    text: $t('ui.widgets.document'),
  },
  {
    handler: () => {
      openWindow(VBEN_GITHUB_URL, {
        target: '_blank',
      });
    },
    icon: SvgGithubIcon,
    text: 'GitHub',
  },
  {
    handler: () => {
      openWindow(`${VBEN_GITHUB_URL}/issues`, {
        target: '_blank',
      });
    },
    icon: CircleHelp,
    text: $t('ui.widgets.qa'),
  },
]);

const avatar = computed(() => {
  const avatarPath = userStore.userInfo?.avatar;
  if (avatarPath) {
    return getFileStreamUrl(avatarPath);
  }
  return preferences.app.defaultAvatar;
});

async function handleLogout() {
  await authStore.logout(false);
}

function handleNoticeClear() {
  clearReadMessages();
}

function handleMakeAll() {
  markAllAsRead();
}

function handleNoticeRead(item: any) {
  markAsRead(item);
}

function handleAnnouncementRead(item: any) {
  markAnnouncementAsRead(item);
}

function handleViewAll() {
  viewAllMessages();
}

function handleTabChange(tab: 'announcement' | 'message') {
  activeTab.value = tab;
}
watch(
  () => ({
    enable: preferences.app.watermark,
    content: preferences.app.watermarkContent,
  }),
  async ({ enable, content }) => {
    if (enable) {
      await updateWatermark({
        content:
          content ||
          `${userStore.userInfo?.username} - ${userStore.userInfo?.realName}`,
      });
    } else {
      destroyWatermark();
    }
  },
  {
    immediate: true,
  },
);
</script>

<template>
  <BasicLayout @clear-preferences-and-logout="handleLogout">
    <template #user-dropdown>
      <UserDropdown
        :avatar
        :menus
        :text="userStore.userInfo?.realName"
        description="jiangzhikj@outlook.com"
        tag-text="Pro"
        @logout="handleLogout"
      />
    </template>
    <template #notification>
      <NotificationPopup
        :dot="showDot"
        :notifications="notifications"
        :message-unread-count="messageUnreadCount"
        :announcements="announcements"
        :announcement-unread-count="announcementUnreadCount"
        :active-tab="activeTab"
        @clear="handleNoticeClear"
        @make-all="handleMakeAll"
        @read-message="handleNoticeRead"
        @read-announcement="handleAnnouncementRead"
        @view-all="handleViewAll"
        @update:active-tab="handleTabChange"
      />
    </template>
    <template #extra>
      <AuthenticationLoginExpiredModal
        v-model:open="accessStore.loginExpired"
        :avatar
      >
        <LoginForm />
      </AuthenticationLoginExpiredModal>
    </template>
    <template #lock-screen>
      <LockScreen :avatar @to-login="handleLogout" />
    </template>
  </BasicLayout>
</template>
