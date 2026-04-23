<script lang="ts" setup>
import type { Message } from '#/api/core/message';

import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { MailCheck, Trash2 } from '@vben/icons';
import { $t } from '@vben/locales';

import { ElButton, ElMessage, ElMessageBox, ElTag } from 'element-plus';

import {
  clearReadMessagesApi,
  deleteMessageApi,
  getMessageListApi,
  getUnreadCountApi,
  markAllAsReadApi,
  markAsReadApi,
} from '#/api/core/message';
import { useZqTable } from '#/components/zq-table';

defineOptions({ name: 'MessageList' });

// 消息类型映射
type TagType = 'danger' | 'info' | 'primary' | 'success' | 'warning';
const typeMap: Record<string, { label: string; type: TagType }> = {
  system: { label: $t('message.typeMap.system'), type: 'info' },
  workflow: { label: $t('message.typeMap.workflow'), type: 'primary' },
  todo: { label: $t('message.typeMap.todo'), type: 'warning' },
  announcement: { label: $t('message.typeMap.announcement'), type: 'success' },
};

// 未读数量
const unreadCount = ref(0);

// 列表 API
const fetchMessageList = async (params: any) => {
  const res = await getMessageListApi({
    page: params.page.currentPage,
    pageSize: params.page.pageSize,
    msg_type: params.form?.msg_type,
    status: params.form?.status,
  });
  // 过滤掉公告类型的消息，公告在公告管理页面单独显示
  const filteredItems = (res.items || []).filter(
    (msg) => msg.msg_type !== 'announcement',
  );
  return {
    items: filteredItems,
    total: res.total,
  };
};

// 使用 ZqTable
const [Grid, gridApi] = useZqTable({
  gridOptions: {
    columns: [
      {
        key: 'msg_type',
        title: $t('message.type'),
        width: 100,
        align: 'center',
        slots: { default: 'cell-msg_type' },
      },
      {
        key: 'title',
        title: $t('message.msgTitle'),
        minWidth: 200,
        slots: { default: 'cell-title' },
      },
      {
        key: 'content',
        title: $t('message.content'),
        minWidth: 300,
        showOverflow: true,
      },
      {
        key: 'created_at',
        title: $t('message.time'),
        width: 170,
        slots: { default: 'cell-created_at' },
      },
      {
        key: 'actions',
        title: $t('message.actions'),
        width: 150,
        fixed: 'right' as any,
        align: 'center',
        slots: { default: 'cell-actions' },
      },
    ],
    border: true,
    stripe: true,
    showSelection: true,
    showIndex: true,
    proxyConfig: {
      autoLoad: true,
      ajax: {
        query: fetchMessageList,
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
        fieldName: 'msg_type',
        label: $t('message.type'),
        component: 'Select',
        componentProps: {
          options: [
            { label: $t('message.typeMap.all'), value: '' },
            { label: $t('message.typeMap.system'), value: 'system' },
            { label: $t('message.typeMap.workflow'), value: 'workflow' },
            { label: $t('message.typeMap.todo'), value: 'todo' },
          ],
        },
      },
      {
        fieldName: 'status',
        label: $t('message.status'),
        component: 'Select',
        componentProps: {
          options: [
            { label: $t('message.statusMap.all'), value: '' },
            { label: $t('message.statusMap.unread'), value: 'unread' },
            { label: $t('message.statusMap.read'), value: 'read' },
          ],
        },
      },
    ],
    showCollapseButton: false,
    submitOnChange: false,
  },
});

const selectedItems = ref<Message[]>([]);

function handleSelectionChange(items: Record<string, any>[]) {
  selectedItems.value = items as Message[];
}

// 加载未读数量
async function loadUnreadCount() {
  try {
    const res = await getUnreadCountApi();
    unreadCount.value = res.total;
  } catch (error) {
    console.error('Failed to load unread count:', error);
    ElMessage.error($t('message.loadUnreadFailed'));
  }
}

