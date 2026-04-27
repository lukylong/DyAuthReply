<script lang="ts" setup>
import { ref } from 'vue';

import { useVbenForm } from '@vben/common-ui';
import { ElMessage } from 'element-plus';

import {
  getSimpleDouyinAccountListApi,
  quickEnableDouyinRuleApi,
} from '#/api/core/douyin';
import { ZqDialog } from '#/components/zq-dialog';

const emit = defineEmits<{ success: [] }>();

const visible = ref(false);
const confirmLoading = ref(false);

const [Form, formApi] = useVbenForm({
  layout: 'vertical',
  showDefaultActions: false,
  schema: [
    {
      component: 'ApiSelect',
      fieldName: 'account_id',
      label: '所属账号',
      rules: 'required',
      componentProps: {
        placeholder: '请选择需要托管的账号',
        api: getSimpleDouyinAccountListApi,
        labelField: 'nickname',
        valueField: 'id',
      },
    },
    {
      component: 'Textarea',
      fieldName: 'reply_text',
      label: '自动回复文案',
      rules: 'required',
      componentProps: {
        rows: 5,
        maxlength: 500,
        showWordLimit: true,
        placeholder:
          '例如：您好，消息已收到～客服会尽快回复您。也可写：你好 {{nickname}}，请问想了解哪款产品？',
      },
    },
    {
      component: 'Textarea',
      fieldName: 'keyword_text',
      label: '触发关键词（可选）',
      componentProps: {
        rows: 3,
        maxlength: 200,
        showWordLimit: true,
        placeholder: '每行一个关键词；填了后会创建关键词规则，便于快速调试',
      },
    },
    {
      component: 'InputNumber',
      fieldName: 'cooldown_seconds',
      label: '同会话冷却时间（秒）',
      defaultValue: 300,
      componentProps: {
        min: 0,
        max: 86400,
      },
    },
  ] as any,
});

function open() {
  visible.value = true;
  formApi.setValues({
    cooldown_seconds: 300,
    keyword_text: '',
  });
}

async function onSubmit() {
  const { valid } = await formApi.validate();
  if (!valid) return;

  const values = await formApi.getValues();
  const keywords = String(values.keyword_text || '')
    .split(/\n|,|，/)
    .map((item) => item.trim())
    .filter(Boolean);
  confirmLoading.value = true;
  try {
    const res = await quickEnableDouyinRuleApi({
      account_id: values.account_id,
      reply_text: values.reply_text,
      keywords,
      cooldown_seconds: values.cooldown_seconds,
      send_mode: 'merged',
    });
    ElMessage.success(res.message || '已开启自动回复');
    visible.value = false;
    emit('success');
  } finally {
    confirmLoading.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <ZqDialog
    v-model="visible"
    title="一键开启陌生人自动回复"
    width="640px"
    :confirm-loading="confirmLoading"
    @confirm="onSubmit"
  >
    <Form class="mx-4" />
  </ZqDialog>
</template>
