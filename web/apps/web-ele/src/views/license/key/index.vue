<script lang="ts" setup>
import type { GeneratedLicenseKey, LicenseKey, LicensePlan } from '#/api/core/license';

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
  ElMessageBox,
  ElOption,
  ElPagination,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  deleteLicenseKeyApi,
  generateLicenseKeysApi,
  getLicenseKeyListApi,
  getLicensePlanListApi,
  revokeLicenseKeyApi,
} from '#/api/core/license';

defineOptions({ name: 'LicenseKeys' });

const loading = ref(false);
const generating = ref(false);
const keys = ref<LicenseKey[]>([]);
const plans = ref<LicensePlan[]>([]);
const generateDialogVisible = ref(false);
const generatedDialogVisible = ref(false);
const generatedItems = ref<GeneratedLicenseKey[]>([]);

const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0,
});

const searchForm = reactive({
  status: '',
  plan_id: '',
  masked_code: '',
});

const showAdvanced = ref(false);

const generateForm = reactive({
  plan_id: '',
  count: 10,
  issued_to: '',
  batch_no: '',
  max_devices_override: undefined as number | undefined,
  valid_days_override: undefined as number | undefined,
  notes: '',
});

const selectedPlan = computed(() =>
  plans.value.find((plan) => plan.id === generateForm.plan_id),
);

const statusTagType = computed<Record<string, 'danger' | 'info' | 'success' | 'warning'>>(() => ({
  pending: 'info',
  active: 'success',
  revoked: 'danger',
  expired: 'warning',
}));

async function loadPlans() {
  const res = await getLicensePlanListApi({ page: 1, pageSize: 200 });
  plans.value = res.items || [];
  const defaultPlan = plans.value.find((plan) => plan.is_active) || plans.value[0];
  if (!generateForm.plan_id && defaultPlan) {
    generateForm.plan_id = defaultPlan.id;
  }
}

function openGenerateDialog() {
  showAdvanced.value = false;
  if (!generateForm.plan_id) {
    const defaultPlan = plans.value.find((plan) => plan.is_active) || plans.value[0];
    if (defaultPlan) generateForm.plan_id = defaultPlan.id;
  }
  generateDialogVisible.value = true;
}

async function loadData() {
  loading.value = true;
  try {
    const res = await getLicenseKeyListApi({
      page: pagination.page,
      pageSize: pagination.pageSize,
      plan_id: searchForm.plan_id || undefined,
      status: searchForm.status || undefined,
      masked_code: searchForm.masked_code || undefined,
    });
    keys.value = res.items || [];
    pagination.total = res.total || 0;
  } catch (error) {
    console.error(error);
    ElMessage.error('加载卡密列表失败');
  } finally {
    loading.value = false;
  }
}

function onSearch() {
  pagination.page = 1;
  loadData();
}

async function onGenerate() {
  if (!generateForm.plan_id) {
    ElMessage.warning('请先选择套餐');
    return;
  }
  generating.value = true;
  try {
    const res = await generateLicenseKeysApi(generateForm);
    generatedItems.value = res.items || [];
    generateDialogVisible.value = false;
    generatedDialogVisible.value = true;
    ElMessage.success(`已生成 ${res.count} 个卡密`);
    await loadData();
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error?.response?.data?.detail || '卡密生成失败');
  } finally {
    generating.value = false;
  }
}

async function onRevoke(row: LicenseKey) {
  try {
    await ElMessageBox.confirm(`确定撤销卡密 ${row.masked_code} 吗？`, '撤销卡密', { type: 'warning' });
  } catch {
    return;
  }

  try {
    await revokeLicenseKeyApi(row.id, '后台手动撤销');
    ElMessage.success('卡密已撤销');
    loadData();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '撤销失败');
  }
}

async function onDelete(row: LicenseKey) {
  try {
    await ElMessageBox.confirm(`确定删除卡密 ${row.masked_code} 吗？删除后将从列表移除。`, '删除卡密', {
      type: 'warning',
    });
  } catch {
    return;
  }

  try {
    await deleteLicenseKeyApi(row.id);
    ElMessage.success('卡密已删除');
    loadData();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '删除失败');
  }
}

onMounted(async () => {
  await loadPlans();
  await loadData();
});
</script>

