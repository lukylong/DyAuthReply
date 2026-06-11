<script lang="ts" setup>
import type {
  KuaishouReplyLog,
  KuaishouReplyLogStat,
} from '#/api/core/kuaishou';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElCol,
  ElOption,
  ElPagination,
  ElRow,
  ElSelect,
  ElSpace,
  ElStatistic,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  getKuaishouReplyLogList,
  getKuaishouReplyLogStat,
} from '#/api/core/kuaishou';

defineOptions({ name: 'KuaishouReplyLog' });

const RESULT_META: Record<string, { text: string; type: string }> = {
  success: { text: '成功', type: 'success' },
  failed: { text: '失败', type: 'danger' },
  skipped: { text: '跳过', type: 'info' },
  cooldown: { text: '冷却', type: 'warning' },
  quota_exceeded: { text: '超限', type: 'warning' },
  silent: { text: '静默', type: 'info' },
};

const rows = ref<KuaishouReplyLog[]>([]);
const stat = ref<KuaishouReplyLogStat>();
const loading = ref(false);
const page = reactive({ page: 1, page_size: 20, total: 0 });
const search = reactive({ result: undefined as string | undefined });

async function load() {
  loading.value = true;
  try {
    const [res, st] = await Promise.all([
      getKuaishouReplyLogList({
        page: page.page,
        page_size: page.page_size,
        result: search.result,
      }),
      getKuaishouReplyLogStat(),
    ]);
    rows.value = res.items || [];
    page.total = res.total || 0;
    stat.value = st;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <Page title="回复日志" description="自动回复执行明细与结果统计">
    <ElRow :gutter="16" class="stat-row">
      <ElCol :span="4">
        <ElCard shadow="hover"><ElStatistic title="总数" :value="stat?.total ?? 0" /></ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="hover"><ElStatistic title="成功" :value="stat?.success ?? 0" /></ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="hover"><ElStatistic title="失败" :value="stat?.failed ?? 0" /></ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="hover"><ElStatistic title="跳过" :value="stat?.skipped ?? 0" /></ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="hover"><ElStatistic title="超限" :value="stat?.quota_exceeded ?? 0" /></ElCard>
      </ElCol>
      <ElCol :span="4">
        <ElCard shadow="hover">
          <ElStatistic title="平均耗时" :value="stat?.avg_duration_ms ?? 0" suffix=" ms" />
        </ElCard>
      </ElCol>
    </ElRow>

    <ElSpace wrap class="toolbar">
      <ElSelect v-model="search.result" placeholder="结果" clearable style="width: 130px">
        <ElOption
          v-for="(meta, key) in RESULT_META"
          :key="key"
          :label="meta.text"
          :value="key"
        />
      </ElSelect>
      <ElButton type="primary" @click="load">查询</ElButton>
    </ElSpace>

    <ElTable :data="rows" v-loading="loading" stripe>
      <ElTableColumn prop="account_nickname" label="账号" width="130" />
      <ElTableColumn prop="peer_nickname" label="对方" width="130" />
      <ElTableColumn prop="trigger_message_content" label="触发消息" min-width="160" show-overflow-tooltip />
      <ElTableColumn prop="rule_name" label="命中规则" width="140" show-overflow-tooltip />
      <ElTableColumn prop="reply_text" label="回复内容" min-width="180" show-overflow-tooltip />
      <ElTableColumn label="结果" width="90">
        <template #default="{ row }">
          <ElTag :type="(RESULT_META[row.result]?.type as any) || 'info'" size="small">
            {{ row.result_display || RESULT_META[row.result]?.text || row.result }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="duration_ms" label="耗时(ms)" width="90" />
      <ElTableColumn prop="sent_at" label="时间" width="170" />
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
.stat-row {
  margin-bottom: 16px;
}

.toolbar {
  margin-bottom: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
