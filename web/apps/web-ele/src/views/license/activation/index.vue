<script lang="ts" setup>
import type { LicenseActivation } from '#/api/core/license';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElInput,
  ElPagination,
  ElSelect,
  ElOption,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { getLicenseActivationListApi } from '#/api/core/license';

defineOptions({ name: 'LicenseActivations' });

const loading = ref(false);
const rows = ref<LicenseActivation[]>([]);
const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0,
});
const searchForm = reactive({
  status: '',
  license_key_id: '',
  client_device_id: '',
});

async function loadData() {
  loading.value = true;
  try {
    const res = await getLicenseActivationListApi({
      page: pagination.page,
      pageSize: pagination.pageSize,
      status: searchForm.status || undefined,
      license_key_id: searchForm.license_key_id || undefined,
      client_device_id: searchForm.client_device_id || undefined,
    });
    rows.value = res.items || [];
    pagination.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

function onSearch() {
  pagination.page = 1;
  loadData();
}

onMounted(loadData);
</script>

<template>
  <Page auto-content-height>
    <ElCard shadow="never">
      <div class="mb-4 flex gap-3">
        <ElInput v-model="searchForm.license_key_id" placeholder="卡密ID" clearable style="width: 220px" />
        <ElInput v-model="searchForm.client_device_id" placeholder="设备ID" clearable style="width: 220px" />
        <ElSelect v-model="searchForm.status" clearable placeholder="状态" style="width: 140px">
          <ElOption label="活跃" value="active" />
          <ElOption label="已撤销" value="revoked" />
          <ElOption label="已解绑" value="deactivated" />
          <ElOption label="已过期" value="expired" />
        </ElSelect>
        <ElButton type="primary" @click="onSearch">查询</ElButton>
      </div>

      <ElTable :data="rows" v-loading="loading" border>
        <ElTableColumn prop="masked_code" label="卡密" min-width="140" />
        <ElTableColumn prop="device_fingerprint" label="设备指纹" min-width="220" />
        <ElTableColumn prop="activated_at" label="激活时间" min-width="180" />
        <ElTableColumn prop="last_heartbeat_at" label="最后心跳" min-width="180" />
        <ElTableColumn prop="last_valid_until" label="离线截止" min-width="180" />
        <ElTableColumn prop="expires_at" label="到期时间" min-width="180" />
        <ElTableColumn label="状态" width="100">
          <template #default="{ row }">
            <ElTag :type="row.status === 'active' ? 'success' : row.status === 'revoked' ? 'danger' : 'warning'">
              {{ row.status }}
            </ElTag>
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
  </Page>
</template>
