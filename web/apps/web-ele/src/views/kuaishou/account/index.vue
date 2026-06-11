<script lang="ts" setup>
import type {
  KuaishouAccount,
  KuaishouAccountGroup,
  KuaishouAccountInput,
} from '#/api/core/kuaishou';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
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
  createKuaishouAccount,
  deleteKuaishouAccount,
  getAllKuaishouAccountGroup,
  getKuaishouAccountList,
  patchKuaishouAccount,
  updateKuaishouAccount,
} from '#/api/core/kuaishou';

defineOptions({ name: 'KuaishouAccount' });

const STATUS_META: Record<number, { text: string; type: string }> = {
  0: { text: '未登录', type: 'info' },
  1: { text: '在线', type: 'success' },
  2: { text: '登录失效', type: 'warning' },
  3: { text: '已禁用', type: 'danger' },
};

const WORK_MODES = [
  { label: '全自动回复', value: 'auto' },
  { label: '仅人工介入', value: 'manual' },
  { label: '混合', value: 'hybrid' },
];

const rows = ref<KuaishouAccount[]>([]);
const groups = ref<KuaishouAccountGroup[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 10, total: 0 });
const search = reactive({
  nickname: '',
  status: undefined as number | undefined,
  group_id: undefined as string | undefined,
});

const dialogVisible = ref(false);
const editingId = ref<null | string>(null);
const form = reactive<KuaishouAccountInput>({
  nickname: '',
  group_id: null,
  work_mode: 'auto',
  priority: 0,
  daily_reply_quota: 200,
  auto_reply_enabled: true,
  min_interval_seconds: 8,
  max_interval_seconds: 25,
  proxy_url: '',
  remark: '',
});

async function loadGroups() {
  groups.value = await getAllKuaishouAccountGroup();
}