// 标记已读
async function handleMarkRead(row: Message) {
  try {
    await markAsReadApi(row.id);
    ElMessage.success($t('message.markReadSuccess'));
    gridApi.reload();
    loadUnreadCount();
  } catch (error) {
    console.error('Failed to mark as read:', error);
    ElMessage.error($t('message.markReadFailed'));
  }
}

// 全部已读
async function handleMarkAllRead() {
  try {
    await ElMessageBox.confirm($t('message.markAllReadConfirm'), $t('message.markAllReadConfirmTitle'), {
      confirmButtonText: $t('common.confirm'),
      cancelButtonText: $t('common.cancel'),
      type: 'warning',
    });
    await markAllAsReadApi();
    ElMessage.success($t('message.markAllReadSuccess'));
    gridApi.reload();
    unreadCount.value = 0;
  } catch {
    // 用户取消
  }
}

// 删除消息
async function handleDelete(row: Message) {
  try {
    await ElMessageBox.confirm($t('message.deleteConfirm'), $t('message.deleteConfirmTitle'), {
      confirmButtonText: $t('common.confirm'),
      cancelButtonText: $t('common.cancel'),
      type: 'warning',
    });
    await deleteMessageApi(row.id);
    ElMessage.success($t('message.deleteSuccess'));
    gridApi.reload();
  } catch {
    // 用户取消
  }
}

// 清空已读
async function handleClearRead() {
  try {
    await ElMessageBox.confirm($t('message.clearReadConfirm'), $t('message.clearReadConfirmTitle'), {
      confirmButtonText: $t('common.confirm'),
      cancelButtonText: $t('common.cancel'),
      type: 'warning',
    });
    await clearReadMessagesApi();
    ElMessage.success($t('message.clearReadSuccess'));
    gridApi.reload();
  } catch {
    // 用户取消
  }
}

// 格式化日期
function formatDate(dateStr: string): string {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleString();
}

// 初始化
onMounted(() => {
  loadUnreadCount();
});
</script>

<template>
  <Page auto-content-height>
    <Grid @selection-change="handleSelectionChange">
      <!-- 工具栏操作 -->
      <template #toolbar-actions>
        <ElTag v-if="unreadCount > 0" type="danger" size="default" class="mr-2">
          {{ $t('message.unreadCount', { count: unreadCount }) }}
        </ElTag>
        <ElButton
          type="primary"
          :icon="MailCheck"
          :disabled="unreadCount === 0"
          @click="handleMarkAllRead"
        >
          {{ $t('message.markAllRead') }}
        </ElButton>
        <ElButton type="danger" plain :icon="Trash2" @click="handleClearRead">
          {{ $t('message.clearRead') }}
        </ElButton>
      </template>

      <!-- 类型列 -->
      <template #cell-msg_type="{ row }">
        <ElTag :type="typeMap[row.msg_type]?.type || 'info'" size="small">
          {{ typeMap[row.msg_type]?.label || row.msg_type }}
        </ElTag>
      </template>

      <!-- 标题列 -->
      <template #cell-title="{ row }">
        <div class="flex items-center gap-2">
          <span
            v-if="row.status === 'unread'"
            class="h-2 w-2 rounded-full bg-[var(--el-color-danger)]"
          ></span>
          <span :class="{ 'font-medium': row.status === 'unread' }">
            {{ row.title }}
          </span>
        </div>
      </template>

      <!-- 时间列 -->
      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <!-- 操作列 -->
      <template #cell-actions="{ row }">
        <ElButton
          v-if="row.status === 'unread'"
          link
          type="primary"
          :icon="MailCheck"
          @click="handleMarkRead(row)"
        >
          {{ $t('message.markRead') }}
        </ElButton>
        <ElButton link type="danger" :icon="Trash2" @click="handleDelete(row)">
          {{ $t('common.delete') }}
        </ElButton>
      </template>
    </Grid>
  </Page>
</template>
