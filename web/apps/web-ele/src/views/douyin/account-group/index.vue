<script lang="ts" setup>
import type {
  DouyinAccountGroup,
  DouyinAccountGroupInput,
} from '#/api/core/douyin';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElColorPicker,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElPagination,
  ElSpace,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  createDouyinAccountGroup,
  deleteDouyinAccountGroup,
  getDouyinAccountGroupList,
  updateDouyinAccountGroup,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinAccountGroup' });

const rows = ref<DouyinAccountGroup[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 10, total: 0 });
const search = reactive({ name: '' });

const dialogVisible = ref(false);
const editingId = ref<null | string>(null);
const form = reactive<DouyinAccountGroupInput>({
  name: '',
  color: '#409EFF',
  default_daily_reply_quota: 200,
  default_min_interval: 8,
  default_max_interval: 25,
  status: true,
  remark: '',
});

async function load() {
  loading.value = true;
  try {
    const res = await getDouyinAccountGroupList({
      page: page.page,
      page_size: page.page_size,
      name: search.name || undefined,
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
    name: '',
    color: '#409EFF',
    default_daily_reply_quota: 200,
    default_min_interval: 8,
    default_max_interval: 25,
    status: true,
    remark: '',
  });
  dialogVisible.value = true;
}

function openEdit(row: DouyinAccountGroup) {
  editingId.value = row.id;
  Object.assign(form, {
    name: row.name,
    color: row.color,
    default_daily_reply_quota: row.default_daily_reply_quota,
    default_min_interval: row.default_min_interval,
    default_max_interval: row.default_max_interval,
    status: row.status,
    remark: row.remark,
  });
  dialogVisible.value = true;
}

async function onSave() {
  if (!form.name) {
    ElMessage.warning('请输入分组名');
    return;
  }
  try {
    if (editingId.value) {
      await updateDouyinAccountGroup(editingId.value, form);
      ElMessage.success('已更新');
    } else {
      await createDouyinAccountGroup(form);
      ElMessage.success('已创建');
    }
    dialogVisible.value = false;
    load();
  } catch (e: unknown) {
    ElMessage.error((e as Error).message);
  }
}

async function onDelete(row: DouyinAccountGroup) {
  await ElMessageBox.confirm(`确定删除分组 "${row.name}" ?`, '提示', {
    type: 'warning',
  });
  try {
    await deleteDouyinAccountGroup(row.id);
    ElMessage.success('已删除');
    load();
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '分组下仍有账号，无法删除');
  }
}

onMounted(load);
</script>

<template>
  <Page title="账号分组管理" description="按业务线/团队对多个抖音账号进行分组管理">
    <ElSpace wrap class="toolbar">
      <ElInput
        v-model="search.name"
        placeholder="搜索分组名"
        clearable
        style="width: 220px"
        @keyup.enter="load"
      />
      <ElButton type="primary" @click="load">搜索</ElButton>
      <ElButton type="success" @click="openCreate">新建分组</ElButton>
    </ElSpace>

    <ElTable :data="rows" v-loading="loading" stripe>
      <ElTableColumn label="分组" min-width="160">
        <template #default="{ row }">
          <ElTag :color="row.color" effect="dark">{{ row.name }}</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="account_count" label="账号数" width="90" />
      <ElTableColumn prop="owner_name" label="负责人" width="120" />
      <ElTableColumn label="默认策略" min-width="200">
        <template #default="{ row }">
          日上限 {{ row.default_daily_reply_quota }} / 间隔
          {{ row.default_min_interval }}-{{ row.default_max_interval }}s
        </template>
      </ElTableColumn>
      <ElTableColumn label="状态" width="80">
        <template #default="{ row }">
          <ElTag :type="row.status ? 'success' : 'info'" size="small">
            {{ row.status ? '启用' : '停用' }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="remark" label="备注" show-overflow-tooltip />
      <ElTableColumn label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" size="small" @click="openEdit(row)">
            编辑
          </ElButton>
          <ElButton link type="danger" size="small" @click="onDelete(row)">
            删除
          </ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pager">
      <ElPagination
        v-model:current-page="page.page"
        v-model:page-size="page.page_size"
        :total="page.total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="load"
      />
    </div>

    <ElDialog
      v-model="dialogVisible"
      :title="editingId ? '编辑分组' : '新建分组'"
      width="520px"
    >
      <ElForm :model="form" label-width="120px">
        <ElFormItem label="分组名">
          <ElInput v-model="form.name" />
        </ElFormItem>
        <ElFormItem label="颜色">
          <ElColorPicker v-model="form.color" />
        </ElFormItem>
        <ElFormItem label="日回复上限">
          <ElInputNumber v-model="form.default_daily_reply_quota" :min="0" />
        </ElFormItem>
        <ElFormItem label="最小间隔(秒)">
          <ElInputNumber v-model="form.default_min_interval" :min="1" />
        </ElFormItem>
        <ElFormItem label="最大间隔(秒)">
          <ElInputNumber v-model="form.default_max_interval" :min="1" />
        </ElFormItem>
        <ElFormItem label="启用">
          <ElSwitch v-model="form.status" />
        </ElFormItem>
        <ElFormItem label="备注">
          <ElInput v-model="form.remark" type="textarea" :rows="2" />
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
