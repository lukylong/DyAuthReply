<script lang="ts" setup>
import type {
  AnnouncementItem,
  NotificationItem,
} from '#/composables/useNotification';

import { computed, ref } from 'vue';

import { useVbenDrawer } from '@vben/common-ui';
import { Mail, MailCheck, Megaphone, Trash2 } from '@vben/icons';

import {
  ElButton,
  ElEmpty,
  ElScrollbar,
  ElTabPane,
  ElTabs,
  ElTooltip,
} from 'element-plus';

interface Props {
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

defineOptions({ name: 'NotificationDrawer' });

const props = withDefaults(defineProps<Props>(), {
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

const [Drawer] = useVbenDrawer();

const currentTab = ref(props.activeTab);

const messageTabLabel = computed(
  () =>
    `消息 ${props.messageUnreadCount > 0 ? `(${props.messageUnreadCount})` : ''}`,
);

const announcementTabLabel = computed(
  () =>
    `公告 ${props.announcementUnreadCount > 0 ? `(${props.announcementUnreadCount})` : ''}`,
);

function handleTabChange(value: number | string) {
  currentTab.value = value as 'announcement' | 'message';
  emit('update:activeTab', currentTab.value);
}

function handleViewAll() {
  emit('viewAll');
}

function handleMakeAll() {
  emit('makeAll');
}

function handleClear() {
  emit('clear');
}

function handleMessageClick(item: NotificationItem) {
  emit('readMessage', item);
}

function handleAnnouncementClick(item: AnnouncementItem) {
  emit('readAnnouncement', item);
}

// 优先级样式
function getPriorityClass(priority: number): string {
  if (priority === 2) return 'border-l-4 border-l-red-500';
  if (priority === 1) return 'border-l-4 border-l-orange-500';
  return '';
}
</script>

<template>
  <div>
    <Drawer title="消息中心" class="sm:max-w-md" :footer="false">
      <template #extra>
        <div v-if="currentTab === 'message'" class="flex items-center gap-2">
          <ElTooltip content="全部已读" placement="bottom">
            <ElButton
              :disabled="notifications.length <= 0"
              link
              @click="handleMakeAll"
            >
              <MailCheck class="size-4" />
            </ElButton>
          </ElTooltip>
          <ElTooltip content="清空已读" placement="bottom">
            <ElButton
              :disabled="notifications.length <= 0"
              link
              @click="handleClear"
            >
              <Trash2 class="size-4" />
            </ElButton>
          </ElTooltip>
        </div>
      </template>

      <div class="flex h-full flex-col">
        <!-- Tab 切换 -->
        <ElTabs v-model="currentTab" class="mb-4" @tab-change="handleTabChange">
          <ElTabPane :label="messageTabLabel" name="message" />
          <ElTabPane :label="announcementTabLabel" name="announcement" />
        </ElTabs>

        <!-- 消息列表 -->
        <template v-if="currentTab === 'message'">
          <ElScrollbar v-if="notifications.length > 0" class="flex-1">
            <ul class="flex w-full flex-col gap-2">
              <template v-for="item in notifications" :key="item.id">
                <li
                  class="relative flex w-full cursor-pointer items-start gap-3 rounded-lg border border-[var(--el-border-color)] p-3 transition-colors hover:bg-[var(--el-fill-color-light)]"
                  @click="handleMessageClick(item)"
                >
                  <span
                    v-if="!item.isRead"
                    class="absolute right-2 top-2 h-2 w-2 rounded-full bg-[var(--el-color-danger)]"
                  ></span>

                  <span
                    class="relative flex h-10 w-10 shrink-0 overflow-hidden rounded-full"
                  >
                    <img
                      :src="item.avatar"
                      class="aspect-square h-full w-full object-cover"
                    />
                  </span>
                  <div class="flex flex-1 flex-col gap-1 leading-none">
                    <p class="text-sm font-medium">{{ item.title }}</p>
                    <p
                      class="line-clamp-2 text-xs text-[var(--el-text-color-secondary)]"
                    >
                      {{ item.message }}
                    </p>
                    <p class="text-xs text-[var(--el-text-color-placeholder)]">
                      {{ item.date }}
                    </p>
                  </div>
                </li>
              </template>
            </ul>
          </ElScrollbar>

          <template v-else>
            <div class="flex flex-1 items-center justify-center">
              <ElEmpty description="暂无消息">
                <template #image>
                  <Mail
                    class="size-16 text-[var(--el-text-color-placeholder)]"
                  />
                </template>
              </ElEmpty>
            </div>
          </template>
        </template>

        <!-- 公告列表 -->
        <template v-else>
          <ElScrollbar v-if="announcements.length > 0" class="flex-1">
            <ul class="flex w-full flex-col gap-2">
              <template v-for="item in announcements" :key="item.id">
                <li
                  class="relative flex w-full cursor-pointer flex-col gap-2 rounded-lg border border-[var(--el-border-color)] p-3 transition-colors hover:bg-[var(--el-fill-color-light)]"
                  :class="getPriorityClass(item.priority)"
                  @click="handleAnnouncementClick(item)"
                >
                  <span
                    v-if="!item.isRead"
                    class="absolute right-2 top-2 h-2 w-2 rounded-full bg-[var(--el-color-danger)]"
                  ></span>

                  <div class="flex items-center gap-2">
                    <span
                      v-if="item.isTop"
                      class="rounded bg-[var(--el-color-danger)] px-1.5 py-0.5 text-xs text-white"
                    >
                      置顶
                    </span>
                    <span
                      v-if="item.priority === 2"
                      class="rounded bg-[var(--el-color-danger)] px-1.5 py-0.5 text-xs text-white"
                    >
                      紧急
                    </span>
                    <span
                      v-else-if="item.priority === 1"
                      class="rounded bg-[var(--el-color-warning)] px-1.5 py-0.5 text-xs text-white"
                    >
                      重要
                    </span>
                    <p class="flex-1 truncate text-sm font-medium">
                      {{ item.title }}
                    </p>
                  </div>
                  <p
                    class="line-clamp-2 text-xs text-[var(--el-text-color-secondary)]"
                  >
                    {{ item.summary || item.content }}
                  </p>
                  <div
                    class="flex items-center justify-between text-xs text-[var(--el-text-color-placeholder)]"
                  >
                    <span>{{ item.publisherName }}</span>
                    <span>{{ item.date }}</span>
                  </div>
                </li>
              </template>
            </ul>
          </ElScrollbar>

          <template v-else>
            <div class="flex flex-1 items-center justify-center">
              <ElEmpty description="暂无公告">
                <template #image>
                  <Megaphone
                    class="size-16 text-[var(--el-text-color-placeholder)]"
                  />
                </template>
              </ElEmpty>
            </div>
          </template>
        </template>

        <!-- 底部操作 -->
        <!-- <div class="mt-4 flex justify-center border-t border-[var(--el-border-color)] pt-4">
          <ElButton type="primary" @click="handleViewAll">
            查看全部
          </ElButton>
        </div> -->
      </div>
    </Drawer>
  </div>
</template>
