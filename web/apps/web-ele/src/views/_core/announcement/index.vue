<script lang="ts" setup>
import type {
  AnnouncementCreate,
  AnnouncementListItem,
} from '#/api/core/announcement';

import { reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { $t } from '@vben/locales';
import { Edit, Eye, MoreHorizontal, Play, Plus, Trash2 } from '@vben/icons';

import {
  ElButton,
  ElDatePicker,
  ElDialog,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  createAnnouncementApi,
  deleteAnnouncementApi,
  getAnnouncementDetailApi,
  getAnnouncementListApi,
  getReadStatsApi,
  publishAnnouncementApi,
  updateAnnouncementApi,
} from '#/api/core/announcement';
import { useZqTable } from '#/components/zq-table';

defineOptions({ name: 'AnnouncementManager' });

// 列表 API
const fetchAnnouncementList = async (params: any) => {
  const res = await getAnnouncementListApi({
    page: params.page.currentPage,
    pageSize: params.page.pageSize,
    keyword: params.form?.keyword,
    status: params.form?.status,
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
        key: 'title',
        title: $t('announcement.title'),
        minWidth: 200,
        slots: { default: 'cell-title' },
      },
      {
        key: 'status',
        title: $t('announcement.status'),
        width: 100,
        align: 'center',
        slots: { default: 'cell-status' },
      },
      {
        key: 'priority',
        title: $t('announcement.priority'),
        width: 80,
        align: 'center',
        slots: { default: 'cell-priority' },
      },
      {
        key: 'target_type',
        title: $t('announcement.targetType'),
        width: 100,
        align: 'center',
        slots: { default: 'cell-target_type' },
      },
      { key: 'read_count', title: $t('announcement.readCount'), width: 80, align: 'center' },
      { key: 'publisher_name', title: $t('announcement.publisher'), width: 100 },
      {
        key: 'publish_time',
        title: $t('announcement.publishTime'),
        width: 170,
        slots: { default: 'cell-publish_time' },
      },
      {
        key: 'actions',
        title: $t('announcement.actions'),
        width: 200,
        fixed: 'right',
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
        fieldName: 'keyword',
        label: $t('announcement.keyword'),
        component: 'Input',
        componentProps: {
          placeholder: $t('announcement.keywordPlaceholder'),
        },
      },
      {
        fieldName: 'status',
        label: $t('announcement.status'),
        component: 'Select',
        componentProps: {
          options: [
            { label: $t('announcement.statusAll'), value: '' },
            { label: $t('announcement.statusDraft'), value: 'draft' },
            { label: $t('announcement.statusPublished'), value: 'published' },
            { label: $t('announcement.statusExpired'), value: 'expired' },
          ],
        },
      },
    ],
    showCollapseButton: false,
    submitOnChange: false,
  },
});

// 状态映射
type TagType = 'danger' | 'info' | 'success' | 'warning';
const statusMap: Record<string, { label: string; type: TagType }> = {
  draft: { label: $t('announcement.statusDraft'), type: 'info' },
  published: { label: $t('announcement.statusPublished'), type: 'success' },
  expired: { label: $t('announcement.statusExpired'), type: 'warning' },
};

// 优先级映射
const priorityMap: Record<number, { label: string; type: TagType }> = {
  0: { label: $t('announcement.priorityNormal'), type: 'info' },
  1: { label: $t('announcement.priorityImportant'), type: 'warning' },
  2: { label: $t('announcement.priorityUrgent'), type: 'danger' },
};

// 接收范围映射
const targetTypeMap: Record<string, string> = {
  all: $t('announcement.targetTypeAll'),
  dept: $t('announcement.targetTypeDept'),
  role: $t('announcement.targetTypeRole'),
  user: $t('announcement.targetTypeUser'),
};

const selectedItems = ref<AnnouncementListItem[]>([]);

// 表单弹窗
const dialogVisible = ref(false);
const dialogTitle = ref('');
const isEdit = ref(false);
const editId = ref('');
const formRef = ref();
const formData = reactive<AnnouncementCreate>({
  title: '',
  content: '',
  summary: '',
  status: 'draft',
  priority: 0,
  is_top: false,
  target_type: 'all',
  target_ids: [],
  publish_time: undefined,
  expire_time: undefined,
});

// 阅读统计弹窗
const statsDialogVisible = ref(false);
const statsData = ref<{ readers: any[]; total_read: number }>({
  total_read: 0,
  readers: [],
});

