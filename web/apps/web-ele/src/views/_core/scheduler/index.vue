<script lang="ts" setup>
import type { SchedulerJob } from '#/api/core/scheduler';

import { computed, onMounted, ref } from 'vue';

import { $t } from '@vben/locales';
import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import { ElCard, ElMessage, ElSkeleton, ElSkeletonItem } from 'element-plus';

import {
  getSchedulerJobDetailApi,
  getSchedulerJobStatisticsApi,
  getSchedulerStatusApi,
} from '#/api/core/scheduler';

import SchedulerJobDetail from './modules/scheduler-job-detail.vue';
import SchedulerListCard from './modules/scheduler-list-card.vue';

defineOptions({ name: 'SystemNewSchedule' });

interface StatsItem {
  icon: string;
  title: string;
  value: number | string;
  subtitle: string;
  type: 'danger' | 'info' | 'primary' | 'success' | 'warning';
  suffix: string;
  isText?: boolean;
}

const statistics = ref<any>(null);
const schedulerStatus = ref<any>(null);
const statsLoading = ref(false);
const selectedJobId = ref<string>();
const selectedJob = ref<null | SchedulerJob>(null);

// 统计卡片数据
const statsItems = computed<StatsItem[]>(() => {
  // 如果选中了 job，显示该 job 的统计数据
  if (selectedJob.value) {
    const successRate =
      selectedJob.value.total_run_count > 0
        ? Math.round(
            (selectedJob.value.success_count /
              selectedJob.value.total_run_count) *
              100,
          )
        : 0;
    const failureCount =
      (selectedJob.value.total_run_count || 0) -
      (selectedJob.value.success_count || 0);

    return [
      {
        icon: 'mdi:play-circle',
        title: $t('scheduler.executionCount'),
        value: selectedJob.value.total_run_count || 0,
        subtitle: $t('scheduler.totalRunCount'),
        type: 'info',
        suffix: '',
      },
      {
        icon: 'mdi:close-circle',
        title: $t('scheduler.failureCount'),
        value: failureCount,
        subtitle: $t('scheduler.executionFailed'),
        type: 'danger',
        suffix: '',
      },
      {
        icon: 'mdi:trending-up',
        title: $t('scheduler.successRate'),
        value: successRate,
        subtitle: $t('scheduler.jobSuccessRate'),
        type: 'success',
        suffix: '%',
      },
      {
        icon: 'mdi:check-circle',
        title: $t('scheduler.successCount'),
        value: selectedJob.value.success_count || 0,
        subtitle: $t('scheduler.executionSuccess'),
        type: 'success',
        suffix: '',
      },
    ];
  }

  // 否则显示全局统计数据
  return [
    {
      icon: 'mdi:list-box',
      title: $t('scheduler.totalJobs'),
      value: statistics.value?.total_jobs || 0,
      subtitle: $t('scheduler.allJobs'),
      type: 'primary',
      suffix: '',
    },
    {
      icon: 'mdi:check-circle',
      title: $t('scheduler.enabledJobs'),
      value: statistics.value?.enabled_jobs || 0,
      subtitle: $t('scheduler.runningJobs'),
      type: 'warning',
      suffix: '',
    },
    {
      icon: 'mdi:play-circle',
      title: $t('scheduler.totalExecutions'),
      value: statistics.value?.total_executions || 0,
      subtitle: $t('scheduler.executionRecords'),
      type: 'info',
      suffix: '',
    },
    {
      icon: 'mdi:trending-up',
      title: $t('scheduler.successRate'),
      value: statistics.value?.success_rate || 0,
      subtitle: $t('scheduler.jobSuccessRate'),
      type: 'success',
      suffix: '%',
    },
  ];
});

/**
 * 获取统计信息
 */
async function fetchStatistics() {
  try {
    statsLoading.value = true;
    statistics.value = await getSchedulerJobStatisticsApi();
  } finally {
    statsLoading.value = false;
  }
}

/**
 * 获取调度器状态
 */
async function fetchSchedulerStatus() {
  try {
    schedulerStatus.value = await getSchedulerStatusApi();
  } catch {
    ElMessage.error($t('scheduler.fetchStatusFailed'));
  }
}

/**
 * 获取选中任务的详情
 */
async function fetchSelectedJobDetail(jobId: string) {
  try {
    const job = await getSchedulerJobDetailApi(jobId);
    selectedJob.value = job;
  } catch {
    ElMessage.error($t('scheduler.fetchDetailFailed'));
    selectedJob.value = null;
  }
}

/**
 * 处理任务选择
 */
function onJobSelect(jobId: string | undefined) {
  selectedJobId.value = jobId;

  if (jobId) {
    fetchSelectedJobDetail(jobId);
  } else {
    selectedJob.value = null;
  }
}

/**
 * 处理任务列表刷新
 */
async function onListRefresh() {
  await fetchStatistics();
}

onMounted(async () => {
  // 并行请求统计信息和调度器状态
  await Promise.all([fetchStatistics(), fetchSchedulerStatus()]);
});
</script>

