<script lang="ts" setup>
import type {
  DouyinConversationItem,
  DouyinMessageItem,
} from '#/api/core/douyin';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';

import { ElMessage } from 'element-plus';

import {
  getAccountConversations,
  getAccountMessages,
  getWorkerCommandStatus,
  sendAccountManualReply,
  refreshConversationUserApi,
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
const convKeyword = ref('');
const convPage = ref(1);
const convTotal = ref(0);
const convHasMore = ref(false);
const loadingMoreConversations = ref(false);
const CONV_PAGE_SIZE = 50;

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
  convKeyword.value = '';
  convPage.value = 1;
  convTotal.value = 0;
  convHasMore.value = false;

  await loadConversations();
}

/**
 * 加载会话列表（支持分页、搜索、静默刷新）
 */
async function loadConversations(options?: { silent?: boolean; append?: boolean }) {
  if (!activeAccountId.value) return;

  const silent = options?.silent ?? false;
  const append = options?.append ?? false;

  if (append) {
    if (!convHasMore.value || loadingMoreConversations.value || conversationsLoading.value) return;
    loadingMoreConversations.value = true;
    try {
      const nextPage = convPage.value + 1;
      const res = await getAccountConversations(activeAccountId.value, {
        page: nextPage,
        page_size: CONV_PAGE_SIZE,
        keyword: convKeyword.value.trim() || undefined,
      });
      const existing = new Set(conversations.value.map((c) => c.id));
      for (const item of res.items) {
        if (!existing.has(item.id)) conversations.value.push(item);
      }
      convPage.value = nextPage;
      convTotal.value = res.total;
      convHasMore.value = res.has_more;
    } catch (error: any) {
      if (!silent) ElMessage.error(error.message || '加载更多会话失败');
    } finally {
      loadingMoreConversations.value = false;
    }
    return;
  }

  if (!silent) conversationsLoading.value = true;
  try {
    const refreshSize =
      silent && conversations.value.length > CONV_PAGE_SIZE
        ? conversations.value.length
        : CONV_PAGE_SIZE;
    const res = await getAccountConversations(activeAccountId.value, {
      page: 1,
      page_size: refreshSize,
      keyword: convKeyword.value.trim() || undefined,
    });
    conversations.value = res.items;
    convTotal.value = res.total;
    convHasMore.value = res.has_more;
    if (!silent) {
      convPage.value = Math.max(1, Math.ceil(res.items.length / CONV_PAGE_SIZE));
    }
  } catch (error: any) {
    if (!silent) ElMessage.error(error.message || '加载会话列表失败');
    if (!silent) conversations.value = [];
  } finally {
    if (!silent) conversationsLoading.value = false;
  }
}

function onConversationSearch() {
  convPage.value = 1;
  void loadConversations();
}

function onLoadMoreConversations() {
  void loadConversations({ silent: true, append: true });
}

/**
 * 刷新/补齐指定会话的对方昵称头像
 */
async function refreshUser(force = false) {
  if (!activeAccountId.value || !activeConversationId.value) return;
  try {
    const res = await refreshConversationUserApi(
      activeAccountId.value,
      activeConversationId.value,
    );
    if (res.success) {
      // 成功更新了用户资料，重新加载会话列表以更新左栏昵称和头像
      await loadConversations();
    } else if (force) {
      ElMessage.warning(res.message || '刷新用户资料失败');
    }
  } catch (error: any) {
    if (force) {
      ElMessage.error(error.message || '刷新用户资料失败');
    }
  }
}

/**
 * 手动点击刷新按钮触发的事件（同时刷新最新消息与最新用户资料）
 */
async function onRefresh() {
  await Promise.all([
    loadMessages(),
    refreshUser(true),
  ]);
}

/**
 * 选择会话
 */
async function onConversationSelect(conversationId: string) {
  if (activeConversationId.value === conversationId) return;

  activeConversationId.value = conversationId;

  // 加载该会话的消息列表
  await loadMessages();

  // 如果没有对方昵称，或者昵称包含 "用户_" 这种 fallback (说明需要补齐资料)，在后台静默刷新
  const conv = activeConversation.value;
  if (
    conv &&
    (!conv.peer_nickname ||
      conv.peer_nickname.startsWith('用户_') ||
      conv.peer_sec_uid.startsWith('fallback_'))
  ) {
    refreshUser(false);
  }
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
 * 发送后多次刷新消息/会话列表（Worker 异步处理 + 落库有延迟）
 */
async function refreshAfterSend(maxAttempts = 5, intervalMs = 1200) {
  for (let i = 0; i < maxAttempts; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    await Promise.all([loadMessages(), loadConversations()]);
  }
}

/**
 * 轮询 Worker 命令结果（客户端 db 模式有 command_id；Docker redis 模式跳过）
 */
async function waitManualReplyResult(commandId?: string | null) {
  if (!commandId) {
    await refreshAfterSend();
    return { ok: true, message: '发送指令已下发' };
  }
  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const st = await getWorkerCommandStatus(commandId);
    if (st.status === 'success') {
      await Promise.all([loadMessages(), loadConversations()]);
      return { ok: true, message: '发送成功' };
    }
    if (st.status === 'failed') {
      return { ok: false, message: st.error || '发送失败' };
    }
  }
  await refreshAfterSend(3, 1000);
  return { ok: true, message: '发送指令已下发（结果确认超时，已刷新列表）' };
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

    if (!res.success) {
      ElMessage.error(res.message || '发送失败');
      return;
    }

    const outcome = await waitManualReplyResult(res.command_id);
    if (outcome.ok) {
      ElMessage.success(outcome.message || res.message || '发送成功');
    } else {
      ElMessage.error(outcome.message || '发送失败');
    }
  } catch (error: any) {
    ElMessage.error(error.message || '发送失败');
  }
}

let replyTimer: null | ReturnType<typeof setInterval> = null;

async function pollReplyData() {
  if (!activeAccountId.value) return;
  try {
    await loadConversations({ silent: true });
    if (activeConversationId.value) {
      messages.value = await getAccountMessages(
        activeAccountId.value,
        activeConversationId.value,
      );
    }
  } catch (error) {
    console.error('静默刷新会话或消息失败:', error);
  }
}

watch(activeAccountId, (newId) => {
  if (replyTimer) {
    clearInterval(replyTimer);
    replyTimer = null;
  }
  if (newId) {
    replyTimer = setInterval(pollReplyData, 3000);
  }
});

onUnmounted(() => {
  if (replyTimer) {
    clearInterval(replyTimer);
    replyTimer = null;
  }
});
</script>

<template>
  <Page>
    <div class="message-reply-layout">
      <AccountList
        :active-account-id="activeAccountId"
        @select-account="onAccountSelect"
      />

      <ConversationList
        v-model:search-keyword="convKeyword"
        :account-id="activeAccountId"
        :conversations="conversations"
        :active-conversation-id="activeConversationId"
        :loading="conversationsLoading"
        :loading-more="loadingMoreConversations"
        :has-more="convHasMore"
        :total="convTotal"
        @select-conversation="onConversationSelect"
        @search="onConversationSearch"
        @load-more="onLoadMoreConversations"
      />

      <ChatPanel
        :account-id="activeAccountId"
        :conversation-id="activeConversationId"
        :conversation="activeConversation"
        :messages="messages"
        :loading="messagesLoading"
        @refresh="onRefresh"
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
