<script lang="ts" setup>
import type { CredentialStatusItem, CredentialStatusResponse } from '#/api/core/douyin';

import { computed, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElEmpty,
  ElMessage,
  ElSpace,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { getCredentialStatusApi } from '#/api/core/douyin';

defineOptions({ name: 'DouyinCookieManagement' });

const loading = ref(false);
const statusData = ref<CredentialStatusResponse>({
  accounts: [],
  duplicates: {},
});

const CREDENTIAL_TAG: Record<string, { label: string; type: string }> = {
  sendable: { label: '可发送', type: 'success' },
  receive_only: { label: '仅接收', type: 'warning' },
  invalid: { label: '已失效', type: 'danger' },
  unknown: { label: '未知', type: 'info' },
};

const STATUS_TAG_TYPE: Record<number, string> = {
  0: 'info',
  1: 'success',
  2: 'danger',
  3: 'info',
};

const STATUS_TEXT: Record<number, string> = {
  0: '未登录',
  1: '在线',
  2: '登录失效',
  3: '已禁用',
};

// 计算统计信息
const stats = computed(() => {
  const accounts = statusData.value.accounts;
  return {
    total: accounts.length,
    online: accounts.filter(a => a.status === 1).length,
    sendable: accounts.filter(a => a.credential_state === 'sendable').length,
    receiveOnly: accounts.filter(a => a.credential_state === 'receive_only').length,
    invalid: accounts.filter(a => a.credential_state === 'invalid').length,
    duplicates: Object.keys(statusData.value.duplicates).length,
  };
});

// 判断账号是否重复
function isDuplicate(item: CredentialStatusItem): boolean {
  if (!item.sec_uid) return false;
  const duplicateList = statusData.value.duplicates[item.sec_uid];
  return duplicateList && duplicateList.length > 1;
}

// 获取重复账号的数量
function getDuplicateCount(item: CredentialStatusItem): number {
  if (!item.sec_uid) return 0;
  const duplicateList = statusData.value.duplicates[item.sec_uid];
  return duplicateList ? duplicateList.length : 0;
}

// 格式化时间
function formatTime(dateStr?: string): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// 加载数据
async function loadData() {
  loading.value = true;
  try {
    statusData.value = await getCredentialStatusApi();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载失败');
  } finally {
    loading.value = false;
  }
}

// 刷新
function onRefresh() {
  loadData();
}

onMounted(() => {
  loadData();
});
</script>

<template>
  <Page
    title="Cookie 管理"
    description="查看所有账号的凭证状态，检测重复的登录态"
  >
    <!-- 统计卡片 -->
    <div class="stats-cards">
      <div class="stat-card">
        <div class="stat-label">总账号数</div>
        <div class="stat-value">{{ stats.total }}</div>
      </div>
      <div class="stat-card success">
        <div class="stat-label">在线账号</div>
        <div class="stat-value">{{ stats.online }}</div>
      </div>
      <div class="stat-card success">
        <div class="stat-label">可发送</div>
        <div class="stat-value">{{ stats.sendable }}</div>
      </div>
      <div class="stat-card warning">
        <div class="stat-label">仅接收</div>
        <div class="stat-value">{{ stats.receiveOnly }}</div>
      </div>
      <div class="stat-card danger">
        <div class="stat-label">已失效</div>
        <div class="stat-value">{{ stats.invalid }}</div>
      </div>
      <div class="stat-card danger">
        <div class="stat-label">重复登录态</div>
        <div class="stat-value">{{ stats.duplicates }}</div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div style="margin-bottom: 16px">
      <ElSpace>
        <ElButton type="primary" :loading="loading" @click="onRefresh">
          刷新
        </ElButton>
      </ElSpace>
    </div>

    <!-- 表格 -->
    <ElTable
      v-loading="loading"
      :data="statusData.accounts"
      stripe
      style="width: 100%"
    >
      <ElTableColumn prop="nickname" label="昵称" width="180">
        <template #default="{ row }">
          <div style="display: flex; align-items: center; gap: 8px">
            <img
              v-if="row.avatar"
              :src="row.avatar"
              alt=""
              style="width: 32px; height: 32px; border-radius: 50%"
            />
            <span>{{ row.nickname }}</span>
          </div>
        </template>
      </ElTableColumn>

      <ElTableColumn prop="sec_uid" label="Sec UID" min-width="200">
        <template #default="{ row }">
          <div v-if="row.sec_uid" style="font-family: monospace; font-size: 12px">
            {{ row.sec_uid.substring(0, 30) }}...
          </div>
          <span v-else class="text-gray-400">-</span>
        </template>
      </ElTableColumn>

      <ElTableColumn label="账号状态" width="100">
        <template #default="{ row }">
          <ElTag :type="STATUS_TAG_TYPE[row.status]" size="small">
            {{ STATUS_TEXT[row.status] }}
          </ElTag>
        </template>
      </ElTableColumn>

      <ElTableColumn label="凭证状态" width="100">
        <template #default="{ row }">
          <ElTag
            :type="CREDENTIAL_TAG[row.credential_state]?.type || 'info'"
            size="small"
          >
            {{ CREDENTIAL_TAG[row.credential_state]?.label || row.credential_state }}
          </ElTag>
        </template>
      </ElTableColumn>

      <ElTableColumn label="凭证文件" width="100">
        <template #default="{ row }">
          <ElTag
            :type="row.storage_state_exists ? 'success' : 'danger'"
            size="small"
          >
            {{ row.storage_state_exists ? '存在' : '缺失' }}
          </ElTag>
        </template>
      </ElTableColumn>

      <ElTableColumn label="发送能力" width="100">
        <template #default="{ row }">
          <ElTag
            :type="row.has_send_credential ? 'success' : 'info'"
            size="small"
          >
            {{ row.has_send_credential ? '可发送' : '仅接收' }}
          </ElTag>
        </template>
      </ElTableColumn>

      <ElTableColumn label="最后登录" width="150">
        <template #default="{ row }">
          {{ formatTime(row.last_login_at) }}
        </template>
      </ElTableColumn>

      <ElTableColumn label="重复检测" width="120">
        <template #default="{ row }">
          <ElTag v-if="isDuplicate(row)" type="danger" size="small">
            重复 ({{ getDuplicateCount(row) }} 个)
          </ElTag>
          <span v-else class="text-gray-400">-</span>
        </template>
      </ElTableColumn>
    </ElTable>

    <ElEmpty
      v-if="!loading && statusData.accounts.length === 0"
      description="暂无账号"
    />
  </Page>
</template>

<style scoped lang="scss">
.stats-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  padding: 20px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  text-align: center;

  &.success {
    border-color: #52c41a;
    background: #f6ffed;
  }

  &.warning {
    border-color: #faad14;
    background: #fffbe6;
  }

  &.danger {
    border-color: #ff4d4f;
    background: #fff1f0;
  }
}

.stat-label {
  font-size: 14px;
  color: #8c8c8c;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 600;
  color: #262626;
}
</style>
