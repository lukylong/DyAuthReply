<script lang="ts" setup>
import type { KuaishouEvent } from '#/api/core/kuaishou';

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
  getKuaishouEventList,
  markReadKuaishouEvent,
} from '#/api/core/kuaishou';

defineOptions({ name: 'KuaishouEvent' });

const rows = ref<KuaishouEvent[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 20, total: 0 });
const search = reactive({
  level: undefined as string | undefined,
  is_read: undefined as boolean | undefined,
});
const selectedIds = ref<string[]>([]);

async function load() {
  loading.value = true;
  try {
    const res = await getKuaishouEventList({
      page: page.page,
      page_size: page.page_size,
      level: search.level,
      is_read: search.is_read,
    });
    rows.value = res.items || [];
    page.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

function levelType(level: string) {
  return (
    { info: 'info', warning: 'warning', error: 'danger', critical: 'danger' }[
      level
    ] || 'info'
  );
}

async function onMarkRead() {
  if (selectedIds.value.length === 0) return;
  await markReadKuaishouEvent(selectedIds.value);
  ElMessage.success('已标记已读');
  selectedIds.value = [];
  load();
}

onMounted(load);
</script>

<template>
  <Page title="运行事件" description="登录、掉线、风控告警、发送失败等运行时事件流">
    <ElSpace wrap class="toolbar">
      <ElSelect v-model="search.level" placeholder="级别" clearable style="width: 120px">
        <ElOption label="信息" value="info" />
        <ElOption label="警告" value="warning" />
        <ElOption label="错误" value="error" />
        <ElOption label="严重" value="critical" />
      </ElSelect>
      <ElSelect
        v-model="search.is_read"
        placeholder="阅读状态"
        clearable
        style="width: 120px"
      >
        <ElOption label="未读" :value="false" />
        <ElOption label="已读" :value="true" />
      </ElSelect>
      <ElButton type="primary" @click="load">查询</ElButton>
      <ElButton @click="onMarkRead" :disabled="selectedIds.length === 0">
        标记已读
      </ElButton>
    </ElSpace>

    <ElTable
      :data="rows"
      v-loading="loading"
      stripe
      @selection-change="(v: KuaishouEvent[]) => (selectedIds = v.map((r) => r.id))"
    >
      <ElTableColumn type="selection" width="40" />
      <ElTableColumn label="级别" width="80">
        <template #default="{ row }">
          <ElTag :type="levelType(row.level) as any" size="small">
            {{ row.level_display || row.level }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="event_type_display" label="类型" width="140" />
      <ElTableColumn prop="title" label="标题" min-width="200" />
      <ElTableColumn prop="worker_id" label="Worker" width="140" show-overflow-tooltip />
      <ElTableColumn prop="occurred_at" label="时间" width="170" />
      <ElTableColumn label="状态" width="70">
        <template #default="{ row }">
          <ElTag v-if="row.is_read" type="info" size="small">已读</ElTag>
          <ElTag v-else type="danger" size="small">未读</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="detail" label="详情" show-overflow-tooltip />
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
