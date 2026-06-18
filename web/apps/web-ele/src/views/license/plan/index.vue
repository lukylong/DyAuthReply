<script lang="ts" setup>
import type { LicensePlan, LicensePlanInput } from '#/api/core/license';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElPagination,
  ElSwitch,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import {
  createLicensePlanApi,
  getLicensePlanListApi,
  getLicenseStatsApi,
  updateLicensePlanApi,
} from '#/api/core/license';

defineOptions({ name: 'LicensePlans' });

const loading = ref(false);
const saving = ref(false);
const plans = ref<LicensePlan[]>([]);
const stats = ref<{ plans_total: number; keys_total: number; activations_active: number } | null>(null);
const dialogVisible = ref(false);
const editingId = ref<string | null>(null);
const formRef = ref();

const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0,
});

const searchForm = reactive({
  code: '',
  name: '',
});

const form = reactive<LicensePlanInput>({
  code: '',
  name: '',
  description: '',
  feature_flags: {
    auto_reply: true,
    account_manage: true,
  },
  max_devices: 1,
  valid_days: 365,
  heartbeat_interval_minutes: 30,
  grace_period_minutes: 2880,
  is_active: true,
});

const dialogTitle = computed(() => (editingId.value ? '编辑套餐' : '新建套餐'));

async function loadStats() {
  try {
    const res = await getLicenseStatsApi();
    stats.value = {
      plans_total: res.plans_total,
      keys_total: res.keys_total,
      activations_active: res.activations_active,
    };
  } catch (error) {
    console.error('加载授权统计失败', error);
  }
}

async function loadData() {
  loading.value = true;
  try {
    const res = await getLicensePlanListApi({
      page: pagination.page,
      pageSize: pagination.pageSize,
      code: searchForm.code || undefined,
      name: searchForm.name || undefined,
    });
    plans.value = res.items || [];
    pagination.total = res.total || 0;
  } catch (error) {
    console.error(error);
    ElMessage.error('加载套餐列表失败');
  } finally {
    loading.value = false;
  }
}

function resetForm() {
  editingId.value = null;
  Object.assign(form, {
    code: '',
    name: '',
    description: '',
    feature_flags: {
      auto_reply: true,
      account_manage: true,
    },
    max_devices: 1,
    valid_days: 365,
    heartbeat_interval_minutes: 30,
    grace_period_minutes: 2880,
    is_active: true,
  });
}

function onSearch() {
  pagination.page = 1;
  loadData();
}

function openCreate() {
  resetForm();
  dialogVisible.value = true;
}

function openEdit(row: LicensePlan) {
  editingId.value = row.id;
  Object.assign(form, {
    code: row.code,
    name: row.name,
    description: row.description || '',
    feature_flags: row.feature_flags || {},
    max_devices: row.max_devices,
    valid_days: row.valid_days,
    heartbeat_interval_minutes: row.heartbeat_interval_minutes,
    grace_period_minutes: row.grace_period_minutes,
    is_active: row.is_active,
  });
  dialogVisible.value = true;
}

async function onSubmit() {
  try {
    await formRef.value?.validate?.();
  } catch {
    return;
  }

  saving.value = true;
  try {
    if (editingId.value) {
      await updateLicensePlanApi(editingId.value, form);
      ElMessage.success('套餐已更新');
    } else {
      await createLicensePlanApi(form);
      ElMessage.success('套餐已创建');
    }
    dialogVisible.value = false;
    await Promise.all([loadData(), loadStats()]);
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error?.response?.data?.detail || '保存套餐失败');
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadData(), loadStats()]);
});
</script>

<template>
  <Page auto-content-height>
    <div class="space-y-4">
      <div class="grid grid-cols-3 gap-4">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">套餐数</div>
          <div class="mt-2 text-2xl font-semibold">{{ stats?.plans_total ?? 0 }}</div>
        </ElCard>
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">卡密数</div>
          <div class="mt-2 text-2xl font-semibold">{{ stats?.keys_total ?? 0 }}</div>
        </ElCard>
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">生效激活</div>
          <div class="mt-2 text-2xl font-semibold">{{ stats?.activations_active ?? 0 }}</div>
        </ElCard>
      </div>

      <ElCard shadow="never">
        <div class="mb-4 flex items-center justify-between gap-4">
          <div class="flex gap-3">
            <ElInput v-model="searchForm.code" placeholder="套餐编码" clearable style="width: 180px" />
            <ElInput v-model="searchForm.name" placeholder="套餐名称" clearable style="width: 180px" />
            <ElButton type="primary" @click="onSearch">查询</ElButton>
          </div>
          <ElButton type="primary" @click="openCreate">新建套餐</ElButton>
        </div>

        <ElTable :data="plans" v-loading="loading" border>
          <ElTableColumn prop="code" label="编码" min-width="120" />
          <ElTableColumn prop="name" label="套餐名称" min-width="140" />
          <ElTableColumn prop="max_devices" label="设备数" width="100" />
          <ElTableColumn prop="valid_days" label="有效天数" width="110" />
          <ElTableColumn prop="heartbeat_interval_minutes" label="心跳间隔" width="110" />
          <ElTableColumn prop="grace_period_minutes" label="离线宽限" width="110" />
          <ElTableColumn label="状态" width="90">
            <template #default="{ row }">
              <span>{{ row.is_active ? '启用' : '停用' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <ElButton link type="primary" @click="openEdit(row)">编辑</ElButton>
            </template>
          </ElTableColumn>
        </ElTable>

        <div class="mt-4 flex justify-end">
          <ElPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next"
            :total="pagination.total"
            @current-change="loadData"
            @size-change="loadData"
          />
        </div>
      </ElCard>
    </div>

    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="620px">
      <ElForm ref="formRef" :model="form" label-width="110px">
        <ElFormItem label="套餐编码" prop="code">
          <ElInput v-model="form.code" :disabled="Boolean(editingId)" />
        </ElFormItem>
        <ElFormItem label="套餐名称" prop="name">
          <ElInput v-model="form.name" />
        </ElFormItem>
        <ElFormItem label="描述">
          <ElInput v-model="form.description" type="textarea" :rows="3" />
        </ElFormItem>
        <ElFormItem label="最大设备数">
          <ElInputNumber v-model="form.max_devices" :min="1" />
        </ElFormItem>
        <ElFormItem label="有效天数">
          <ElInputNumber v-model="form.valid_days" :min="0" />
        </ElFormItem>
        <ElFormItem label="心跳间隔">
          <ElInputNumber v-model="form.heartbeat_interval_minutes" :min="1" />
        </ElFormItem>
        <ElFormItem label="离线宽限">
          <ElInputNumber v-model="form.grace_period_minutes" :min="1" />
        </ElFormItem>
        <ElFormItem label="核心能力">
          <div class="flex flex-col gap-2">
            <ElSwitch v-model="form.feature_flags!.auto_reply" active-text="自动回复" />
            <ElSwitch v-model="form.feature_flags!.account_manage" active-text="账号管理" />
          </div>
        </ElFormItem>
        <ElFormItem label="启用状态">
          <ElSwitch v-model="form.is_active" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="onSubmit">保存</ElButton>
      </template>
    </ElDialog>
  </Page>
</template>
