<script lang="ts" setup>
import type { UserAnnouncement } from '#/api/core/announcement';

import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { $t } from '@vben/locales';
import { Eye } from '@vben/icons';

import { ElButton, ElDialog, ElTag } from 'element-plus';

import {
  getUnreadAnnouncementCountApi,
  getUserAnnouncementListApi,
  markAnnouncementReadApi,
} from '#/api/core/announcement';
import { useZqTable } from '#/components/zq-table';

defineOptions({ name: 'AnnouncementList' });

// 未读数量
const unreadCount = ref(0);

// 详情弹窗
const detailVisible = ref(false);
const currentAnnouncement = ref<null | UserAnnouncement>(null);

// 列表 API
const fetchAnnouncementList = async (params: any) => {
  const res = await getUserAnnouncementListApi({
    page: params.page.currentPage,
    pageSize: params.page.pageSize,
    unread_only: params.form?.unread_only === 'true',
  });
  return {
    items: res.items,
    total: res.total,
  };
};

// 使用 ZqTable
const [Grid, gridApi] = useZqTable({
  gridOptions: {
    columns: [
      {
        key: 'priority',
        title: $t('announcement.priority'),
        width: 80,
        align: 'center',
        slots: { default: 'cell-priority' },
      },
      {
        key: 'title',
        title: $t('announcement.title'),
        minWidth: 200,
        slots: { default: 'cell-title' },
      },
      {
        key: 'summary',
        title: $t('announcement.summary'),
        minWidth: 300,
        showOverflow: true,
      },
      { key: 'publisher_name', title: $t('announcement.publisher'), width: 120 },
      {
        key: 'publish_time',
        title: $t('announcement.publishTime'),
        width: 170,
        slots: { default: 'cell-publish_time' },
      },
      {
        key: 'actions',
        title: $t('announcement.actions'),
        width: 100,
        fixed: 'right',
        align: 'center',
        slots: { default: 'cell-actions' },
      },
    ],
    border: true,
    stripe: true,
    showIndex: true,
    proxyConfig: {
      autoLoad: true,
      ajax: {
        query: fetchAnnouncementList,
      },
    },
    pagerConfig: {
      enabled: true,
      pageSize: 20,
    },
    toolbarConfig: {
      search: true,
      refresh: true,
      zoom: true,
      custom: true,
    },
  },
  formOptions: {
    schema: [
      {
        fieldName: 'unread_only',
        label: $t('announcement.unreadOnly'),
        component: 'Select',
        componentProps: {
          options: [
            { label: $t('announcement.unreadOnlyAll'), value: '' },
            { label: $t('announcement.unreadOnlyUnread'), value: 'true' },
          ],
        },
      },
    ],
    showCollapseButton: false,
    submitOnChange: false,
  },
});

// 加载未读数量
async function loadUnreadCount() {
  try {
    const res = await getUnreadAnnouncementCountApi();
    unreadCount.value = res.count;
  } catch (error) {
    console.error('加载未读数量失败:', error);
  }
}

// 查看公告详情
async function handleView(row: UserAnnouncement) {
  currentAnnouncement.value = row;
  detailVisible.value = true;

  // 标记已读
  if (!row.is_read) {
    try {
      await markAnnouncementReadApi(row.id);
      row.is_read = true;
      unreadCount.value = Math.max(0, unreadCount.value - 1);
    } catch (error) {
      console.error('标记已读失败:', error);
    }
  }
}

// 格式化日期
function formatDate(dateStr: string): string {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleString();
}

// 优先级标签
type TagType = 'danger' | 'info' | 'warning';
function getPriorityTag(priority: number): { label: string; type: TagType } {
  if (priority === 2) return { label: $t('announcement.priorityUrgent'), type: 'danger' };
  if (priority === 1) return { label: $t('announcement.priorityImportant'), type: 'warning' };
  return { label: $t('announcement.priorityNormal'), type: 'info' };
}

// 初始化
onMounted(() => {
  loadUnreadCount();
});
</script>

<template>
  <Page auto-content-height>
    <Grid>
      <!-- 工具栏操作 -->
      <template #toolbar-actions>
        <ElTag v-if="unreadCount > 0" type="danger" size="default" class="mr-2">
          {{ $t('announcement.unreadCount', { count: unreadCount }) }}
        </ElTag>
      </template>

      <!-- 优先级列 -->
      <template #cell-priority="{ row }">
        <ElTag :type="getPriorityTag(row.priority).type" size="small">
          {{ getPriorityTag(row.priority).label }}
        </ElTag>
      </template>

      <!-- 标题列 -->
      <template #cell-title="{ row }">
        <div class="flex items-center gap-2">
          <ElTag v-if="row.is_top" type="danger" size="small">{{ $t('announcement.topTag') }}</ElTag>
          <span
            v-if="!row.is_read"
            class="h-2 w-2 rounded-full bg-[var(--el-color-danger)]"
          ></span>
          <span
            class="cursor-pointer hover:text-[var(--el-color-primary)]"
            :class="{ 'font-medium': !row.is_read }"
            @click="handleView(row)"
          >
            {{ row.title }}
          </span>
        </div>
      </template>

      <!-- 发布时间列 -->
      <template #cell-publish_time="{ row }">
        {{ formatDate(row.publish_time) }}
      </template>

      <!-- 操作列 -->
      <template #cell-actions="{ row }">
        <ElButton link type="primary" :icon="Eye" @click="handleView(row)">
          {{ $t('announcement.viewButton') }}
        </ElButton>
      </template>
    </Grid>

    <!-- 公告详情弹窗 -->
    <ElDialog
      v-model="detailVisible"
      :title="currentAnnouncement?.title"
      width="700px"
      destroy-on-close
    >
      <div v-if="currentAnnouncement" class="announcement-detail">
        <div
          class="mb-4 flex items-center gap-4 text-sm text-[var(--el-text-color-secondary)]"
        >
          <span>{{ $t('announcement.publisherLabel') }}：{{ currentAnnouncement.publisher_name }}</span>
          <span>{{ $t('announcement.publishTimeLabel') }}：{{
              formatDate(currentAnnouncement.publish_time || '')
            }}</span>
          <ElTag
            :type="getPriorityTag(currentAnnouncement.priority).type"
            size="small"
          >
            {{ getPriorityTag(currentAnnouncement.priority).label }}
          </ElTag>
        </div>
        <div
          class="prose max-w-none"
          v-html="currentAnnouncement.content"
        ></div>
      </div>
    </ElDialog>
  </Page>
</template>

<style scoped>
.announcement-detail :deep(img) {
  max-width: 100%;
}
</style>
