<script lang="ts" setup>
import type {
  OnActionClickParams,
  VxeTableGridOptions,
} from '#/adapter/vxe-table';
import type { DouyinRule } from '#/api/core/douyin';

import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { ElButton, ElMessage, ElMessageBox } from 'element-plus';

import { useVbenVxeGrid } from '#/adapter/vxe-table';
import {
  batchDeleteDouyinRuleApi,
  deleteDouyinRuleApi,
  getDouyinRuleListApi,
} from '#/api/core/douyin';

import { useRuleTableColumns, useSearchFormSchema } from './data';
import RuleFormModal from './modules/rule-form-modal.vue';

defineOptions({ name: 'DouyinRule' });

const selectedRows = ref<DouyinRule[]>([]);
const formModalRef = ref<InstanceType<typeof RuleFormModal>>();

function onAdd() {
  formModalRef.value?.open();
}

function onEdit(row: DouyinRule) {
  formModalRef.value?.open(row);
}

async function onDelete(row: DouyinRule) {
  try {
    await ElMessageBox.confirm(`确定删除规则 "${row.name}" ?`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    });
  } catch {
    return;
  }
  await deleteDouyinRuleApi(row.id);
  ElMessage.success('已删除');
  gridApi.query();
}

async function onBatchDelete() {
  if (selectedRows.value.length === 0) {
    ElMessage.warning('请先勾选要删除的规则');
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确定删除勾选的 ${selectedRows.value.length} 条规则？`,
      '批量删除',
      { type: 'warning' },
    );
  } catch {
    return;
  }
  await batchDeleteDouyinRuleApi(selectedRows.value.map((r) => r.id));
  ElMessage.success('已删除');
  selectedRows.value = [];
  gridApi.query();
}

function onActionClick({ code, row }: OnActionClickParams<DouyinRule>) {
  switch (code) {
    case 'delete': {
      onDelete(row);
      break;
    }
    case 'edit': {
      onEdit(row);
      break;
    }
  }
}

function onFormSuccess() {
  gridApi.query();
}

const [Grid, gridApi] = useVbenVxeGrid({
  formOptions: {
    schema: useSearchFormSchema(),
    submitOnChange: true,
    collapsed: false,
  },
  gridEvents: {
    checkboxAll: ({ records }: { records: DouyinRule[] }) => {
      selectedRows.value = records;
    },
    checkboxChange: ({ records }: { records: DouyinRule[] }) => {
      selectedRows.value = records;
    },
  },
  gridOptions: {
    columns: useRuleTableColumns(onActionClick),
    height: 'auto',
    keepSource: true,
    proxyConfig: {
      ajax: {
        query: async ({ page }, formValues) => {
          return await getDouyinRuleListApi({
            page: page.currentPage,
            pageSize: page.pageSize,
            ...formValues,
          });
        },
      },
    },
    checkboxConfig: { reserve: true, trigger: 'default' },
    rowConfig: { keyField: 'id' },
    toolbarConfig: {
      custom: true,
      export: false,
      refresh: true,
      search: true,
      zoom: true,
    },
  } as VxeTableGridOptions<DouyinRule>,
});
</script>

<template>
  <Page auto-content-height>
    <RuleFormModal ref="formModalRef" @success="onFormSuccess" />

    <Grid>
      <template #table-title>
        <ElButton type="primary" @click="onAdd">新增规则</ElButton>
        <ElButton
          type="danger"
          plain
          :disabled="selectedRows.length === 0"
          @click="onBatchDelete"
        >
          批量删除{{ selectedRows.length > 0 ? `(${selectedRows.length})` : '' }}
        </ElButton>
      </template>
    </Grid>
  </Page>
</template>