async function load() {
  loading.value = true;
  try {
    const res = await getKuaishouAccountList({
      page: page.page,
      page_size: page.page_size,
      nickname: search.nickname || undefined,
      status: search.status,
      group_id: search.group_id,
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
    nickname: '',
    group_id: null,
    work_mode: 'auto',
    priority: 0,
    daily_reply_quota: 200,
    auto_reply_enabled: true,
    min_interval_seconds: 8,
    max_interval_seconds: 25,
    proxy_url: '',
    remark: '',
  });
  dialogVisible.value = true;
}

function openEdit(row: KuaishouAccount) {
  editingId.value = row.id;
  Object.assign(form, {
    nickname: row.nickname,
    group_id: row.group_id ?? null,
    work_mode: row.work_mode,
    priority: row.priority,
    daily_reply_quota: row.daily_reply_quota,
    auto_reply_enabled: row.auto_reply_enabled,
    min_interval_seconds: row.min_interval_seconds,
    max_interval_seconds: row.max_interval_seconds,
    proxy_url: row.proxy_url ?? '',
    remark: row.remark ?? '',
  });
  dialogVisible.value = true;
}

async function onSave() {
  if (!form.nickname) {
    ElMessage.warning('请输入快手昵称');
    return;
  }
  try {
    if (editingId.value) {
      await updateKuaishouAccount(editingId.value, form);
      ElMessage.success('已更新');
    } else {
      await createKuaishouAccount(form);
      ElMessage.success('已创建');
    }
    dialogVisible.value = false;
    load();
  } catch (e: unknown) {
    ElMessage.error((e as Error).message);
  }
}

async function onToggleAuto(row: KuaishouAccount) {
  try {
    await patchKuaishouAccount(row.id, {
      auto_reply_enabled: row.auto_reply_enabled,
    });
    ElMessage.success('已更新自动回复开关');
  } catch (e: unknown) {
    row.auto_reply_enabled = !row.auto_reply_enabled;
    ElMessage.error((e as Error).message);
  }
}

async function onDelete(row: KuaishouAccount) {
  await ElMessageBox.confirm(`确定删除账号 "${row.nickname}" ?`, '提示', {
    type: 'warning',
  });
  await deleteKuaishouAccount(row.id);
  ElMessage.success('已删除');
  load();
}

onMounted(() => {
  loadGroups();
  load();
});
</script>

<template>
  <Page title="快手账号管理" description="绑定快手账号、配置回复策略（登录态接入随协议逆向上线）">
    <ElSpace wrap class="toolbar">
      <ElInput
        v-model="search.nickname"
        placeholder="搜索昵称"
        clearable
        style="width: 180px"
        @keyup.enter="load"
      />
      <ElSelect
        v-model="search.status"
        placeholder="状态"
        clearable
        style="width: 130px"
      >
        <ElOption label="未登录" :value="0" />
        <ElOption label="在线" :value="1" />
        <ElOption label="登录失效" :value="2" />
        <ElOption label="已禁用" :value="3" />
      </ElSelect>
      <ElSelect
        v-model="search.group_id"
        placeholder="分组"
        clearable
        style="width: 150px"
      >
        <ElOption
          v-for="g in groups"
          :key="g.id"
          :label="g.name"
          :value="g.id"
        />
      </ElSelect>
      <ElButton type="primary" @click="load">搜索</ElButton>
      <ElButton type="success" @click="openCreate">新增账号</ElButton>
    </ElSpace>

    <ElTable :data="rows" v-loading="loading" stripe>
      <ElTableColumn prop="nickname" label="昵称" min-width="140" />
      <ElTableColumn label="状态" width="100">
        <template #default="{ row }">
          <ElTag :type="(STATUS_META[row.status]?.type as any) || 'info'" size="small">
            {{ STATUS_META[row.status]?.text || row.status }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="group_name" label="分组" width="120" />
      <ElTableColumn prop="work_mode_display" label="工作模式" width="120" />
      <ElTableColumn label="自动回复" width="100">
        <template #default="{ row }">
          <ElSwitch
            v-model="row.auto_reply_enabled"
            @change="onToggleAuto(row)"
          />
        </template>
      </ElTableColumn>
      <ElTableColumn label="今日/上限" width="110">
        <template #default="{ row }">
          {{ row.reply_today }} / {{ row.daily_reply_quota }}
        </template>
      </ElTableColumn>
      <ElTableColumn prop="last_heartbeat" label="最近心跳" width="170" />
      <ElTableColumn prop="remark" label="备注" show-overflow-tooltip />
      <ElTableColumn label="操作" width="150" fixed="right">
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
      :title="editingId ? '编辑账号' : '新增账号'"
      width="560px"
    >
      <ElForm :model="form" label-width="120px">
        <ElFormItem label="快手昵称">
          <ElInput v-model="form.nickname" />
        </ElFormItem>
        <ElFormItem label="所属分组">
          <ElSelect v-model="form.group_id" clearable placeholder="未分组" style="width: 100%">
            <ElOption
              v-for="g in groups"
              :key="g.id"
              :label="g.name"
              :value="g.id"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="工作模式">
          <ElSelect v-model="form.work_mode" style="width: 100%">
            <ElOption
              v-for="m in WORK_MODES"
              :key="m.value"
              :label="m.label"
              :value="m.value"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="调度优先级">
          <ElInputNumber v-model="form.priority" :min="0" />
        </ElFormItem>
        <ElFormItem label="日回复上限">
          <ElInputNumber v-model="form.daily_reply_quota" :min="0" />
        </ElFormItem>
        <ElFormItem label="启用自动回复">
          <ElSwitch v-model="form.auto_reply_enabled" />
        </ElFormItem>
        <ElFormItem label="最小间隔(秒)">
          <ElInputNumber v-model="form.min_interval_seconds" :min="1" />
        </ElFormItem>
        <ElFormItem label="最大间隔(秒)">
          <ElInputNumber v-model="form.max_interval_seconds" :min="1" />
        </ElFormItem>
        <ElFormItem label="代理地址">
          <ElInput v-model="form.proxy_url" placeholder="http://user:pass@host:port" />
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