function handleSelectionChange(items: Record<string, any>[]) {
  selectedItems.value = items as AnnouncementListItem[];
}

// 新增
function handleCreate() {
  isEdit.value = false;
  editId.value = '';
  dialogTitle.value = $t('announcement.createTitle');
  resetForm();
  dialogVisible.value = true;
}

// 编辑
async function handleEdit(row: AnnouncementListItem) {
  isEdit.value = true;
  editId.value = row.id;
  dialogTitle.value = $t('announcement.editTitle');

  const detail = await getAnnouncementDetailApi(row.id);

  Object.assign(formData, {
    title: detail.title,
    content: detail.content,
    summary: detail.summary,
    status: detail.status,
    priority: detail.priority,
    is_top: detail.is_top,
    target_type: detail.target_type,
    target_ids: detail.target_ids,
    publish_time: detail.publish_time,
    expire_time: detail.expire_time,
  });

  dialogVisible.value = true;
}

// 删除
async function handleDelete(row: AnnouncementListItem) {
  try {
    await ElMessageBox.confirm(
      $t('announcement.deleteConfirm', { title: row.title }),
      $t('announcement.deleteConfirmTitle'),
      {
        confirmButtonText: $t('common.confirm'),
        cancelButtonText: $t('common.cancel'),
        type: 'warning',
      },
    );
    await deleteAnnouncementApi(row.id);
    ElMessage.success($t('announcement.deleteSuccess'));
    gridApi.reload();
  } catch {
    // 用户取消
  }
}

// 发布
async function handlePublish(row: AnnouncementListItem) {
  try {
    await ElMessageBox.confirm(
      $t('announcement.publishConfirm'),
      $t('announcement.publishConfirmTitle'),
      {
        confirmButtonText: $t('common.confirm'),
        cancelButtonText: $t('common.cancel'),
        type: 'warning',
      },
    );
    await publishAnnouncementApi(row.id);
    ElMessage.success($t('announcement.publishSuccess'));
    gridApi.reload();
  } catch {
    // 用户取消
  }
}

// 查看阅读统计
async function handleViewStats(row: AnnouncementListItem) {
  try {
    const res = await getReadStatsApi(row.id);
    statsData.value = res;
    statsDialogVisible.value = true;
  } catch (error) {
    console.error('获取统计失败:', error);
  }
}

// 保存
async function handleSave() {
  try {
    if (isEdit.value) {
      await updateAnnouncementApi(editId.value, formData);
      ElMessage.success($t('announcement.updateSuccess'));
    } else {
      await createAnnouncementApi(formData);
      ElMessage.success($t('announcement.createSuccess'));
    }
    dialogVisible.value = false;
    gridApi.reload();
  } catch (error) {
    console.error('保存失败:', error);
  }
}

// 重置表单
function resetForm() {
  Object.assign(formData, {
    title: '',
    content: '',
    summary: '',
    status: 'draft',
    priority: 0,
    is_top: false,
    target_type: 'all',
    target_ids: [],
    publish_time: undefined,
    expire_time: undefined,
  });
}

// 格式化日期
function formatDate(dateStr?: string): string {
  if (!dateStr) return '-';
  return new Date(dateStr).toLocaleString();
}

// 处理更多菜单命令
function handleCommand(command: string, row: AnnouncementListItem) {
  switch (command) {
    case 'publish': {
      handlePublish(row);
      break;
    }
    case 'stats': {
      handleViewStats(row);
      break;
    }
  }
}
</script>

