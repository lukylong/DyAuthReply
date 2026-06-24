<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { ElButton, ElMessage, ElMessageBox, ElTag } from 'element-plus';

import { ClientAnnouncementApi } from '#/api/core/client-announcement';

import AnnouncementForm from './modules/announcement-form.vue';

defineOptions({ name: 'ClientAnnouncement' });

const loading = ref(false);
const tableData = ref<ClientAnnouncementApi.AnnouncementItem[]>([]);
const total = ref(0);

const queryParams = reactive({
  page: 1,
  page_size: 10,
  status: undefined as 'draft' | 'published' | 'revoked' | undefined,
  level: undefined as 'info' | 'warning' | 'urgent' | undefined,
});

const formVisible = ref(false);
const formMode = ref<'create' | 'edit'>('create');
const currentRow = ref<ClientAnnouncementApi.AnnouncementItem | null>(null);

const levelMap = {
  info: { label: '普通', type: 'info' },
  warning: { label: '警告', type: 'warning' },
  urgent: { label: '紧急', type: 'danger' },
};

const statusMap = {
  draft: { label: '草稿', type: 'info' },
  published: { label: '已发布', type: 'success' },
  revoked: { label: '已撤回', type: 'warning' },
};

async function fetchList() {
  loading.value = true;
  try {
    const res = await ClientAnnouncementApi.getList(queryParams);
    tableData.value = res.items;
    total.value = res.total;
  } catch (error: any) {
    ElMessage.error(error.message || '获取列表失败');
  } finally {
    loading.value = false;
  }
}

function handleCreate() {
  formMode.value = 'create';
  currentRow.value = null;
  formVisible.value = true;
}

function handleEdit(row: ClientAnnouncementApi.AnnouncementItem) {
  formMode.value = 'edit';
  currentRow.value = row;
  formVisible.value = true;
}

async function handleDelete(row: ClientAnnouncementApi.AnnouncementItem) {
  try {
    await ElMessageBox.confirm(`确定要删除公告「${row.title}」吗？`, '删除确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    });

    await ClientAnnouncementApi.remove(row.id);
    ElMessage.success('删除成功');
    await fetchList();
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '删除失败');
    }
  }
}

async function handlePublish(row: ClientAnnouncementApi.AnnouncementItem) {
  try {
    await ElMessageBox.confirm(
      `确定要发布公告「${row.title}」吗？发布后将通过 WebSocket 推送到所有在线客户端。`,
      '发布确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'info',
      },
    );

    await ClientAnnouncementApi.publish(row.id);
    ElMessage.success('发布成功');
    await fetchList();
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '发布失败');
    }
  }
}

function handlePageChange(page: number) {
  queryParams.page = page;
  fetchList();
}

function handleSizeChange(size: number) {
  queryParams.page_size = size;
  queryParams.page = 1;
  fetchList();
}

function handleFormSuccess() {
  formVisible.value = false;
  fetchList();
}

onMounted(() => {
  fetchList();
});
</script>

<template>
  <Page
    auto-content-height
    content-class="flex flex-col"
    description="管理客户端公告，发布后将通过 WebSocket 推送到所有在线客户端"
    title="客户端公告"
  >
    <div class="mb-4 flex items-center justify-between">
      <div class="flex items-center gap-2">
        <el-select
          v-model="queryParams.status"
          clearable
          placeholder="状态"
          style="width: 120px"
          @change="fetchList"
        >
          <el-option label="草稿" value="draft" />
          <el-option label="已发布" value="published" />
          <el-option label="已撤回" value="revoked" />
        </el-select>
        <el-select
          v-model="queryParams.level"
          clearable
          placeholder="级别"
          style="width: 120px"
          @change="fetchList"
        >
          <el-option label="普通" value="info" />
          <el-option label="警告" value="warning" />
          <el-option label="紧急" value="urgent" />
        </el-select>
        <el-button @click="fetchList">刷新</el-button>
      </div>
      <el-button type="primary" @click="handleCreate">新增公告</el-button>
    </div>

    <el-table v-loading="loading" :data="tableData" border stripe>
      <el-table-column label="标题" min-width="200" prop="title" />
      <el-table-column label="级别" width="100">
        <template #default="{ row }">
          <el-tag :type="levelMap[row.level].type">
            {{ levelMap[row.level].label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusMap[row.status].type">
            {{ statusMap[row.status].label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="发布时间" width="180">
        <template #default="{ row }">
          {{ row.publish_time || '-' }}
        </template>
      </el-table-column>
      <el-table-column label="过期时间" width="180">
        <template #default="{ row }">
          {{ row.expire_time || '永不过期' }}
        </template>
      </el-table-column>
      <el-table-column label="目标版本" width="120">
        <template #default="{ row }">
          {{ row.target_version || '所有版本' }}
        </template>
      </el-table-column>
      <el-table-column fixed="right" label="操作" width="200">
        <template #default="{ row }">
          <el-button link size="small" type="primary" @click="handleEdit(row)">
            编辑
          </el-button>
          <el-button
            v-if="row.status === 'draft'"
            link
            size="small"
            type="success"
            @click="handlePublish(row)"
          >
            发布
          </el-button>
          <el-button link size="small" type="danger" @click="handleDelete(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="mt-4 flex justify-end">
      <el-pagination
        v-model:current-page="queryParams.page"
        v-model:page-size="queryParams.page_size"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        background
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </div>

    <AnnouncementForm
      v-model:visible="formVisible"
      :current-row="currentRow"
      :mode="formMode"
      @success="handleFormSuccess"
    />
  </Page>
</template>
