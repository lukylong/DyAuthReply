<script lang="ts" setup>
import type { ClientDevice } from '#/api/core/license';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElPagination,
  ElSelect,
  ElOption,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { getClientDeviceListApi, getLicenseActivationListApi, unbindLicenseDeviceApi } from '#/api/core/license';

defineOptions({ name: 'LicenseDevices' });

const loading = ref(false);
const devices = ref<ClientDevice[]>([]);
const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0,
});
const searchForm = reactive({
  device_fingerprint: '',
  os_type: '',
  status: '',
});

const unbinding = ref('');

async function loadData() {
  loading.value = true;
  try {
    const res = await getClientDeviceListApi({
      page: pagination.page,
      pageSize: pagination.pageSize,
      device_fingerprint: searchForm.device_fingerprint || undefined,
      os_type: searchForm.os_type || undefined,
      status: searchForm.status || undefined,
    });
    devices.value = res.items || [];
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

async function onUnbind(row: ClientDevice) {
  try {
    const activations = await getLicenseActivationListApi({
      page: 1,
      pageSize: 20,
      client_device_id: row.id,
      status: 'active',
    });
    const activation = (activations.items || [])[0];
    if (!activation?.license_key_id) {
      ElMessage.warning('当前设备没有活动中的授权绑定');
      return;
    }

    await ElMessageBox.confirm(
      `确定解绑设备「${row.device_name || row.device_fingerprint}」吗？`,
      '解绑设备',
      { type: 'warning' },
    );

    unbinding.value = row.id;
    await unbindLicenseDeviceApi(activation.license_key_id, row.id, '管理员解绑');
    ElMessage.success('设备已解绑');
    loadData();
  } catch (error: any) {
    if (error === 'cancel') return;
    ElMessage.error(error?.message || '解绑失败');
  } finally {
    unbinding.value = '';
  }
}
</script>

<template>
  <Page auto-content-height>
    <ElCard shadow="never">
      <div class="mb-4 flex gap-3">
        <ElInput v-model="searchForm.device_fingerprint" placeholder="设备指纹" clearable style="width: 220px" />
        <ElInput v-model="searchForm.os_type" placeholder="系统类型" clearable style="width: 160px" />
        <ElSelect v-model="searchForm.status" clearable placeholder="状态" style="width: 140px">
          <ElOption label="活跃" value="active" />
          <ElOption label="已封禁" value="blocked" />
        </ElSelect>
        <ElButton type="primary" @click="onSearch">查询</ElButton>
      </div>

      <ElTable :data="devices" v-loading="loading" border>
        <ElTableColumn prop="device_name" label="设备名称" min-width="150" />
        <ElTableColumn prop="device_fingerprint" label="设备指纹" min-width="220" />
        <ElTableColumn prop="os_type" label="系统" width="120" />
        <ElTableColumn prop="os_version" label="系统版本" min-width="120" />
        <ElTableColumn prop="app_version" label="客户端版本" min-width="120" />
        <ElTableColumn prop="last_seen_at" label="最后在线" min-width="180" />
        <ElTableColumn label="状态" width="90">
          <template #default="{ row }">
            <ElTag :type="row.status === 'active' ? 'success' : 'danger'">{{ row.status }}</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <ElButton
              link
              type="danger"
              :loading="unbinding === row.id"
              @click="onUnbind(row)"
            >
              解绑
            </ElButton>
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
