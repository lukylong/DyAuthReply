<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { RouterLink } from 'vue-router';
import {
  getWorkerCommandStatus,
  listAccounts,
  listConversations,
  listMessages,
  sendManualReply,
  type ConversationItem,
  type DouyinAccount,
  type MessageItem,
} from '../api/client';

const accounts = ref<DouyinAccount[]>([]);
const activeAccountId = ref('');
const conversations = ref<ConversationItem[]>([]);
const activeConversationId = ref('');
const messages = ref<MessageItem[]>([]);
const replyText = ref('');
const messagesEl = ref<HTMLElement | null>(null);
const messagesEndRef = ref<HTMLElement | null>(null);
const convsColEl = ref<HTMLElement | null>(null);
const stickToBottom = ref(true);

const loadingAccounts = ref(true);
const loadingConversations = ref(false);
const loadingMessages = ref(false);
const syncing = ref(false);
const sending = ref(false);
const error = ref('');
const toast = ref('');

const activeAccount = computed(() =>
  accounts.value.find((a) => a.id === activeAccountId.value),
);
const activeConversation = computed(() =>
  conversations.value.find((c) => c.id === activeConversationId.value),
);

let pollTimer: ReturnType<typeof setInterval> | null = null;
let convSig = '';
let msgSig = '';

function displayPeerName(conv?: ConversationItem | null) {
  if (!conv) return '未知用户';
  const nick = conv.peer_nickname?.trim();
  if (nick) return nick;
  const uid = conv.peer_unique_id?.trim();
  if (uid) return uid;
  const sec = conv.peer_sec_uid || '';
  if (sec.length > 12) return `用户 ${sec.slice(0, 8)}…`;
  return sec || '未知用户';
}

function displayPeerSubtitle(conv?: ConversationItem | null) {
  if (!conv) return '';
  const parts: string[] = [];
  const uid = conv.peer_unique_id?.trim();
  if (uid && uid !== conv.peer_nickname?.trim()) parts.push(`@${uid}`);
  const sec = conv.peer_sec_uid?.trim();
  if (sec && !sec.startsWith('fallback_') && sec.length > 16) {
    parts.push(sec.slice(0, 18) + '…');
  }
  return parts.join(' · ');
}

function avatarInitial(name: string) {
  const t = name.trim();
  if (!t) return '?';
  return t.slice(0, 1).toUpperCase();
}

function conversationsSignature(items: ConversationItem[]) {
  return items
    .map(
      (c) =>
        `${c.id}:${c.last_message_at || ''}:${c.unread_count}:${c.peer_nickname || ''}:${c.peer_avatar || ''}`,
    )
    .join('|');
}

function messagesSignature(items: MessageItem[]) {
  return items.map((m) => `${m.id}:${m.received_at || ''}`).join('|');
}

