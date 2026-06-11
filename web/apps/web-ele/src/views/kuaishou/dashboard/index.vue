<script lang="ts" setup>
import type { KuaishouDashboardOverview } from '#/api/core/kuaishou';

import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElCol,
  ElProgress,
  ElRow,
  ElStatistic,
  ElTag,
} from 'element-plus';

import { getKuaishouDashboardOverview } from '#/api/core/kuaishou';

defineOptions({ name: 'KuaishouDashboard' });

const overview = ref<KuaishouDashboardOverview>();
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    overview.value = await getKuaishouDashboardOverview();
  } finally {
    loading.value = false;
  }
}

function onlineRatio(): number {
  if (!overview.value) return 0;
  const total = overview.value.total_accounts || 1;
  return Math.round((overview.value.online_accounts * 100) / total);
}

function successRatio(): number {
  if (!overview.value) return 0;
  const total = overview.value.success_today + overview.value.failed_today;
  if (!total) return 100;
  return Math.round((overview.value.success_today * 100) / total);
}

onMounted(load);
</script>

<template>
  <Page title="快手托管看板" description="多账号实时概览（协议接入中，数据随 worker 上报逐步完善）">
    <ElRow v-loading="loading" :gutter="16" class="stat-row">
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic title="账号总数" :value="overview?.total_accounts ?? 0" />
          <div class="card-sub">
            <ElTag type="success">在线 {{ overview?.online_accounts ?? 0 }}</ElTag>
          </div>
          <ElProgress :percentage="onlineRatio()" :stroke-width="6" :show-text="false" />
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic
            title="运行中会话"
            :value="overview?.running_sessions ?? 0"
            suffix=" 路"
          />
          <div class="card-sub">共 {{ overview?.total_sessions ?? 0 }} 路托管</div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic title="今日自动回复" :value="overview?.replies_today ?? 0" />
          <div class="card-sub">
            <ElTag type="success">成功率 {{ successRatio() }}%</ElTag>
            <ElTag type="danger">失败 {{ overview?.failed_today ?? 0 }}</ElTag>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic title="今日收到消息" :value="overview?.messages_today ?? 0" />
          <div class="card-sub">
            <ElTag v-if="(overview?.unread_events ?? 0) > 0" type="danger">
              未读告警 {{ overview?.unread_events }}
            </ElTag>
            <span v-else>暂无未读告警</span>
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="16" class="mt-16">
      <ElCol :span="24">
        <ElCard shadow="hover" header="说明">
          <p class="hint">
            快手私信协议逆向接入完成后，worker 会通过心跳上报实时指标，本看板的会话、消息、回复数据将自动填充。
          </p>
          <ElButton type="primary" @click="load">刷新数据</ElButton>
        </ElCard>
      </ElCol>
    </ElRow>
  </Page>
</template>

<style scoped>
.stat-row {
  margin-bottom: 16px;
}

.card-sub {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}

.mt-16 {
  margin-top: 16px;
}

.hint {
  margin-bottom: 12px;
  font-size: 13px;
  color: #909399;
}
</style>
