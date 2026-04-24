<script lang="ts" setup>
import type {
  OnActionClickParams,
  VxeTableGridOptions,
} from '#/adapter/vxe-table';
import type { DouyinReplyLog, DouyinReplyLogStat } from '#/api/core/douyin';

import { computed, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElCard,
  ElCol,
  ElRow,
  ElStatistic,
} from 'element-plus';

import { useVbenVxeGrid } from '#/adapter/vxe-table';
import {
  getDouyinReplyLogListApi,
  getDouyinReplyLogStatApi,
} from '#/api/core/douyin';

import { useReplyLogTableColumns, useSearchFormSchema } from './data';
import DetailDrawer from './modules/detail-drawer.vue';

defineOptions({ name: 'DouyinReplyLog' });

const stat = ref<DouyinReplyLogStat | null>(null);
const detailDrawerRef = ref<InstanceType<typeof DetailDrawer>>();
const currentLog = ref<DouyinReplyLog>();
const currentAccountId = ref<string | undefined>();

const successRate = computed(() => {
  if (!stat.value || !stat.value.total) return 0;
  return Math.round((stat.value.success / stat.value.total) * 100);
});

async function loadStat(accountId?: string) {
  try {
    stat.value = await getDouyinReplyLogStatApi(accountId);
  } catch {
    stat.value = null;
  }
}

function onDetail(row: DouyinReplyLog) {
  currentLog.value = row;
  detailDrawerRef.value?.open();
}

function onActionClick({ code, row }: OnActionClickParams<DouyinReplyLog>) {
  if (code === 'detail') {
    onDetail(row);
  }
}

const [Grid, gridApi] = useVbenVxeGrid({
  formOptions: {
    schema: useSearchFormSchema(),
    submitOnChange: true,
    collapsed: false,
  },
  gridOptions: {
    columns: useReplyLogTableColumns(onActionClick),
    height: 'auto',
    keepSource: true,
    proxyConfig: {
      ajax: {
        query: async ({ page }, formValues) => {
          currentAccountId.value = formValues?.account_id;
          loadStat(formValues?.account_id);
          return await getDouyinReplyLogListApi({
            page: page.currentPage,
            pageSize: page.pageSize,
            ...formValues,
          });
        },
      },
    },
    rowConfig: { keyField: 'id' },
    toolbarConfig: {
      custom: true,
      export: false,
      refresh: true,
      search: true,
      zoom: true,
    },
  } as VxeTableGridOptions<DouyinReplyLog>,
});

// 当 account 筛选变化时主动刷新统计（proxy 内部已处理一次，这里兜底）
watch(currentAccountId, () => {
  // intentionally empty — statistics already refreshed inside query()
});

onMounted(() => {
  loadStat();
});
</script>

<template>
  <Page auto-content-height>
    <DetailDrawer ref="detailDrawerRef" :log="currentLog" />

    <ElRow :gutter="12" class="mb-3">
      <ElCol :span="4">
        <ElCard shadow="never">
          <ElStatistic title="总尝试" :value="stat?.total || 0" />
        </ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="never">
          <ElStatistic title="成功" :value="stat?.success || 0" />
        </ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="never">
          <ElStatistic title="失败" :value="stat?.failed || 0" />
        </ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="never">
          <ElStatistic title="跳过/冷却" :value="(stat?.skipped || 0) + (stat?.cooldown || 0)" />
        </ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="never">
          <ElStatistic title="成功率 %" :value="successRate" />
        </ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="never">
          <ElStatistic
            title="平均耗时 (ms)"
            :value="stat?.avg_duration_ms || 0"
          />
        </ElCard>
      </ElCol>
    </ElRow>

    <Grid />
  </Page>
</template>
