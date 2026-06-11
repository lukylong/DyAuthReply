<script lang="ts" setup>
import type { KuaishouSession } from '#/api/core/kuaishou';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElMessage,
  ElOption,
  ElPagination,
  ElSelect,
  ElSpace,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  controlKuaishouSession,
  getKuaishouSessionList,
} from '#/api/core/kuaishou';

defineOptions({ name: 'KuaishouSession' });

const STATUS_META: Record<string, { text: string; type: string }> = {
  idle: { text: '空闲', type: 'info' },
  running: { text: '运行中', type: 'success' },
  paused: { text: '已暂停', type: 'warning' },
  stopped: { text: '已停止', type: 'info' },
  error: { text: '异常', type: 'danger' },
};

const rows = ref<KuaishouSession[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 20, total: 0 });
const search = reactive({ status: undefined as string | undefined });

async function load() {
  loading.value = true;
  try {
    const res = await getKuaishouSessionList({
      page: page.page,
      page_size: page.page_size,
      status: search.status,
    });
    rows.value = res.items || [];
    page.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

async function onControl(
  row: KuaishouSession,
  action: 'pause' | 'restart' | 'resume' | 'stop',
) {
  try {
    const res = await controlKuaishouSession(row.id, action);
    ElMessage.success(`已下发指令，当前状态：${res.status}`);
    load();
  } catch (e: unknown) {
    ElMessage.error((e as Error).message);
  }
}

onMounted(load);
</script>

<template>
  <Page title="会话监控" description="并发 worker 会话实时状态与控制（协议接入后数据自动上报）">
    <ElSpace wrap class="toolbar">
      <ElSelect
        v-model="search.status"
        placeholder="状态"
        clearable
        style="width: 130px"
      >
        <ElOption label="空闲" value="idle" />
        <ElOption label="运行中" value="running" />
        <ElOption label="已暂停" value="paused" />
        <ElOption label="已停止" value="stopped" />
        <ElOption label="异常" value="error" />
      </ElSelect>
      <ElButton type="primary" @click="load">查询</ElButton>
    </ElSpace>

    <ElTable :data="rows" v-loading="loading" stripe>
      <ElTableColumn prop="account_nickname" label="账号" min-width="140" />
      <ElTableColumn label="状态" width="100">
        <template #default="{ row }">
          <ElTag :type="(STATUS_META[row.status]?.type as any) || 'info'" size="small">
            {{ STATUS_META[row.status]?.text || row.status }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="worker_id" label="Worker" width="150" show-overflow-tooltip />
      <ElTableColumn label="今日消息/回复" width="130">
        <template #default="{ row }">
          {{ row.messages_today }} / {{ row.replies_today }}
        </template>
      </ElTableColumn>
      <ElTableColumn prop="errors_today" label="今日错误" width="90" />
      <ElTableColumn prop="last_heartbeat" label="最近心跳" width="170" />
      <ElTableColumn label="资源" width="130">
        <template #default="{ row }">
          {{ row.cpu_percent }}% / {{ row.memory_mb }}MB
        </template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <ElButton link type="warning" size="small" @click="onControl(row, 'pause')">
            暂停
          </ElButton>
          <ElButton link type="success" size="small" @click="onControl(row, 'resume')">
            恢复
          </ElButton>
          <ElButton link type="primary" size="small" @click="onControl(row, 'restart')">
            重启
          </ElButton>
          <ElButton link type="danger" size="small" @click="onControl(row, 'stop')">
            停止
          </ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pager">
      <ElPagination
        v-model:current-page="page.page"
        v-model:page-size="page.page_size"
        :total="page.total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="load"
      />
    </div>
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
