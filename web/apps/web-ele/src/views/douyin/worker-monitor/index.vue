<script lang="ts" setup>
import type { DouyinWorkerMonitorApi } from '#/api/core/douyin';

import { computed, onMounted, onUnmounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElAlert,
  ElButton,
  ElCard,
  ElCol,
  ElDescriptions,
  ElDescriptionsItem,
  ElEmpty,
  ElRow,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { DouyinWorkerMonitorApi as WorkerMonitorApi } from '#/api/core/douyin';

defineOptions({ name: 'DouyinWorkerMonitor' });

const loading = ref(false);
const overview = ref<DouyinWorkerMonitorApi.Overview | null>(null);
let timer: null | ReturnType<typeof setInterval> = null;

const issueCount = computed(() => overview.value?.issues.length ?? 0);
const errorCount = computed(
  () => overview.value?.issues.filter((i) => i.level === 'error').length ?? 0,
);

async function loadOverview() {
  loading.value = true;
  try {
    overview.value = await WorkerMonitorApi.getOverview();
  } finally {
    loading.value = false;
  }
}

function shardStatusType(status: string) {
  return (
    {
      ok: 'success',
      idle: 'info',
      partial: 'warning',
      missing_worker: 'danger',
    }[status] || 'info'
  );
}

function shardStatusLabel(status: string) {
  return (
    {
      ok: '正常',
      idle: '空闲',
      partial: '部分托管',
      missing_worker: '无 Worker',
    }[status] || status
  );
}

function workerStatusType(status: string) {
  return (
    { alive: 'success', stale: 'warning', dead: 'danger' }[status] || 'info'
  );
}

function workerStatusLabel(status: string) {
  return (
    { alive: '存活', stale: '心跳过期', dead: '已停止' }[status] || status
  );
}

function issueLevelType(level: string) {
  return (
    { error: 'error', warning: 'warning', info: 'info' }[level] || 'info'
  );
}

function formatTime(value?: null | string) {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

onMounted(() => {
  loadOverview();
  timer = setInterval(loadOverview, 5000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <Page auto-content-height>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h2 class="text-lg font-semibold">Worker 进程监控</h2>
        <p class="text-sm text-gray-500">
          分片分布 · Redis 租约 · 存活 worker · 重复托管检测（5s 自动刷新）
        </p>
      </div>
      <ElButton :loading="loading" type="primary" @click="loadOverview">
        刷新
      </ElButton>
    </div>

    <ElRow v-if="overview" :gutter="16" class="mb-4">
      <ElCol :lg="4" :md="8" :sm="12" :xs="24">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">分片数</div>
          <div class="text-2xl font-bold">{{ overview.shard_count }}</div>
        </ElCard>
      </ElCol>
      <ElCol :lg="4" :md="8" :sm="12" :xs="24">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">托管账号</div>
          <div class="text-2xl font-bold">{{ overview.managed_account_total }}</div>
        </ElCard>
      </ElCol>
      <ElCol :lg="4" :md="8" :sm="12" :xs="24">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">存活会话</div>
          <div class="text-2xl font-bold">{{ overview.active_session_total }}</div>
        </ElCard>
      </ElCol>
      <ElCol :lg="4" :md="8" :sm="12" :xs="24">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">Worker 进程</div>
          <div class="text-2xl font-bold">{{ overview.worker_process_total }}</div>
        </ElCard>
      </ElCol>
      <ElCol :lg="4" :md="8" :sm="12" :xs="24">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">Redis 租约</div>
          <div class="text-2xl font-bold">{{ overview.lease_total }}</div>
        </ElCard>
      </ElCol>
      <ElCol :lg="4" :md="8" :sm="12" :xs="24">
        <ElCard shadow="never">
          <div class="text-sm text-gray-500">告警</div>
          <div
            class="text-2xl font-bold"
            :class="errorCount > 0 ? 'text-red-500' : ''"
          >
            {{ issueCount }}
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElCard v-if="overview" class="mb-4" shadow="never">
      <ElDescriptions :column="3" border size="small" title="运行配置">
        <ElDescriptionsItem label="分片规则">
          {{ overview.shard_index_hint }}
        </ElDescriptionsItem>
        <ElDescriptionsItem label="租约">
          <ElTag :type="overview.lease_enabled ? 'success' : 'info'" size="small">
            {{ overview.lease_enabled ? `已开启 TTL=${overview.lease_ttl}s` : '未开启' }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem label="Redis">
          <ElTag :type="overview.redis_ok ? 'success' : 'danger'" size="small">
            {{ overview.redis_ok ? '正常' : '不可用' }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem label="最近采集">
          {{ formatTime(overview.timestamp) }}
        </ElDescriptionsItem>
      </ElDescriptions>
    </ElCard>

    <ElCard v-if="overview?.issues.length" class="mb-4" shadow="never">
      <template #header>
        <span class="font-medium">告警</span>
      </template>
      <div class="space-y-2">
        <ElAlert
          v-for="(issue, idx) in overview.issues"
          :key="`${issue.code}-${idx}`"
          :title="issue.title"
          :type="issueLevelType(issue.level)"
          :closable="false"
          show-icon
        >
          {{ issue.detail }}
        </ElAlert>
      </div>
    </ElCard>

    <ElRow v-if="overview" :gutter="16">
      <ElCol :lg="12" :xs="24">
        <ElCard class="mb-4" shadow="never">
          <template #header>
            <span class="font-medium">分片状态</span>
          </template>
          <ElTable :data="overview.shards" size="small" stripe>
            <ElTableColumn label="分片" prop="shard_index" width="70" />
            <ElTableColumn label="状态" width="110">
              <template #default="{ row }">
                <ElTag :type="shardStatusType(row.status)" size="small">
                  {{ shardStatusLabel(row.status) }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="账号" width="100">
              <template #default="{ row }">
                {{ row.active_account_count }} / {{ row.expected_account_count }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="Worker" min-width="160">
              <template #default="{ row }">
                <span v-if="row.worker_ids.length">{{ row.worker_ids.join(', ') }}</span>
                <span v-else class="text-gray-400">-</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="账号列表" min-width="180">
              <template #default="{ row }">
                <span v-if="row.accounts.length">
                  {{ row.accounts.map((a) => a.nickname).join('、') }}
                </span>
                <span v-else class="text-gray-400">无</span>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>

      <ElCol :lg="12" :xs="24">
        <ElCard class="mb-4" shadow="never">
          <template #header>
            <span class="font-medium">Worker 进程</span>
          </template>
          <ElTable
            v-if="overview.workers.length"
            :data="overview.workers"
            size="small"
            stripe
          >
            <ElTableColumn label="Worker ID" min-width="140" prop="worker_id" />
            <ElTableColumn label="分片" width="70">
              <template #default="{ row }">
                {{ row.inferred_shard_index ?? '-' }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="100">
              <template #default="{ row }">
                <ElTag :type="workerStatusType(row.status)" size="small">
                  {{ workerStatusLabel(row.status) }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="账号数" prop="account_count" width="80" />
            <ElTableColumn label="租约" prop="lease_count" width="70" />
            <ElTableColumn label="内存 MB" prop="memory_mb" width="90" />
            <ElTableColumn label="最近心跳" min-width="150">
              <template #default="{ row }">
                {{ formatTime(row.last_heartbeat) }}
              </template>
            </ElTableColumn>
          </ElTable>
          <ElEmpty v-else description="暂无 Worker 会话" />
        </ElCard>
      </ElCol>
    </ElRow>

    <ElCard v-if="overview" shadow="never">
      <template #header>
        <span class="font-medium">Redis 租约</span>
      </template>
      <ElTable
        v-if="overview.leases.length"
        :data="overview.leases"
        size="small"
        stripe
      >
        <ElTableColumn label="账号" prop="nickname" min-width="120" />
        <ElTableColumn label="租约持有者" prop="holder_worker_id" min-width="140" />
        <ElTableColumn label="会话 Worker" min-width="140">
          <template #default="{ row }">
            {{ row.session_worker_id || '-' }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="TTL(s)" prop="ttl_seconds" width="80" />
        <ElTableColumn label="一致" width="80">
          <template #default="{ row }">
            <ElTag :type="row.consistent ? 'success' : 'danger'" size="small">
              {{ row.consistent ? '是' : '否' }}
            </ElTag>
          </template>
        </ElTableColumn>
      </ElTable>
      <ElEmpty v-else description="暂无租约（租约未开启或 Redis 不可用）" />
    </ElCard>
  </Page>
</template>

<style scoped lang="scss">
.mb-4 {
  margin-bottom: 16px;
}

.space-y-2 > * + * {
  margin-top: 8px;
}
</style>