function formatTime(iso?: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function previewText(conv: ConversationItem) {
  return conv.last_message_preview || '暂无消息';
}

function sortConversations(items: ConversationItem[]) {
  return [...items].sort((a, b) => {
    const ta = a.last_message_at ? new Date(a.last_message_at).getTime() : 0;
    const tb = b.last_message_at ? new Date(b.last_message_at).getTime() : 0;
    return tb - ta;
  });
}

function isNearBottom(el: HTMLElement, threshold = 96) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

async function scrollMessagesToBottom() {
  await nextTick();
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
  const el = messagesEl.value;
  if (el) el.scrollTop = el.scrollHeight;
  messagesEndRef.value?.scrollIntoView({ block: 'end' });
}

function onMessagesScroll() {
  const el = messagesEl.value;
  if (!el) return;
  stickToBottom.value = isNearBottom(el);
}

function resetConversationsScroll() {
  const el = convsColEl.value;
  if (el) el.scrollTop = 0;
}

async function loadAccounts() {
  loadingAccounts.value = true;
  error.value = '';
  try {
    accounts.value = await listAccounts();
    if (!activeAccountId.value && accounts.value.length > 0) {
      activeAccountId.value = accounts.value[0].id;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loadingAccounts.value = false;
  }
}

async function loadConversations(silent = false) {
  if (!activeAccountId.value) {
    conversations.value = [];
    return;
  }
  if (!silent) loadingConversations.value = true;
  else syncing.value = true;
  try {
    const items = sortConversations(await listConversations(activeAccountId.value));
    const sig = conversationsSignature(items);
    if (sig !== convSig) {
      convSig = sig;
      conversations.value = items;
      await nextTick();
      resetConversationsScroll();
    }
    if (
      activeConversationId.value &&
      !conversations.value.some((c) => c.id === activeConversationId.value)
    ) {
      activeConversationId.value = '';
      messages.value = [];
      msgSig = '';
    }
    if (!activeConversationId.value && conversations.value.length > 0) {
      activeConversationId.value = conversations.value[0].id;
    }
  } catch (e) {
    if (!silent) toast.value = e instanceof Error ? e.message : String(e);
    if (!silent) conversations.value = [];
  } finally {
    if (!silent) loadingConversations.value = false;
    syncing.value = false;
  }
}

async function loadMessages(silent = false) {
  if (!activeAccountId.value || !activeConversationId.value) {
    messages.value = [];
    return;
  }
  if (!silent) loadingMessages.value = true;
  else syncing.value = true;
  const shouldScroll = !silent || stickToBottom.value;
  try {
    const items = await listMessages(activeAccountId.value, activeConversationId.value);
    const sig = messagesSignature(items);
    if (sig !== msgSig) {
      msgSig = sig;
      messages.value = items;
      if (shouldScroll) {
        stickToBottom.value = true;
        await scrollMessagesToBottom();
      }
    }
  } catch (e) {
    if (!silent) toast.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (!silent) loadingMessages.value = false;
    syncing.value = false;
  }
}

function selectAccount(id: string) {
  if (activeAccountId.value === id) return;
  activeAccountId.value = id;
  activeConversationId.value = '';
  messages.value = [];
  convSig = '';
  msgSig = '';
}

function selectConversation(id: string) {
  if (activeConversationId.value === id) return;
  activeConversationId.value = id;
  msgSig = '';
  stickToBottom.value = true;
}

function pollTick() {
  if (document.hidden) return;
  if (activeAccountId.value) void loadConversations(true);
  if (activeConversationId.value) void loadMessages(true);
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollTick, 10000);
}

function onVisibilityChange() {
  if (!document.hidden) pollTick();
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitManualReplyResult(commandId: string, sentText: string) {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const st = await getWorkerCommandStatus(commandId);
    if (st.status === 'success') {
      return { ok: true as const };
    }
    if (st.status === 'failed') {
      return { ok: false as const, error: st.error || '发送失败' };
    }
    if (st.consumed && st.status === 'unknown') {
      return { ok: false as const, error: '发送结果未知，请刷新消息列表' };
    }
    await sleep(500);
  }
  // 无 command_id 轮询时的兜底：看是否已落库出站消息
  const latest = messages.value.filter((m) => m.direction === 'out').at(-1);
  if (latest?.content?.trim() === sentText.trim()) {
    return { ok: true as const };
  }
  return { ok: false as const, error: '等待 worker 回执超时，请稍后刷新或重试' };
}

async function onSend() {
  const text = replyText.value.trim();
  if (!text || !activeAccountId.value || !activeConversationId.value) return;
  sending.value = true;
  toast.value = '';
  try {
    const res = await sendManualReply(activeAccountId.value, activeConversationId.value, text);
    if (!res.success) {
      toast.value = res.message || '发送失败';
      return;
    }
    replyText.value = '';
    toast.value = '已发送，等待 worker 回执…';
    if (res.command_id) {
      const outcome = await waitManualReplyResult(res.command_id, text);
      if (outcome.ok) {
        toast.value = '发送成功';
        await loadMessages(true);
        await loadConversations();
        window.setTimeout(() => {
          if (toast.value === '发送成功') toast.value = '';
        }, 2500);
        return;
      }
      toast.value = outcome.error || '发送失败';
      return;
    }
    await loadMessages(true);
    await loadConversations();
  } catch (e) {
    toast.value = e instanceof Error ? e.message : String(e);
  } finally {
    sending.value = false;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

watch(activeAccountId, () => {
  convSig = '';
  loadConversations();
});

watch(activeConversationId, () => {
  msgSig = '';
  loadMessages();
});

onMounted(async () => {
  await loadAccounts();
  await loadConversations();
  startPolling();
  document.addEventListener('visibilitychange', onVisibilityChange);
});

onUnmounted(() => {
  stopPolling();
  document.removeEventListener('visibilitychange', onVisibilityChange);
});
</script>

<template>
  <div class="chat-page">
    <aside class="col accounts">
      <div class="col-head">账号</div>
      <div v-if="loadingAccounts" class="col-empty">加载中…</div>
      <div v-else-if="accounts.length === 0" class="col-empty">
        暂无账号，请先在「我的抖音号」导入
      </div>
      <div
        v-for="acc in accounts"
        :key="acc.id"
        class="acc-item"
        :class="{ active: acc.id === activeAccountId }"
        role="button"
        tabindex="0"
        @click="selectAccount(acc.id)"
        @keydown.enter="selectAccount(acc.id)"
      >
        <span class="acc-name">{{ acc.nickname }}</span>
        <span class="acc-sub">今日 {{ acc.reply_today ?? 0 }}/{{ acc.daily_reply_quota ?? 200 }}</span>
      </div>
    </aside>

    <aside ref="convsColEl" class="col convs">
      <div class="col-head">
        会话
        <span v-if="loadingConversations" class="spin">…</span>
        <span v-else-if="syncing" class="sync-dot" title="同步中" />
      </div>
      <div v-if="!activeAccountId" class="col-empty">请选择账号</div>
      <div v-else-if="conversations.length === 0" class="col-empty">
        暂无会话。请确保 worker 在跑，并有人给你发私信。
      </div>
      <button
        v-for="conv in conversations"
        :key="conv.id"
        type="button"
        class="conv-item"
        :class="{ active: conv.id === activeConversationId }"
        @click="selectConversation(conv.id)"
      >
        <div class="conv-row">
          <div class="avatar sm">
            <img v-if="conv.peer_avatar" :src="conv.peer_avatar" alt="" loading="lazy" />
            <span v-else>{{ avatarInitial(displayPeerName(conv)) }}</span>
          </div>
          <div class="conv-body">
            <div class="conv-top">
              <span class="conv-name">{{ displayPeerName(conv) }}</span>
              <span class="conv-time">{{ formatTime(conv.last_message_at) }}</span>
            </div>
            <div class="conv-sub" v-if="displayPeerSubtitle(conv)">{{ displayPeerSubtitle(conv) }}</div>
            <div class="conv-preview">{{ previewText(conv) }}</div>
          </div>
          <span v-if="conv.unread_count > 0" class="badge">{{ conv.unread_count }}</span>
        </div>
      </button>
    </aside>

    <section class="chat-main">
      <div v-if="!activeConversation" class="chat-empty">
        <p>选择左侧会话开始聊天</p>
        <p class="hint">手动回复会通过 worker 经抖音 IM 协议发出</p>
      </div>
      <template v-else>
        <header class="chat-head">
          <div class="avatar md">
            <img
              v-if="activeConversation.peer_avatar"
              :src="activeConversation.peer_avatar"
              alt=""
            />
            <span v-else>{{ avatarInitial(displayPeerName(activeConversation)) }}</span>
          </div>
          <div class="chat-head-text">
            <strong>{{ displayPeerName(activeConversation) }}</strong>
            <span v-if="displayPeerSubtitle(activeConversation)" class="peer-sub">
              {{ displayPeerSubtitle(activeConversation) }}
            </span>
            <span v-if="activeAccount" class="acc-tag">@ {{ activeAccount.nickname }}</span>
          </div>
        </header>

        <div ref="messagesEl" class="messages" @scroll="onMessagesScroll">
          <div v-if="loadingMessages && messages.length === 0" class="chat-loading inline">加载消息…</div>
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="msg-row"
            :class="msg.direction === 'out' ? 'out' : 'in'"
          >
            <div v-if="msg.direction === 'in'" class="avatar xs">
              <img
                v-if="msg.sender_avatar || activeConversation?.peer_avatar"
                :src="msg.sender_avatar || activeConversation?.peer_avatar || ''"
                alt=""
              />
              <span v-else>{{ avatarInitial(displayPeerName(activeConversation)) }}</span>
            </div>
            <div class="bubble">
              <p class="text">{{ msg.content }}</p>
              <time>{{ formatTime(msg.received_at) }}</time>
            </div>
            <div v-if="msg.direction === 'out'" class="avatar xs out-av">
              <img v-if="activeAccount?.avatar" :src="activeAccount.avatar" alt="" />
              <span v-else>{{ avatarInitial(activeAccount?.nickname || '我') }}</span>
            </div>
          </div>
          <p v-if="messages.length === 0 && !loadingMessages" class="no-msg">还没有消息记录</p>
          <div ref="messagesEndRef" class="messages-end" aria-hidden="true" />
        </div>

        <footer class="composer">
          <p v-if="toast" class="toast">{{ toast }}</p>
          <div class="composer-row">
            <textarea
              v-model="replyText"
              rows="2"
              placeholder="输入回复内容，Enter 发送（Shift+Enter 换行）"
              @keydown.enter.exact.prevent="onSend"
            />
            <button type="button" class="send" :disabled="sending || !replyText.trim()" @click="onSend">
              {{ sending ? '发送中' : '发送' }}
            </button>
          </div>
        </footer>
      </template>
    </section>

    <aside v-if="activeAccount" class="col sidebar">
      <div class="col-head">本账号</div>
      <div class="side-body">
        <p class="side-name">{{ activeAccount.nickname }}</p>
        <p class="side-meta">
          今日 {{ activeAccount.reply_today ?? 0 }} / {{ activeAccount.daily_reply_quota ?? 200 }}
        </p>
        <nav class="side-links">
          <RouterLink to="/rules">管理规则 →</RouterLink>
          <RouterLink to="/logs">查看记录 →</RouterLink>
        </nav>
      </div>
    </aside>

    <p v-if="error" class="page-error">{{ error }}</p>
  </div>
</template>

<style scoped>
.chat-page {
  display: grid;
  grid-template-columns: 180px 300px 1fr 180px;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: rgba(15, 23, 42, 0.5);
}

.avatar {
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, rgba(254, 44, 85, 0.35), rgba(255, 107, 53, 0.25));
  color: #fff;
  font-weight: 700;
}

.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar.sm {
  width: 40px;
  height: 40px;
  font-size: 0.85rem;
}

.avatar.md {
  width: 44px;
  height: 44px;
  font-size: 0.95rem;
}

.avatar.xs {
  width: 32px;
  height: 32px;
  font-size: 0.75rem;
  align-self: flex-end;
}

.avatar.out-av {
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.35), rgba(99, 102, 241, 0.25));
}

