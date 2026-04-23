<script lang="ts" setup>
import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import DictItemList from './modules/dict-item-list.vue';
import DictList from './modules/dict-list.vue';

defineOptions({ name: 'SystemDict' });

const currentDictId = ref<string>();

/**
 * 字典选择事件
 */
function onDictSelect(dictId: string | undefined) {
  currentDictId.value = dictId;
}
</script>

<template>
  <Page auto-content-height>
    <div class="flex h-full">
      <!-- 字典列表 -->
      <div class="w-1/6">
        <DictList @select="onDictSelect" />
      </div>

      <!-- 字典项列表 -->
      <div class="w-5/6">
        <DictItemList :dict-id="currentDictId" />
      </div>
    </div>
  </Page>
</template>