<template>
  <Page auto-content-height>
    <ElCard shadow="never">
      <div class="mb-4 flex items-center justify-between gap-4">
        <div class="flex gap-3">
          <ElSelect v-model="searchForm.plan_id" clearable placeholder="套餐" style="width: 180px">
            <ElOption v-for="plan in plans" :key="plan.id" :label="plan.name" :value="plan.id" />
          </ElSelect>
          <ElSelect v-model="searchForm.status" clearable placeholder="状态" style="width: 160px">
            <ElOption label="未激活" value="pending" />
            <ElOption label="生效中" value="active" />
            <ElOption label="已撤销" value="revoked" />
            <ElOption label="已过期" value="expired" />
          </ElSelect>
          <ElInput v-model="searchForm.masked_code" placeholder="脱敏卡密" clearable style="width: 180px" />
          <ElButton type="primary" @click="onSearch">查询</ElButton>
        </div>
        <ElButton type="primary" @click="openGenerateDialog">批量发卡</ElButton>
      </div>

      <ElTable :data="keys" v-loading="loading" border>
        <ElTableColumn prop="masked_code" label="卡密" min-width="140" />
        <ElTableColumn prop="plan_name" label="套餐" min-width="120" />
        <ElTableColumn prop="issued_to" label="发放对象" min-width="120" />
        <ElTableColumn prop="batch_no" label="批次号" min-width="120" />
        <ElTableColumn label="状态" width="100">
          <template #default="{ row }">
            <ElTag :type="statusTagType[row.status]">{{ row.status }}</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="effective_max_devices" label="设备数" width="90" />
        <ElTableColumn prop="effective_valid_days" label="有效天数" width="100" />
        <ElTableColumn prop="expires_at" label="到期时间" min-width="180" />
        <ElTableColumn prop="last_check_in_at" label="最后心跳" min-width="180" />
        <ElTableColumn label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <ElButton link type="danger" :disabled="row.status === 'revoked'" @click="onRevoke(row)">撤销</ElButton>
            <ElButton link type="danger" @click="onDelete(row)">删除</ElButton>
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

    <ElDialog v-model="generateDialogVisible" title="批量生成卡密" width="560px">
      <ElForm :model="generateForm" label-width="90px">
        <ElFormItem label="授权套餐">
          <ElSelect v-model="generateForm.plan_id" placeholder="请选择套餐" style="width: 100%">
            <ElOption v-for="plan in plans" :key="plan.id" :label="plan.name" :value="plan.id" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="生成数量">
          <ElInputNumber v-model="generateForm.count" :min="1" :max="200" />
          <span v-if="selectedPlan" class="ml-3 text-xs text-gray-400">
            将套用：{{ selectedPlan.max_devices }} 台设备 / {{ selectedPlan.valid_days }} 天有效
          </span>
        </ElFormItem>

        <div class="mb-2">
          <ElButton link type="primary" @click="showAdvanced = !showAdvanced">
            {{ showAdvanced ? '收起高级选项' : '高级选项（发放对象 / 批次号 / 覆盖项 / 备注）' }}
          </ElButton>
        </div>

        <template v-if="showAdvanced">
          <ElFormItem label="发放对象">
            <ElInput v-model="generateForm.issued_to" placeholder="选填，用于备注客户/渠道" />
          </ElFormItem>
          <ElFormItem label="批次号">
            <ElInput v-model="generateForm.batch_no" placeholder="选填，留空自动按时间生成" />
          </ElFormItem>
          <ElFormItem label="设备数覆盖">
            <ElInputNumber v-model="generateForm.max_devices_override" :min="1" />
            <span class="ml-3 text-xs text-gray-400">留空则用套餐默认值</span>
          </ElFormItem>
          <ElFormItem label="有效期覆盖">
            <ElInputNumber v-model="generateForm.valid_days_override" :min="0" />
            <span class="ml-3 text-xs text-gray-400">留空则用套餐默认值</span>
          </ElFormItem>
          <ElFormItem label="备注">
            <ElInput v-model="generateForm.notes" type="textarea" :rows="3" />
          </ElFormItem>
        </template>
      </ElForm>
      <template #footer>
        <ElButton @click="generateDialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="generating" @click="onGenerate">生成</ElButton>
      </template>
    </ElDialog>

    <ElDialog v-model="generatedDialogVisible" title="已生成卡密" width="720px">
      <div class="mb-3 text-sm text-gray-500">请立即保存，明文卡密只在本次展示。</div>
      <ElTable :data="generatedItems" border max-height="420">
        <ElTableColumn prop="code" label="明文卡密" min-width="220" />
        <ElTableColumn prop="masked_code" label="脱敏展示" min-width="140" />
        <ElTableColumn prop="max_devices" label="设备数" width="90" />
        <ElTableColumn prop="expires_at" label="到期时间" min-width="180" />
      </ElTable>
    </ElDialog>
  </Page>
</template>
