<script lang="ts" setup>
import type { DouyinReplyLog } from '#/api/core/douyin';

import { computed } from 'vue';

import { useVbenDrawer } from '@vben/common-ui';

import { ElDescriptions, ElDescriptionsItem, ElLink, ElTag } from 'element-plus';

import { getResultOptions } from '../data';

interface Props {
  log?: DouyinReplyLog;
}

const props = defineProps<Props>();

const [Drawer, drawerApi] = useVbenDrawer({
  title: '回复日志详情',
  footer: false,
  loading: false,
});

const resultOption = computed(() => {
  if (!props.log) return null;
  return getResultOptions().find((o) => o.value === props.log?.result) || null;
});

defineExpose({
  open: drawerApi.open,
  close: drawerApi.close,
});
</script>

<template>
  <Drawer>
    <template v-if="log">
      <ElDescriptions :column="1" border size="small">
        <ElDescriptionsItem label="回复时间">
          {{ log.sent_at || log.sys_create_datetime || '-' }}
        </ElDescriptionsItem>
        <ElDescriptionsItem label="账号">
          {{ log.account_nickname || log.account_id }}
        </ElDescriptionsItem>
        <ElDescriptionsItem label="对方">
          {{ log.peer_nickname || '-' }}
        </ElDescriptionsItem>
        <ElDescriptionsItem label="命中规则">
          {{ log.rule_name || '-' }}
        </ElDescriptionsItem>
        <ElDescriptionsItem label="触发消息">
          <div class="max-w-full whitespace-pre-wrap break-all">
            {{ log.trigger_message_content || '-' }}
          </div>
        </ElDescriptionsItem>
        <ElDescriptionsItem label="回复内容">
          <div class="max-w-full whitespace-pre-wrap break-all">
            {{ log.reply_text }}
          </div>
        </ElDescriptionsItem>
        <ElDescriptionsItem
          v-if="log.reply_links && log.reply_links.length > 0"
          label="附带链接"
        >
          <div class="flex flex-col gap-1">
            <ElLink
              v-for="link in log.reply_links"
              :key="link"
              :href="link"
              target="_blank"
              type="primary"
            >
              {{ link }}
            </ElLink>
          </div>
        </ElDescriptionsItem>
        <ElDescriptionsItem label="结果">
          <ElTag :type="(resultOption?.type as any) || 'info'">
            {{ resultOption?.label || log.result_display || log.result }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem label="耗时">
          {{ log.duration_ms }} ms
        </ElDescriptionsItem>
        <ElDescriptionsItem v-if="log.error_message" label="错误信息">
          <div class="whitespace-pre-wrap break-all text-red-500">
            {{ log.error_message }}
          </div>
        </ElDescriptionsItem>
      </ElDescriptions>
    </template>
    <template v-else>
      <div class="py-8 text-center text-gray-500">暂无数据</div>
    </template>
  </Drawer>
</template>