.col {
  border-right: 1px solid rgba(148, 163, 184, 0.12);
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.col-head {
  padding: 14px 16px;
  font-size: 0.85rem;
  color: #94a3b8;
  border-bottom: 1px solid rgba(148, 163, 184, 0.1);
  flex-shrink: 0;
}

.col-empty {
  padding: 20px 16px;
  color: #64748b;
  font-size: 0.88rem;
  line-height: 1.5;
}

.acc-item,
.conv-item {
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.06);
  display: block;
}

.acc-item:hover,
.conv-item:hover {
  background: rgba(148, 163, 184, 0.08);
}

.acc-item.active,
.conv-item.active {
  background: rgba(254, 44, 85, 0.12);
}

.acc-name {
  display: block;
  font-weight: 600;
  font-size: 0.92rem;
}

.acc-sub {
  display: block;
  font-size: 0.75rem;
  color: #94a3b8;
  margin-top: 4px;
}

.conv-item {
  padding: 10px 12px;
}

.conv-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.conv-body {
  flex: 1;
  min-width: 0;
}

.conv-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.conv-name {
  font-weight: 600;
  font-size: 0.9rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-sub {
  font-size: 0.72rem;
  color: #64748b;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge {
  background: #fe2c55;
  color: #fff;
  font-size: 0.7rem;
  padding: 1px 6px;
  border-radius: 999px;
  flex-shrink: 0;
  align-self: center;
}

.conv-preview {
  font-size: 0.8rem;
  color: #94a3b8;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  font-size: 0.72rem;
  color: #64748b;
  flex-shrink: 0;
}

.chat-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  flex: 1;
}

