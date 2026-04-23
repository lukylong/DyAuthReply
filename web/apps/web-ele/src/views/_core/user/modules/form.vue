<script lang="ts" setup>
import type { User } from '#/api/core';

import { computed, ref } from 'vue';

import { ZqDrawer } from '#/components/zq-drawer';
import { $t } from '@vben/locales';

import { useVbenForm } from '#/adapter/form';
import { createUserApi, updateUserApi } from '#/api/core';

import { getFormSchema } from '../data';

const emit = defineEmits<{
  success: [];
}>();

const formData = ref<User>();
const visible = ref(false);
const confirmLoading = ref(false);

const [Form, formApi] = useVbenForm({
  commonConfig: {
    colon: true,
    componentProps: {
      class: 'w-full',
    },
  },
  schema: getFormSchema(),
  showDefaultActions: false,
  wrapperClass: 'grid-cols-1 gap-x-4',
});

function open(data?: User) {
  visible.value = true;
  if (data) {
    formData.value = data;
    formApi.setValues(formData.value);
  } else {
    formData.value = undefined;
    formApi.resetForm();
  }
}

defineExpose({
  open,
});

const getDrawerTitle = computed(() =>
  formData.value?.id
    ? $t('ui.actionTitle.edit', [$t('user.name')])
    : $t('ui.actionTitle.create', [$t('user.name')]),
);

async function onSubmit() {
  const { valid } = await formApi.validate();
  if (valid) {
    confirmLoading.value = true;
    const data = await formApi.getValues<Omit<User, 'id'>>();
    try {
      await (formData.value?.id
        ? updateUserApi(formData.value.id, data)
        : createUserApi(data));
      visible.value = false;
      emit('success');
    } finally {
      confirmLoading.value = false;
    }
  }
}
</script>

<template>
  <ZqDrawer
    v-model="visible"
    :title="getDrawerTitle"
    :confirm-loading="confirmLoading"
    size="700px"
    @confirm="onSubmit"
  >
    <Form class="mx-4" />
  </ZqDrawer>
</template>
