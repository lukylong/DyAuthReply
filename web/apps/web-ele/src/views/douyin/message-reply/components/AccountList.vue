<script lang="ts" setup>
import type { DouyinAccount } from '#/api/core/douyin';

import { computed, onMounted, ref } from 'vue';

import { ElAvatar, ElBadge, ElEmpty, ElTag } from 'element-plus';

import { getSimpleDouyinAccountListApi } from '#/api/core/douyin/account';

defineOptions({ name: 'AccountList' });

const props = defineProps<{
  activeAccountId?: string;
}>();

const emit = defineEmits<{
  selectAccount: [accountId: string];
}>();

const accounts = ref<DouyinAccount[]>([]);
const loading = ref(false);

const activeAccounts = computed(() =>
  accounts.value.filter((acc) => acc.status === 1),
);

async function loadAccounts() {
  loading.value = true;
  try {
    accounts.value = await getSimpleDouyinAccountListApi();
  } finally {
    loading.value = false;
  }
}

function onSelectAccount(account: DouyinAccount) {
  emit('selectAccount', account.id);
}

// 为账号生成头像颜色
function getAvatarColor(nickname: string): string {
  const colors = [
    '#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1',
    '#13c2c2', '#eb2f96', '#fa8c16', '#2f54eb', '#a0d911',
  ];
  const hash = nickname.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return colors[hash % colors.length];
}

onMounted(() => {
  loadAccounts();
});
</script>

<template>
  <div class="account-list-panel">
    <!-- 列表头部 -->
    <div class="list-header">
      <span class="header-title">账号列表</span>
      <ElTag v-if="activeAccounts.length > 0" size="small" type="success">
        {{ activeAccounts.length }}
      </ElTag>
    </div>

    <!-- 账号列表 -->
    <div v-loading="loading" class="list-body">
      <ElEmpty
        v-if="!activeAccounts.length"
        description="暂无在线账号"
        :image-size="100"
      />

      <div
        v-for="account in activeAccounts"
        :key="account.id"
        class="account-item"
        :class="{ active: account.id === activeAccountId }"
        @click="onSelectAccount(account)"
      >
        <ElBadge :is-dot="account.status === 1" type="success">
          <ElAvatar
            :src="account.avatar || undefined"
            :size="44"
            :style="{
              backgroundColor: getAvatarColor(account.nickname || 'default'),
              color: '#fff'
            }"
          >
            {{ account.nickname?.charAt(0) || '?' }}
          </ElAvatar>
        </ElBadge>

        <div class="account-info">
          <div class="account-nickname">{{ account.nickname }}</div>
          <ElTag
            v-if="account.status === 1"
            type="success"
            size="small"
            effect="plain"
          >
            在线
          </ElTag>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.account-list-panel {
  width: 280px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}

.list-header {
  padding: 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-title {
  font-size: 16px;
  font-weight: 500;
  color: #262626;
}

.list-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

.account-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  margin-bottom: 8px;
  border-radius: 8px;
  background: #fafafa;
  cursor: pointer;
  transition: all 0.2s;
  border: 2px solid transparent;

  &:hover {
    background: #f0f0f0;
    transform: translateX(2px);
  }

  &.active {
    background: #e6f4ff;
    border-color: #91caff;
  }
}

.account-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.account-nickname {
  font-weight: 500;
  font-size: 14px;
  color: #262626;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