<template>
  <Page auto-content-height>
    <Grid @selection-change="handleSelectionChange">
      <!-- 工具栏操作 -->
      <template #toolbar-actions>
        <ElButton type="primary" :icon="Plus" @click="handleCreate">
          {{ $t('announcement.createButton') }}
        </ElButton>
      </template>

      <!-- 标题列 -->
      <template #cell-title="{ row }">
        <div class="flex items-center gap-2">
          <ElTag v-if="row.is_top" type="danger" size="small">{{ $t('announcement.topTag') }}</ElTag>
          <span>{{ row.title }}</span>
        </div>
      </template>

      <!-- 状态列 -->
      <template #cell-status="{ row }">
        <ElTag :type="statusMap[row.status]?.type || 'info'" size="small">
          {{ statusMap[row.status]?.label || row.status }}
        </ElTag>
      </template>

      <!-- 优先级列 -->
      <template #cell-priority="{ row }">
        <ElTag :type="priorityMap[row.priority]?.type || 'info'" size="small">
          {{ priorityMap[row.priority]?.label || '普通' }}
        </ElTag>
      </template>

      <!-- 接收范围列 -->
      <template #cell-target_type="{ row }">
        {{ targetTypeMap[row.target_type] || row.target_type }}
      </template>

      <!-- 发布时间列 -->
      <template #cell-publish_time="{ row }">
        {{ formatDate(row.publish_time) }}
      </template>

      <!-- 操作列 -->
      <template #cell-actions="{ row }">
        <ElButton link type="primary" :icon="Edit" @click="handleEdit(row)">
          {{ $t('announcement.editButton') }}
        </ElButton>
        <ElButton link type="danger" :icon="Trash2" @click="handleDelete(row)">
          {{ $t('announcement.deleteButton') }}
        </ElButton>
        <ElDropdown
          trigger="click"
          @command="(cmd: string) => handleCommand(cmd, row)"
        >
          <ElButton link type="primary" :icon="MoreHorizontal"> {{ $t('announcement.moreButton') }} </ElButton>
          <template #dropdown>
            <ElDropdownMenu>
              <ElDropdownItem
                v-if="row.status === 'draft'"
                command="publish"
                :icon="Play"
              >
                {{ $t('announcement.publishButton') }}
              </ElDropdownItem>
              <ElDropdownItem
                v-if="row.status === 'published'"
                command="stats"
                :icon="Eye"
              >
                {{ $t('announcement.statsButton') }}
              </ElDropdownItem>
            </ElDropdownMenu>
          </template>
        </ElDropdown>
      </template>
    </Grid>

    <!-- 编辑弹窗 -->
    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="700px">
      <ElForm :model="formData" label-width="100px">
        <ElFormItem :label="$t('announcement.formTitleLabel')" required>
          <ElInput v-model="formData.title" :placeholder="$t('announcement.formTitlePlaceholder')" />
        </ElFormItem>
        <ElFormItem :label="$t('announcement.formSummaryLabel')">
          <ElInput
            v-model="formData.summary"
            type="textarea"
            :rows="2"
            :placeholder="$t('announcement.formSummaryPlaceholder')"
          />
        </ElFormItem>
        <ElFormItem :label="$t('announcement.formContentLabel')" required>
          <ElInput
            v-model="formData.content"
            type="textarea"
            :rows="6"
            :placeholder="$t('announcement.formContentPlaceholder')"
          />
        </ElFormItem>
        <ElFormItem :label="$t('announcement.formPriorityLabel')">
          <ElSelect v-model="formData.priority" style="width: 120px">
            <ElOption :value="0" :label="$t('announcement.priorityNormal')" />
            <ElOption :value="1" :label="$t('announcement.priorityImportant')" />
            <ElOption :value="2" :label="$t('announcement.priorityUrgent')" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem :label="$t('announcement.formTopLabel')">
          <ElSwitch v-model="formData.is_top" />
        </ElFormItem>
        <ElFormItem :label="$t('announcement.formTargetTypeLabel')">
          <ElSelect v-model="formData.target_type" style="width: 150px">
            <ElOption value="all" :label="$t('announcement.targetTypeAll')" />
            <ElOption value="dept" :label="$t('announcement.targetTypeDept')" />
            <ElOption value="role" :label="$t('announcement.targetTypeRole')" />
            <ElOption value="user" :label="$t('announcement.targetTypeUser')" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem :label="$t('announcement.formExpireTimeLabel')">
          <ElDatePicker
            v-model="formData.expire_time"
            type="datetime"
            :placeholder="$t('announcement.formExpireTimePlaceholder')"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">{{ $t('announcement.formCancelButton') }}</ElButton>
        <ElButton type="primary" @click="handleSave">{{ $t('announcement.formSaveButton') }}</ElButton>
      </template>
    </ElDialog>

    <!-- 阅读统计弹窗 -->
    <ElDialog v-model="statsDialogVisible" :title="$t('announcement.statsTitle')" width="500px">
      <div class="mb-4">
        <span class="text-lg font-medium">{{ $t('announcement.statsReadCount') }}: {{ statsData.total_read }} 人</span>
      </div>
      <ElTable :data="statsData.readers" max-height="400">
        <ElTableColumn :label="$t('announcement.statsUserLabel')" prop="user_name" />
        <ElTableColumn :label="$t('announcement.statsReadTimeLabel')">
          <template #default="{ row }">
            {{ formatDate(row.read_at) }}
          </template>
        </ElTableColumn>
      </ElTable>
    </ElDialog>
  </Page>
</template>