.chat-empty {
  flex: 1;
  display: grid;
  place-content: center;
  text-align: center;
  color: #94a3b8;
}

.chat-empty .hint {
  font-size: 0.85rem;
  color: #64748b;
}

.chat-head {
  padding: 12px 20px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.chat-head-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.chat-head-text strong {
  font-size: 1rem;
}

.peer-sub {
  font-size: 0.78rem;
  color: #64748b;
}

.acc-tag {
  font-size: 0.78rem;
  color: #94a3b8;
}

.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.msg-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.msg-row.in {
  justify-content: flex-start;
}

.msg-row.out {
  justify-content: flex-end;
}

.bubble {
  max-width: min(72%, 520px);
  padding: 10px 14px;
  border-radius: 14px;
  background: rgba(30, 41, 59, 0.9);
}

.msg-row.out .bubble {
  background: linear-gradient(135deg, rgba(254, 44, 85, 0.85), rgba(255, 107, 53, 0.75));
}

.bubble .text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.92rem;
}

.bubble time {
  display: block;
  margin-top: 6px;
  font-size: 0.7rem;
  opacity: 0.75;
}

.no-msg {
  text-align: center;
  color: #64748b;
  margin: auto;
}

.chat-loading {
  text-align: center;
  color: #64748b;
  padding: 24px;
}