<template>
  <Page auto-content-height>
    <!-- 主内容区：左侧（调度器+列表） + 右侧（统计+详情） -->
    <div class="flex h-full overflow-hidden">
      <!-- 左侧区域 -->
      <div class="flex w-1/6 flex-col overflow-hidden" style="flex: 0 0 auto">
        <!-- 任务列表卡片 -->
        <div class="flex-1 overflow-hidden">
          <SchedulerListCard
            :selected-id="selectedJobId"
            @select="onJobSelect"
            @refresh="onListRefresh"
          />
        </div>
      </div>

      <!-- 右侧区域 -->
      <div class="flex flex-1 flex-col gap-4 overflow-hidden">
        <!-- 统计卡片网格（5列：调度器1个 + 统计4个） -->
        <ElSkeleton :loading="statsLoading" animated>
          <template #template>
            <div
              class="grid gap-3"
              style="grid-template-columns: repeat(5, 1fr)"
            >
              <!-- 统计卡片骨架（4个） -->
              <template v-for="_ in 5" :key="_">
                <ElCard shadow="hover">
                  <ElSkeletonItem
                    variant="text"
                    style="width: 60%; height: 16px; margin-bottom: 12px"
                  />
                  <ElSkeletonItem
                    variant="text"
                    style="width: 70%; height: 32px"
                  />
                </ElCard>
              </template>
            </div>
          </template>
          <template #default>
            <div
              class="grid gap-3"
              style="grid-template-columns: repeat(5, 1fr)"
            >
              <!-- 调度器控制卡片 -->
              <ElCard
                class="cursor-pointer bg-blue-50 transition-all dark:bg-blue-900/20"
                shadow="hover"
              >
                <!-- 卡片头部 -->
                <div class="mb-2 flex items-start justify-between">
                  <div class="flex-1">
                    <div class="text-sm font-medium dark:text-blue-400">
                      {{ $t('scheduler.runStatus') }}
                    </div>
                  </div>
                  <IconifyIcon
                    icon="mdi:cog"
                    class="size-6 opacity-70 dark:text-blue-400"
                  />
                </div>

                <!-- 状态显示 -->
                <div class="mb-1">
                  <div class="text-3xl font-bold">
                    <span
                      class="inline-block rounded-full py-1 text-base font-medium"
                      :class="{
                        'text-green-700': schedulerStatus?.is_running,
                        'text-red-700': !schedulerStatus?.is_running,
                      }"
                    >
                      {{ schedulerStatus?.is_running ? $t('scheduler.running') : $t('scheduler.stopped') }}
                    </span>
                  </div>
                </div>
              </ElCard>

              <!-- 统计卡片 -->
              <template v-for="item in statsItems" :key="item.title">
                <ElCard
                  class="stats-card cursor-pointer transition-all"
                  :class="`stats-${item.type}`"
                  shadow="hover"
                >
                  <!-- 卡片头部 -->
                  <div class="mb-2 flex items-start justify-between">
                    <div class="flex-1">
                      <div class="stats-title text-sm font-medium">
                        {{ item.title }}
                      </div>
                    </div>
                    <IconifyIcon
                      :icon="item.icon"
                      class="stats-icon size-6 opacity-70"
                    />
                  </div>

                  <!-- 数值 - 文本类型 -->
                  <div v-if="item.isText" class="mb-1">
                    <div class="stats-value truncate text-sm">
                      {{ item.value }}
                    </div>
                  </div>

                  <!-- 数值 - 数字类型 -->
                  <div v-else class="mb-1">
                    <div class="stats-value text-3xl font-bold">
                      {{ item.value
                      }}<span
                        v-if="item.suffix"
                        class="ml-1 text-lg font-semibold"
                        >{{ item.suffix }}</span>
                    </div>
                  </div>
                </ElCard>
              </template>
            </div>
          </template>
        </ElSkeleton>

        <!-- 任务详情 -->
        <div class="flex-1 overflow-hidden">
          <SchedulerJobDetail :job-id="selectedJobId" />
        </div>
      </div>
    </div>
  </Page>
</template>

<style scoped>
:deep(.el-card) {
  border: none;
  box-shadow: 0 1px 3px rgb(0 0 0 / 10%);
}

/* 统计卡片样式 */
.stats-card {
  transition: all 0.3s ease;
}

.stats-card.stats-success .stats-title {
  color: var(--el-color-success);
}

.stats-card.stats-success .stats-value {
  color: var(--el-color-success);
}

.stats-card.stats-success .stats-icon {
  color: var(--el-color-success);
}

/* Warning 类型 */
.stats-card.stats-warning .stats-title {
  color: var(--el-color-warning);
}

.stats-card.stats-warning .stats-value {
  color: var(--el-color-warning);
}

.stats-card.stats-warning .stats-icon {
  color: var(--el-color-warning);
}

/* Danger 类型 */
.stats-card.stats-danger .stats-title {
  color: var(--el-color-danger);
}

.stats-card.stats-danger .stats-value {
  color: var(--el-color-danger);
}

.stats-card.stats-danger .stats-icon {
  color: var(--el-color-danger);
}

/* Info 类型 */
.stats-card.stats-info .stats-title {
  color: var(--el-color-info);
}

.stats-card.stats-info .stats-value {
  color: var(--el-color-info);
}

.stats-card.stats-info .stats-icon {
  color: var(--el-color-info);
}

/* Primary 类型 */
.stats-card.stats-primary .stats-title {
  color: var(--el-color-primary);
}

.stats-card.stats-primary .stats-value {
  color: var(--el-color-primary);
}

.stats-card.stats-primary .stats-icon {
  color: var(--el-color-primary);
}
</style>
