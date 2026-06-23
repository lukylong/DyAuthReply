<script lang="ts" setup>
import type { DouyinConversationItem } from '#/api/core/douyin';

import { computed, ref, watch } from 'vue';

import {
  ElAvatar,
  ElBadge,
  ElEmpty,
  ElInput,
  ElTag,
} from 'element-plus';
import { Search } from '@element-plus/icons-vue';

defineOptions({ name: 'ConversationList' });

const props = defineProps<{
  accountId?: string;
  conversations: DouyinConversationItem[];
  activeConversationId?: string;
  loading?: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  total?: number;
}>();

const emit = defineEmits<{
  selectConversation: [conversationId: string];
  search: [];
  loadMore: [];
}>();

const searchKeyword = defineModel<string>('searchKeyword', { default: '' });
const listBodyRef = ref<HTMLElement | null>(null);
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const countLabel = computed(() => {
  const loaded = props.conversations.length;
  const total = props.total ?? loaded;
  if (total <= 0) return '';
  if (loaded >= total) return String(total);
  return `${loaded}/${total}`;
});

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => emit('search'), 300);
}

function onListScroll() {
  const el = listBodyRef.value;
  if (!el || !props.hasMore || props.loadingMore) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 80;
  if (nearBottom) emit('loadMore');
}

// 生成显示名称
function getDisplayName(conv: DouyinConversationItem): string {
  if (conv.peer_nickname) {
    return conv.peer_nickname;
  }
  // 使用 ID 后6位
  return `用户_${conv.peer_sec_uid.slice(-6)}`;
}

// 为用户生成头像颜色
function getAvatarColor(uid: string): string {
  const colors = [
    '#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1',
    '#13c2c2', '#eb2f96', '#fa8c16', '#2f54eb', '#a0d911',
  ];
  const hash = uid.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return colors[hash % colors.length] || '#1890ff';
}

function onSelectConversation(conv: DouyinConversationItem) {
  emit('selectConversation', conv.id);
}

function formatTime(dateStr?: null | string): string {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (seconds < 60) return '刚刚';
  if (minutes < 60) return `${minutes}分钟前`;
  if (hours < 24) return `${hours}小时前`;
  if (days === 1) return '昨天';
  if (days < 7) return `${days}天前`;

  // 修复：跨年消息显示年份
  const isSameYear = date.getFullYear() === now.getFullYear();

  if (isSameYear) {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  } else {
    return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
  }
}

// 截断消息预览
function truncateMessage(text: string, maxLength = 20): string {
  if (!text) return '';
  return text.length > maxLength ? text.slice(0, maxLength) + '...' : text;
}

watch(
  () => props.accountId,
  () => {
    searchKeyword.value = '';
  },
);
</script>

<template>
  <div class="conversation-list">
    <!-- 列表头部 -->
    <div class="list-header">
      <div class="header-title">
        <span>消息列表</span>
        <ElTag v-if="countLabel" size="small" type="info">
          {{ countLabel }}
        </ElTag>
      </div>

      <!-- 搜索框（服务端搜索） -->
      <ElInput
        v-model="searchKeyword"
        placeholder="搜索昵称 / 抖音号"
        :prefix-icon="Search"
        clearable
        size="small"
        class="search-input"
        @input="onSearchInput"
      />
    </div>

    <!-- 会话列表 -->
    <div
      ref="listBodyRef"
      v-loading="loading"
      class="list-body"
      @scroll="onListScroll"
    >
      <div v-if="!accountId" class="empty-state">
        <ElEmpty
          description="请先选择账号"
          :image-size="100"
        />
      </div>

      <div v-else-if="conversations.length === 0 && !loading" class="empty-state">
        <ElEmpty
          :description="searchKeyword ? '未找到匹配的会话' : '暂无会话'"
          :image-size="100"
        />
      </div>

      <template v-else>
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conversation-item"
          :class="{ active: activeConversationId === conv.id }"
          @click="onSelectConversation(conv)"
        >
        <ElBadge
          v-if="conv.unread_count && conv.unread_count > 0"
          :value="conv.unread_count"
          :max="99"
          class="avatar-badge"
        >
          <ElAvatar
            :src="conv.peer_avatar || undefined"
            :size="48"
            :style="{
              backgroundColor: getAvatarColor(conv.peer_sec_uid),
              color: '#fff'
            }"
          >
            {{ getDisplayName(conv).charAt(0) }}
          </ElAvatar>
        </ElBadge>
        <ElAvatar
          v-else
          :src="conv.peer_avatar || undefined"
          :size="48"
          :style="{
            backgroundColor: getAvatarColor(conv.peer_sec_uid),
            color: '#fff'
          }"
        >
          {{ getDisplayName(conv).charAt(0) }}
        </ElAvatar>

        <div class="conversation-info">
          <div class="info-row">
            <span class="conversation-name">{{ getDisplayName(conv) }}</span>
            <span class="conversation-time">{{ formatTime(conv.last_message_at) }}</span>
          </div>
          <div class="info-row">
            <span class="last-message">
              {{ truncateMessage(conv.last_message_preview || '暂无消息') }}
            </span>
          </div>
        </div>
        </div>
        <div v-if="loadingMore" class="load-more-tip">加载更多…</div>
        <div v-else-if="hasMore" class="load-more-tip muted">向下滚动加载更多</div>
      </template>
    </div>
  </div>
</template>

<style scoped lang="scss">
.conversation-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}

.list-header {
  padding: 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.header-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 16px;
  font-weight: 500;
  color: #262626;
}

.search-input {
  width: 100%;
}

.list-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.load-more-tip {
  text-align: center;
  padding: 10px 8px 14px;
  font-size: 12px;
  color: #8c8c8c;
}

.load-more-tip.muted {
  opacity: 0.85;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 300px;
}

.conversation-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 4px;

  &:hover {
    background: #f5f5f5;
  }

  &.active {
    background: #e6f4ff;
    border: 1px solid #91caff;
  }
}

.avatar-badge {
  flex-shrink: 0;
}

.conversation-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.conversation-name {
  font-size: 14px;
  font-weight: 500;
  color: #262626;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conversation-time {
  font-size: 12px;
  color: #8c8c8c;
  white-space: nowrap;
  flex-shrink: 0;
}

.last-message {
  font-size: 13px;
  color: #8c8c8c;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
