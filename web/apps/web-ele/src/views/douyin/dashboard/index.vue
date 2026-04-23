<script lang="ts" setup>
import type {
  DashboardAccountRankItem,
  DashboardOverview,
  DashboardRuleHitItem,
  DashboardTrendPoint,
} from '#/api/core/douyin';

import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElCard,
  ElCol,
  ElProgress,
  ElRow,
  ElStatistic,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  getDashboardAccountRank,
  getDashboardOverview,
  getDashboardRuleHits,
  getDashboardTrend,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinDashboard' });

const overview = ref<DashboardOverview>();
const trend = ref<DashboardTrendPoint[]>([]);
const rank = ref<DashboardAccountRankItem[]>([]);
const ruleHits = ref<DashboardRuleHitItem[]>([]);
const loading = ref(false);

async function loadAll() {
  loading.value = true;
  try {
    const [ov, tr, rk, rh] = await Promise.all([
      getDashboardOverview(),
      getDashboardTrend({}),
      getDashboardAccountRank({}),
      getDashboardRuleHits(),
    ]);
    overview.value = ov;
    trend.value = tr.points || [];
    rank.value = rk.items || [];
    ruleHits.value = rh.items || [];
  } finally {
    loading.value = false;
  }
}

function onlineRatio(): number {
  if (!overview.value) return 0;
  const total = overview.value.accounts_total || 1;
  return Math.round((overview.value.accounts_online * 100) / total);
}

function successRatio(): number {
  if (!overview.value) return 0;
  const total
    = overview.value.replies_today + overview.value.replies_failed_today;
  if (!total) return 100;
  return Math.round((overview.value.replies_today * 100) / total);
}

onMounted(loadAll);
</script>

<template>
  <Page title="抖音托管看板" description="多账号实时概览 + 近 7 日趋势 + 排行">
    <ElRow v-loading="loading" :gutter="16" class="stat-row">
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic
            title="账号总数"
            :value="overview?.accounts_total ?? 0"
          />
          <div class="card-sub">
            <ElTag type="success">在线 {{ overview?.accounts_online ?? 0 }}</ElTag>
            <ElTag type="info">离线 {{ overview?.accounts_offline ?? 0 }}</ElTag>
          </div>
          <ElProgress
            :percentage="onlineRatio()"
            :stroke-width="6"
            :show-text="false"
          />
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic
            title="运行中会话"
            :value="overview?.sessions_running ?? 0"
            suffix=" 路"
          />
          <div class="card-sub">并发 worker 托管</div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic
            title="今日自动回复"
            :value="overview?.replies_today ?? 0"
          />
          <div class="card-sub">
            <ElTag type="success">成功率 {{ successRatio() }}%</ElTag>
            <ElTag type="danger">
              失败 {{ overview?.replies_failed_today ?? 0 }}
            </ElTag>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <ElStatistic
            title="今日收到消息"
            :value="overview?.messages_today ?? 0"
          />
          <div class="card-sub">
            平均响应 {{ overview?.avg_response_ms ?? 0 }} ms
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="16" class="mt-16">
      <ElCol :span="12">
        <ElCard shadow="hover" header="近 7 日回复趋势">
          <ElTable :data="trend" stripe size="small">
            <ElTableColumn prop="stat_date" label="日期" width="120" />
            <ElTableColumn prop="messages_received" label="消息数" />
            <ElTableColumn prop="replies_sent" label="成功回复" />
            <ElTableColumn prop="replies_failed" label="失败">
              <template #default="{ row }">
                <ElTag v-if="row.replies_failed > 0" type="danger" size="small">
                  {{ row.replies_failed }}
                </ElTag>
                <span v-else>0</span>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
      <ElCol :span="12">
        <ElCard shadow="hover" header="账号回复排行 TOP">
          <ElTable :data="rank" stripe size="small">
            <ElTableColumn label="#" type="index" width="50" />
            <ElTableColumn prop="nickname" label="账号" />
            <ElTableColumn prop="replies" label="回复数" width="90" sortable />
            <ElTableColumn prop="messages" label="消息数" width="90" />
            <ElTableColumn prop="response_ms" label="响应(ms)" width="100" />
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="16" class="mt-16">
      <ElCol :span="24">
        <ElCard shadow="hover" header="规则命中分布">
          <ElTable :data="ruleHits" stripe size="small" max-height="400">
            <ElTableColumn label="#" type="index" width="50" />
            <ElTableColumn prop="rule_name" label="规则名称" />
            <ElTableColumn prop="hit_count" label="累计命中" sortable />
          </ElTable>
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
</style>
