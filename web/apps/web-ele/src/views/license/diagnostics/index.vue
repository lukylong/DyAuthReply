<script lang="ts" setup>
import type {
  DiagnosticDevice,
  DiagnosticDeviceDetail,
  DiagnosticEvent,
} from '#/api/core/license-diagnostics';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElDrawer,
  ElInput,
  ElOption,
  ElPagination,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  getDiagnosticDeviceApi,
  getDiagnosticDevicesApi,
  getDiagnosticEventsApi,
} from '#/api/core/license-diagnostics';

defineOptions({ name: 'LicenseDiagnostics' });

const loading = ref(false);
const rows = ref<DiagnosticDevice[]>([]);
const filters = reactive({ app_version: '', os: '' });
const pagination = reactive({ page: 1, pageSize: 10, total: 0 });

const drawerOpen = ref(false);
const detail = ref<DiagnosticDeviceDetail | null>(null);
const events = ref<DiagnosticEvent[]>([]);
const eventsLoading = ref(false);
const eventFilter = reactive({ level: '' });
const eventPage = reactive({ page: 1, pageSize: 20, total: 0 });

async function loadData() {
  loading.value = true;
  try {
    const res = await getDiagnosticDevicesApi({
      ...filters,
      page: pagination.page,
      pageSize: pagination.pageSize,
    });
    rows.value = res.items || [];
    pagination.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

async function loadEvents() {
  if (!detail.value) return;
  eventsLoading.value = true;
  try {
    const res = await getDiagnosticEventsApi(detail.value.device_id, {
      level: eventFilter.level,
      page: eventPage.page,
      pageSize: eventPage.pageSize,
    });
    events.value = res.items || [];
    eventPage.total = res.total || 0;
  } finally {
    eventsLoading.value = false;
  }
}

async function openDevice(row: DiagnosticDevice) {
  drawerOpen.value = true;
  detail.value = await getDiagnosticDeviceApi(row.device_id);
  eventPage.page = 1;
  await loadEvents();
}

function search() {
  pagination.page = 1;
  loadData();
}

function levelType(level: string) {
  if (level === 'ERROR') return 'danger';
  if (level === 'WARN') return 'warning';
  return 'info';
}

function accountTag(value?: string) {
  if (!value) return 'info';
  if (['owned', 'running', 'sendable', 'ws_healthy'].includes(value))
    return 'success';
  if (
    [
      'auth_expired',
      'expired',
      'invalid',
      'lost',
      'risk_controlled',
      'stopped',
    ].includes(value)
  ) {
    return 'danger';
  }
  return 'warning';
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

onMounted(loadData);
</script>

<template>
  <Page auto-content-height>
    <ElCard shadow="never">
      <p class="mb-3 text-sm text-gray-500">
        仅显示在客户端「设置 →
        运行诊断」中手动开启上报的设备（默认关闭）。数据已脱敏：不含私信内容、昵称、抖音账号标识与凭证；账号以不可逆编号显示。保留
        30 天。
      </p>
      <div class="mb-4 flex flex-wrap gap-2">
        <ElInput
          v-model="filters.app_version"
          placeholder="客户端版本，如 0.1.36"
          clearable
          style="width: 200px"
          @keyup.enter="search"
        />
        <ElSelect
          v-model="filters.os"
          placeholder="系统"
          clearable
          style="width: 140px"
        >
          <ElOption label="Windows" value="windows" />
          <ElOption label="macOS" value="macos" />
        </ElSelect>
        <ElButton type="primary" @click="search">查询</ElButton>
      </div>

      <ElTable :data="rows" v-loading="loading" border @row-click="openDevice">
        <ElTableColumn label="设备" min-width="200">
          <template #default="{ row }">
            <div>{{ row.device_name || '未命名设备' }}</div>
            <div class="text-xs text-gray-400">
              {{ shortId(row.device_fingerprint) }}
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="app_version" label="版本" min-width="100" />
        <ElTableColumn label="系统" min-width="140">
          <template #default="{ row }">{{ row.os }} / {{ row.arch }}</template>
        </ElTableColumn>
        <ElTableColumn label="运行" min-width="100">
          <template #default="{ row }">
            <ElTag
              size="small"
              :type="row.runtime_phase === 'running' ? 'success' : 'warning'"
            >
              {{ row.runtime_phase || '未知' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="24 小时" min-width="140">
          <template #default="{ row }">
            <ElTag size="small" :type="row.error_24h ? 'danger' : 'info'">
              错误 {{ row.error_24h }}
            </ElTag>
            <ElTag
              size="small"
              class="ml-1"
              :type="row.warn_24h ? 'warning' : 'info'"
            >
              告警 {{ row.warn_24h }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="托管账号" min-width="260">
          <template #default="{ row }">
            <div
              v-for="account in row.accounts"
              :key="account.account"
              class="account-line"
            >
              <span class="text-xs text-gray-400">{{ account.account }}</span>
              <ElTag size="small" :type="accountTag(account.inbound)">
                收 {{ account.inbound }}
              </ElTag>
              <ElTag size="small" :type="accountTag(account.send)">
                发 {{ account.send }}
              </ElTag>
            </div>
            <span v-if="row.accounts.length === 0" class="muted">无</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="last_report_at" label="最近上报" min-width="180" />
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

    <ElDrawer
      v-model="drawerOpen"
      :title="detail?.device_name || '设备诊断'"
      size="60%"
    >
      <template v-if="detail">
        <p class="mb-2 text-sm text-gray-500">
          {{ detail.app_version }} · {{ detail.os }}/{{ detail.arch }} ·
          累计上报 {{ detail.report_count }} 次 · 最近
          {{ detail.last_report_at }}
        </p>
        <pre class="payload mb-4">{{
          JSON.stringify(detail.snapshot, null, 2)
        }}</pre>
        <div class="mb-2 flex gap-2">
          <ElSelect
            v-model="eventFilter.level"
            placeholder="全部级别"
            clearable
            style="width: 140px"
            @change="
              eventPage.page = 1;
              loadEvents();
            "
          >
            <ElOption label="错误" value="ERROR" />
            <ElOption label="告警" value="WARN" />
            <ElOption label="关键事件" value="INFO" />
          </ElSelect>
        </div>
        <ElTable :data="events" v-loading="eventsLoading" border size="small">
          <ElTableColumn prop="occurred_at" label="时间" min-width="170" />
          <ElTableColumn label="级别" width="80">
            <template #default="{ row }">
              <ElTag size="small" :type="levelType(row.level)">
                {{ row.level }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="message" label="事件" min-width="220" />
          <ElTableColumn label="字段" min-width="260">
            <template #default="{ row }">
              <pre class="payload">{{
                JSON.stringify(row.fields, null, 1)
              }}</pre>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="target" label="模块" min-width="200" />
        </ElTable>
        <div class="mt-3 flex justify-end">
          <ElPagination
            v-model:current-page="eventPage.page"
            v-model:page-size="eventPage.pageSize"
            layout="total, prev, pager, next"
            :total="eventPage.total"
            @current-change="loadEvents"
          />
        </div>
      </template>
    </ElDrawer>
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

.muted {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.account-line {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-bottom: 2px;
}
</style>
