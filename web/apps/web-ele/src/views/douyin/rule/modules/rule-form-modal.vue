<script lang="ts" setup>
import type { DouyinRule, DouyinRuleCreateInput } from '#/api/core/douyin';

import { computed, ref } from 'vue';

import { useVbenForm } from '@vben/common-ui';

import { ElButton, ElMessage } from 'element-plus';

import { createDouyinRuleApi, updateDouyinRuleApi } from '#/api/core/douyin';
import { ZqDialog } from '#/components/zq-dialog';

import { useRuleFormSchema } from '../data';

const emit = defineEmits<{ success: [] }>();

const formData = ref<DouyinRule>();
const visible = ref(false);
const confirmLoading = ref(false);

const getTitle = computed(() =>
  formData.value?.id ? '编辑规则' : '新增规则',
);

const [Form, formApi] = useVbenForm({
  layout: 'vertical',
  schema: useRuleFormSchema() as any,
  showDefaultActions: false,
});

function splitLines(text?: string): string[] {
  if (!text) return [];
  return text
    .split(/\n|,|，/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function joinLines(arr?: string[]): string {
  return (arr || []).join('\n');
}

function resetForm() {
  formApi.resetForm();
  if (formData.value) {
    formApi.setValues(toFormValues(formData.value));
  }
}

function toFormValues(row: DouyinRule) {
  return {
    ...row,
    keywords_text: joinLines(row.keywords),
    links_text: joinLines(row.links),
  };
}

function buildPayload(values: Record<string, any>): DouyinRuleCreateInput {
  const payload: DouyinRuleCreateInput = {
    account_id: values.account_id,
    name: values.name,
    match_type: values.match_type,
    keywords: splitLines(values.keywords_text),
    regex_pattern: values.regex_pattern || null,
    reply_text: values.reply_text || '',
    links: splitLines(values.links_text),
    send_mode: values.send_mode,
    priority: values.priority ?? 0,
    status: values.status !== false,
    cooldown_seconds: values.cooldown_seconds ?? 0,
    remark: values.remark || null,
  };
  return payload;
}

async function onSubmit() {
  const { valid } = await formApi.validate();
  if (!valid) return;
  const values = await formApi.getValues();
  const payload = buildPayload(values);

  if (payload.match_type === 'contains' && (payload.keywords || []).length === 0) {
    ElMessage.warning('包含匹配至少需要一个关键词');
    return;
  }
  if (payload.match_type === 'regex' && !payload.regex_pattern) {
    ElMessage.warning('正则匹配必须填写正则表达式');
    return;
  }

  confirmLoading.value = true;
  try {
    if (formData.value?.id) {
      await updateDouyinRuleApi(formData.value.id, payload);
      ElMessage.success('已更新');
    } else {
      await createDouyinRuleApi(payload);
      ElMessage.success('已创建');
    }
    visible.value = false;
    emit('success');
  } finally {
    confirmLoading.value = false;
  }
}

function open(data?: DouyinRule) {
  visible.value = true;
  if (data) {
    formData.value = data;
    formApi.setValues(toFormValues(data));
  } else {
    formData.value = undefined;
    formApi.resetForm();
  }
}

defineExpose({ open });
</script>

<template>
  <ZqDialog
    v-model="visible"
    :title="getTitle"
    :confirm-loading="confirmLoading"
    width="720px"
    @confirm="onSubmit"
  >
    <Form class="mx-4" />
    <template #footer-left>
      <ElButton @click="resetForm">重置</ElButton>
    </template>
  </ZqDialog>
</template>
