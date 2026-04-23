<script lang="ts" setup>
import type { DouyinSession } from '#/api/core/douyin';

import { onMounted, onUnmounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElMessage,
  ElMessageBox,
  ElProgress,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { controlSession, getLiveSessions } from '#/api/core/douyin';

defineOptions({ name: 'DouyinSession' });

const sessions = ref<DouyinSession[]>([]);
const loading = ref(false);
let timer: null | ReturnType<typeof setInterval> = null;

async function loadSessions() {
  loading.value = true;
  try {
    sessions.value = await getLiveSessions();
  } finally {
    loading.value = false;
  }
}

function statusType(status: string) {
  return (
    {
      idle: 'info',
      running: 'success',
      paused: 'warning',
      error: 'danger',
      stopped: '',
    }[status] || 'info'
  );
}

function statusLabel(status: string) {
  return (
    {
      idle: '空闲',
      running: '运行中',
      paused: '已暂停',
      error: '异常',
      stopped: '已停止',
    }[status] || status
  );
}

async function onControl(
  row: DouyinSession,
  action: 'pause' | 'restart' | 'resume' | 'stop',
) {
  const actionLabel = { pause: '暂停', resume: '恢复', stop: '停止', restart: '重启' }[action];
  await ElMessageBox.confirm(
    `确定 ${actionLabel} 账号 "${row.account_nickname}" 的会话？`,
    '提示',
    { type: 'warning' },
  );
  const res = await controlSession(row.id, action);
  if (res.success) ElMessage.success(res.message);
  else ElMessage.error(res.message);
  await loadSessions();
}

function timeSince(dt?: null | string): string {
  if (!dt) return '-';
  const diff = Date.now() - new Date(dt).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h`;
}

onMounted(() => {
  loadSessions();
  timer = setInterval(loadSessions, 5000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <Page
    title="并发会话监控"
    description="实时显示 worker 托管的每个账号浏览器会话状态，5 秒自动刷新"
  >
    <ElCard shadow="never">
      <template #header>
        <div class="card-header">
          <span>
            在线会话
            <ElTag type="success">{{ sessions.length }}</ElTag>
          </span>
          <ElButton size="small" @click="loadSessions">立即刷新</ElButton>
        </div>
      </template>

      <ElTable :data="sessions" v-loading="loading" stripe>
        <ElTableColumn prop="account_nickname" label="账号" min-width="140" />
        <ElTableColumn label="状态" width="90">
          <template #default="{ row }">
            <ElTag :type="statusType(row.status) as any" size="small">
              {{ statusLabel(row.status) }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="心跳" width="120">
          <template #default="{ row }">
            <span v-if="row.is_alive" class="alive-dot" />
            <span>{{ timeSince(row.last_heartbeat) }} 前</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="worker_id" label="Worker" min-width="160" show-overflow-tooltip />
        <ElTableColumn label="今日消息" width="90">
          <template #default="{ row }">
            {{ row.messages_today }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="今日回复" width="90">
          <template #default="{ row }">
            <span :class="{ 'text-warning': row.errors_today > 0 }">
              {{ row.replies_today }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="错误" width="70">
          <template #default="{ row }">
            <ElTag v-if="row.errors_today > 0" type="danger" size="small">
              {{ row.errors_today }}
            </ElTag>
            <span v-else>-</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="CPU" width="110">
          <template #default="{ row }">
            <ElProgress
              :percentage="Math.min(100, Math.round(row.cpu_percent))"
              :stroke-width="4"
              :show-text="true"
              :text-inside="false"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn label="内存" width="90">
          <template #default="{ row }">
            {{ Math.round(row.memory_mb) }} MB
          </template>
        </ElTableColumn>
        <ElTableColumn prop="proxy_url" label="代理" min-width="140" show-overflow-tooltip />
        <ElTableColumn label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <ElButton
              v-if="row.status === 'running'"
              link
              type="warning"
              size="small"
              @click="onControl(row, 'pause')"
            >
              暂停
            </ElButton>
            <ElButton
              v-if="row.status === 'paused'"
              link
              type="success"
              size="small"
              @click="onControl(row, 'resume')"
            >
              恢复
            </ElButton>
            <ElButton
              link
              type="primary"
              size="small"
              @click="onControl(row, 'restart')"
            >
              重启
            </ElButton>
            <ElButton
              link
              type="danger"
              size="small"
              @click="onControl(row, 'stop')"
            >
              停止
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </Page>
</template>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.alive-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  background: #67c23a;
  border-radius: 50%;
  box-shadow: 0 0 6px #67c23a;
  animation: pulse 1.6s infinite;
}

.text-warning {
  color: #e6a23c;
}

@keyframes pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.4);
    opacity: 0.6;
  }
}
</style>
