<script lang="ts" setup>
import type {
  DouyinConversationItem,
  DouyinMessageItem,
} from '#/api/core/douyin';

import { computed, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { ElMessage } from 'element-plus';

import {
  getAccountConversations,
  getAccountMessages,
  sendAccountManualReply,
} from '#/api/core/douyin/message-reply';

import AccountList from './components/AccountList.vue';
import ChatPanel from './components/ChatPanel.vue';
import ConversationList from './components/ConversationList.vue';

defineOptions({ name: 'DouyinMessageReply' });

// 当前选中的账号和会话
const activeAccountId = ref('');
const activeConversationId = ref('');

// 数据加载状态
const conversationsLoading = ref(false);
const messagesLoading = ref(false);

// 会话列表和消息列表
const conversations = ref<DouyinConversationItem[]>([]);
const messages = ref<DouyinMessageItem[]>([]);

// 当前选中的会话对象
const activeConversation = computed(
  () =>
    conversations.value.find((c) => c.id === activeConversationId.value) ||
    null,
);

/**
 * 选择账号
 */
async function onAccountSelect(accountId: string) {
  if (activeAccountId.value === accountId) return;

  activeAccountId.value = accountId;
  activeConversationId.value = '';
  messages.value = [];

  // 加载该账号的会话列表
  await loadConversations();
}

/**
 * 加载会话列表
 */
async function loadConversations() {
  if (!activeAccountId.value) return;

  conversationsLoading.value = true;
  try {
    conversations.value = await getAccountConversations(activeAccountId.value);
  } catch (error: any) {
    ElMessage.error(error.message || '加载会话列表失败');
    conversations.value = [];
  } finally {
    conversationsLoading.value = false;
  }
}

/**
 * 选择会话
 */
async function onConversationSelect(conversationId: string) {
  if (activeConversationId.value === conversationId) return;

  activeConversationId.value = conversationId;

  // 加载该会话的消息列表
  await loadMessages();
}

/**
 * 加载消息列表
 */
async function loadMessages() {
  if (!activeAccountId.value || !activeConversationId.value) return;

  messagesLoading.value = true;
  try {
    messages.value = await getAccountMessages(
      activeAccountId.value,
      activeConversationId.value,
    );
  } catch (error: any) {
    ElMessage.error(error.message || '加载消息列表失败');
    messages.value = [];
  } finally {
    messagesLoading.value = false;
  }
}

/**
 * 发送消息
 */
async function onSendMessage(text: string) {
  if (!activeAccountId.value || !activeConversationId.value) {
    ElMessage.warning('请先选择账号和会话');
    return;
  }

  try {
    const res = await sendAccountManualReply(
      activeAccountId.value,
      activeConversationId.value,
      text,
    );

    if (res.success) {
      ElMessage.success(res.message || '发送成功');
      // 延迟刷新消息列表，等待 worker 处理
      setTimeout(() => {
        loadMessages();
      }, 1000);
    } else {
      ElMessage.error(res.message || '发送失败');
    }
  } catch (error: any) {
    ElMessage.error(error.message || '发送失败');
  }
}
</script>

<template>
  <Page
    title="消息回复"
    description="选择账号和会话，与用户进行实时消息互动"
  >
    <div class="message-reply-layout">
      <AccountList
        :active-account-id="activeAccountId"
        @select-account="onAccountSelect"
      />

      <ConversationList
        :account-id="activeAccountId"
        :conversations="conversations"
        :active-conversation-id="activeConversationId"
        :loading="conversationsLoading"
        @select-conversation="onConversationSelect"
      />

      <ChatPanel
        :account-id="activeAccountId"
        :conversation-id="activeConversationId"
        :conversation="activeConversation"
        :messages="messages"
        :loading="messagesLoading"
        @refresh="loadMessages"
        @send-message="onSendMessage"
      />
    </div>
  </Page>
</template>

<style scoped lang="scss">
.message-reply-layout {
  display: flex;
  gap: 12px;
  height: calc(100vh - 180px);
  min-height: 600px;

  // 账号列表：固定宽度
  > :nth-child(1) {
    flex-shrink: 0;
  }

  // 会话列表：固定宽度
  > :nth-child(2) {
    flex-shrink: 0;
    width: 340px;
  }

  // 聊天面板：占据剩余空间
  > :nth-child(3) {
    flex: 1;
    min-width: 0;
  }
}

@media (max-width: 1200px) {
  .message-reply-layout {
    height: calc(100vh - 160px);
  }
}
</style>
