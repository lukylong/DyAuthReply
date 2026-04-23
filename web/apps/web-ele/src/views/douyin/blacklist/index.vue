<script lang="ts" setup>
import type {
  DouyinBlacklist,
  DouyinBlacklistInput,
} from '#/api/core/douyin';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElPagination,
  ElSelect,
  ElSpace,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  createBlacklist,
  deleteBlacklist,
  getBlacklistList,
  updateBlacklist,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinBlacklist' });

const rows = ref<DouyinBlacklist[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 10, total: 0 });
const search = reactive({
  value: '',
  blacklist_type: undefined as string | undefined,
  scope: undefined as string | undefined,
});

const dialogVisible = ref(false);
const editingId = ref<null | string>(null);
const form = reactive<DouyinBlacklistInput>({
  blacklist_type: 'user',
  value: '',
  scope: 'global',
  reason: '',
  status: true,
});

async function load() {
  loading.value = true;
  try {
    const res = await getBlacklistList({
      page: page.page,
      page_size: page.page_size,
      value: search.value || undefined,
      blacklist_type: search.blacklist_type,
      scope: search.scope,
    });
    rows.value = res.items || [];
    page.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingId.value = null;
  Object.assign(form, {
    blacklist_type: 'user',
    value: '',
    scope: 'global',
    reason: '',
    status: true,
  });
  dialogVisible.value = true;
}

function openEdit(row: DouyinBlacklist) {
  editingId.value = row.id;
  Object.assign(form, {
    blacklist_type: row.blacklist_type,
    value: row.value,
    scope: row.scope,
    reason: row.reason,
    status: row.status,
  });
  dialogVisible.value = true;
}

async function onSave() {
  if (!form.value) {
    ElMessage.warning('请输入具体值');
    return;
  }
  if (editingId.value) await updateBlacklist(editingId.value, form);
  else await createBlacklist(form);
  ElMessage.success('已保存');
  dialogVisible.value = false;
  load();
}

async function onDelete(row: DouyinBlacklist) {
  await ElMessageBox.confirm(`确定移除黑名单 "${row.value}" ?`, '提示', {
    type: 'warning',
  });
  await deleteBlacklist(row.id);
  ElMessage.success('已移除');
  load();
}

function typeLabel(t: string) {
  return (
    { user: '用户(sec_uid)', nickname_keyword: '昵称关键词', content_keyword: '内容关键词' }[t]
    || t
  );
}

function scopeLabel(s: string) {
  return { global: '全局', account: '账号', group: '分组' }[s] || s;
}

onMounted(load);
</script>

<template>
  <Page title="黑名单" description="命中黑名单的消息会直接被 worker 跳过，不触发回复">
    <ElSpace wrap class="toolbar">
      <ElInput
        v-model="search.value"
        placeholder="搜索值"
        clearable
        style="width: 220px"
      />
      <ElSelect
        v-model="search.blacklist_type"
        placeholder="类型"
        clearable
        style="width: 140px"
      >
        <ElOption label="用户 sec_uid" value="user" />
        <ElOption label="昵称关键词" value="nickname_keyword" />
        <ElOption label="内容关键词" value="content_keyword" />
      </ElSelect>
      <ElSelect v-model="search.scope" placeholder="范围" clearable style="width: 120px">
        <ElOption label="全局" value="global" />
        <ElOption label="账号" value="account" />
        <ElOption label="分组" value="group" />
      </ElSelect>
      <ElButton type="primary" @click="load">搜索</ElButton>
      <ElButton type="success" @click="openCreate">加入黑名单</ElButton>
    </ElSpace>

    <ElTable :data="rows" v-loading="loading" stripe>
      <ElTableColumn label="类型" width="120">
        <template #default="{ row }">
          <ElTag size="small">{{ typeLabel(row.blacklist_type) }}</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="value" label="值" min-width="200" />
      <ElTableColumn label="范围" width="90">
        <template #default="{ row }">
          {{ scopeLabel(row.scope) }}
        </template>
      </ElTableColumn>
      <ElTableColumn prop="reason" label="原因" show-overflow-tooltip />
      <ElTableColumn prop="hit_count" label="命中次数" width="100" sortable />
      <ElTableColumn label="状态" width="80">
        <template #default="{ row }">
          <ElTag :type="row.status ? 'success' : 'info'" size="small">
            {{ row.status ? '启用' : '停用' }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" size="small" @click="openEdit(row)">
            编辑
          </ElButton>
          <ElButton link type="danger" size="small" @click="onDelete(row)">
            移除
          </ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pager">
      <ElPagination
        v-model:current-page="page.page"
        v-model:page-size="page.page_size"
        :total="page.total"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="load"
      />
    </div>

    <ElDialog
      v-model="dialogVisible"
      :title="editingId ? '编辑黑名单' : '加入黑名单'"
      width="520px"
    >
      <ElForm :model="form" label-width="90px">
        <ElFormItem label="类型">
          <ElSelect v-model="form.blacklist_type">
            <ElOption label="用户 sec_uid" value="user" />
            <ElOption label="昵称关键词" value="nickname_keyword" />
            <ElOption label="内容关键词" value="content_keyword" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="值">
          <ElInput v-model="form.value" />
        </ElFormItem>
        <ElFormItem label="范围">
          <ElSelect v-model="form.scope">
            <ElOption label="全局" value="global" />
            <ElOption label="账号" value="account" />
            <ElOption label="分组" value="group" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="原因">
          <ElInput v-model="form.reason" />
        </ElFormItem>
        <ElFormItem label="启用">
          <ElSwitch v-model="form.status" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" @click="onSave">保存</ElButton>
      </template>
    </ElDialog>
  </Page>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