.chat-loading.inline {
  padding: 12px;
}

.messages-end {
  height: 1px;
  flex-shrink: 0;
}

.composer {
  border-top: 1px solid rgba(148, 163, 184, 0.12);
  padding: 12px 16px 16px;
  flex-shrink: 0;
}

.toast {
  margin: 0 0 8px;
  font-size: 0.82rem;
  color: #86efac;
}

.composer-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.composer textarea {
  flex: 1;
  resize: none;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: #0f172a;
  color: #e2e8f0;
  padding: 10px 12px;
  font-family: inherit;
  font-size: 0.92rem;
}

.send {
  border: none;
  border-radius: 12px;
  padding: 12px 20px;
  background: linear-gradient(135deg, #fe2c55, #ff6b35);
  color: #fff;
  font-weight: 600;
  cursor: pointer;
}

.send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.page-error {
  position: fixed;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(239, 68, 68, 0.9);
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 0.85rem;
}

.spin {
  opacity: 0.6;
}

.sync-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #86efac;
  margin-left: 6px;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.35;
  }
  50% {
    opacity: 1;
  }
}

.sidebar {
  border-right: none;
  border-left: 1px solid rgba(148, 163, 184, 0.12);
}

.side-body {
  padding: 16px;
}

.side-name {
  margin: 0 0 12px;
  font-weight: 600;
}

.side-meta {
  margin: 0 0 16px;
  color: #94a3b8;
  font-size: 0.85rem;
}

.side-links {
  display: grid;
  gap: 8px;
}

.side-links a {
  color: #fda4af;
  text-decoration: none;
  font-size: 0.85rem;
}

@media (max-width: 1100px) {
  .sidebar {
    display: none;
  }

  .chat-page {
    grid-template-columns: 200px 280px 1fr;
  }
}

@media (max-width: 900px) {
  .chat-page {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto 1fr;
    height: auto;
    min-height: calc(100vh - 77px);
  }

  .accounts,
  .convs {
    max-height: 160px;
  }
}
</style>
