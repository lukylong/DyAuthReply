<script lang="ts" setup>
import type {
  DouyinConversationItem,
  DouyinMessageItem,
} from '#/api/core/douyin';

import { computed, nextTick, ref, watch } from 'vue';

import {
  ElAvatar,
  ElButton,
  ElCard,
  ElEmpty,
  ElInput,
  ElMessage,
} from 'element-plus';

defineOptions({ name: 'ChatPanel' });

const props = defineProps<{
  accountId?: string;
  conversationId?: string;
  conversation?: DouyinConversationItem | null;
  messages: DouyinMessageItem[];
  loading?: boolean;
}>();

const emit = defineEmits<{
  refresh: [];
  sendMessage: [text: string];
}>();

const messageInput = ref('');
const sending = ref(false);
const messagesContainer = ref<HTMLElement>();

// 生成用户显示名称
const displayName = computed(() => {
  if (!props.conversation) return '未选择会话';
  if (props.conversation.peer_nickname) {
    return props.conversation.peer_nickname;
  }
  // 如果没有昵称，使用 ID 后6位
  const uid = props.conversation.peer_sec_uid;
  return `用户_${uid.slice(-6)}`;
});

// 为用户生成头像颜色
function getAvatarColor(uid: string): string {
  const colors = [
    '#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1',
    '#13c2c2', '#eb2f96', '#fa8c16', '#2f54eb', '#a0d911',
  ];
  const hash = uid.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return colors[hash % colors.length];
}

// 生成消息发送者的显示信息
function getSenderInfo(msg: DouyinMessageItem) {
  const name = msg.sender_name || '未知用户';
  const displayName = name.length > 20 ? `用户_${name.slice(-6)}` : name;

  return {
    name: displayName,
    initial: displayName.charAt(0),
    avatar: msg.sender_avatar,
  };
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
}

async function onSendMessage() {
  const text = messageInput.value.trim();
  if (!text) {
    ElMessage.warning('请输入消息内容');
    return;
  }

  if (!props.conversationId) {
    ElMessage.warning('请先选择一个会话');
    return;
  }

  sending.value = true;
  try {
    emit('sendMessage', text);
    messageInput.value = '';
    scrollToBottom();
  } finally {
    sending.value = false;
  }
}

function handleKeydown(event: KeyboardEvent) {
  // Enter 发送，Shift+Enter 换行
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    onSendMessage();
  }
}

function formatTime(dateStr?: null | string): string {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (seconds < 60) return '刚刚';
  if (minutes < 60) return `${minutes}分钟前`;
  if (hours < 24) return `${hours}小时前`;

  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

watch(
  () => props.messages.length,
  () => {
    scrollToBottom();
  },
  { immediate: true },
);
</script>

<template>
  <div class="chat-panel">
    <!-- 聊天头部 -->
    <div class="chat-header">
      <div class="chat-header-info">
        <ElAvatar
          v-if="conversation"
          :src="conversation.peer_avatar || undefined"
          :size="40"
          :style="{
            backgroundColor: getAvatarColor(conversation.peer_sec_uid),
            color: '#fff'
          }"
        >
          {{ displayName.charAt(0) }}
        </ElAvatar>
        <div class="chat-header-text">
          <div class="chat-title">{{ displayName }}</div>
          <div v-if="messages.length > 0" class="chat-subtitle">
            {{ messages.length }} 条消息
          </div>
        </div>
      </div>
      <ElButton
        size="small"
        :disabled="!conversationId"
        @click="emit('refresh')"
      >
        刷新
      </ElButton>
    </div>

    <!-- 消息区域 -->
    <div class="chat-body">
      <div
        v-if="!accountId || !conversationId"
        class="empty-state"
      >
        <ElEmpty
          description="选择账号和会话开始聊天"
          :image-size="120"
        />
      </div>

      <div
        v-else
        ref="messagesContainer"
        v-loading="loading"
        class="messages-container"
      >
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message-item"
          :class="{ 'is-mine': msg.direction === 'out' }"
        >
          <ElAvatar
            :src="getSenderInfo(msg).avatar || undefined"
            :size="36"
            class="message-avatar"
            :style="{
              backgroundColor: getAvatarColor(msg.sender_name || 'default'),
              color: '#fff'
            }"
          >
            {{ getSenderInfo(msg).initial }}
          </ElAvatar>

          <div class="message-content">
            <div class="message-meta">
              <span class="sender-name">{{ getSenderInfo(msg).name }}</span>
              <span class="message-time">{{ formatTime(msg.received_at) }}</span>
            </div>
            <div class="message-bubble">
              {{ msg.content }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="chat-footer">
      <ElInput
        v-model="messageInput"
        type="textarea"
        :rows="3"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        :disabled="!conversationId"
        class="message-input"
        @keydown="handleKeydown"
      />
      <div class="footer-actions">
        <div class="footer-hint">
          <span class="hint-text">Enter 发送</span>
          <span class="hint-divider">|</span>
          <span class="hint-text">Shift + Enter 换行</span>
        </div>
        <ElButton
          type="primary"
          :loading="sending"
          :disabled="!messageInput.trim() || !conversationId"
          @click="onSendMessage"
        >
          发送
        </ElButton>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.chat-header-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.chat-header-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.chat-title {
  font-size: 16px;
  font-weight: 500;
  color: #262626;
}

.chat-subtitle {
  font-size: 12px;
  color: #8c8c8c;
}

.chat-body {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message-item {
  display: flex;
  gap: 12px;
  animation: slideIn 0.2s ease-out;

  &.is-mine {
    flex-direction: row-reverse;

    .message-content {
      align-items: flex-end;
    }

    .message-meta {
      flex-direction: row-reverse;
    }

    .message-bubble {
      background: #1890ff;
      color: #fff;
      border-radius: 12px 2px 12px 12px;
    }
  }
}

.message-avatar {
  flex-shrink: 0;
}

.message-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 60%;
}

.message-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sender-name {
  font-size: 13px;
  color: #595959;
  font-weight: 500;
}

.message-time {
  font-size: 12px;
  color: #bfbfbf;
}

.message-bubble {
  padding: 10px 14px;
  background: #f5f5f5;
  border-radius: 2px 12px 12px 12px;
  font-size: 14px;
  line-height: 1.6;
  color: #262626;
  word-break: break-word;
}

.chat-footer {
  border-top: 1px solid #f0f0f0;
  background: #fafafa;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message-input {
  :deep(.el-textarea__inner) {
    border-radius: 8px;
    resize: none;
  }
}

.footer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.footer-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #8c8c8c;
}

.hint-divider {
  color: #d9d9d9;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
