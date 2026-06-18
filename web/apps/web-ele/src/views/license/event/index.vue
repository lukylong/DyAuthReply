<script lang="ts" setup>
import type { LicenseEvent } from '#/api/core/license';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElCard,
  ElPagination,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { getLicenseEventListApi } from '#/api/core/license';

defineOptions({ name: 'LicenseEvents' });

const loading = ref(false);
const rows = ref<LicenseEvent[]>([]);
const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0,
});

async function loadData() {
  loading.value = true;
  try {
    const res = await getLicenseEventListApi({
      page: pagination.page,
      pageSize: pagination.pageSize,
    });
    rows.value = res.items || [];
    pagination.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

onMounted(loadData);
</script>

<template>
  <Page auto-content-height>
    <ElCard shadow="never">
      <ElTable :data="rows" v-loading="loading" border>
        <ElTableColumn prop="event_type" label="事件类型" min-width="180">
          <template #default="{ row }">
            <ElTag size="small">{{ row.event_type }}</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="license_key_id" label="卡密ID" min-width="220" />
        <ElTableColumn prop="client_device_id" label="设备ID" min-width="220" />
        <ElTableColumn prop="activation_id" label="激活ID" min-width="220" />
        <ElTableColumn prop="ip" label="来源IP" min-width="140" />
        <ElTableColumn label="载荷" min-width="320">
          <template #default="{ row }">
            <pre class="payload">{{ JSON.stringify(row.payload || {}, null, 2) }}</pre>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="sys_create_datetime" label="时间" min-width="180" />
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

<style scoped>
.payload {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.4;
}
</style>
