<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import {
  getWorkerCommandStatus,
  listAccounts,
  listConversations,
  listMessages,
  messageImageUrl,
  refreshConversationUser,
  sendManualReply,
  type ConversationItem,
  type DouyinAccount,
  type MessageItem,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const accounts = ref<DouyinAccount[]>([]);
const activeAccountId = ref('');
const conversations = ref<ConversationItem[]>([]);
const activeConversationId = ref('');
const messages = ref<MessageItem[]>([]);
const replyText = ref('');
const messagesEl = ref<HTMLElement | null>(null);
const messagesEndRef = ref<HTMLElement | null>(null);
const convsScrollEl = ref<HTMLElement | null>(null);
const stickToBottom = ref(true);

const loadingAccounts = ref(true);
const loadingConversations = ref(false);
const loadingMoreConvs = ref(false);
const loadingMessages = ref(false);
const syncing = ref(false);
const sending = ref(false);
const error = ref('');
const toast = ref('');
const convKeyword = ref('');
const convPage = ref(1);
const convTotal = ref(0);
const convHasMore = ref(false);
const CONV_PAGE_SIZE = 50;
const { licenseStatus: license, ensureStatus } = useClientLicense();

const activeAccount = computed(() =>
  accounts.value.find((a) => a.id === activeAccountId.value),
);
const activeConversation = computed(() =>
  conversations.value.find((c) => c.id === activeConversationId.value),
);
const convCountLabel = computed(() => {
  if (convTotal.value <= 0) return '';
  if (conversations.value.length >= convTotal.value) return `${convTotal.value}`;
  return `${conversations.value.length}/${convTotal.value}`;
});

let pollTimer: ReturnType<typeof setInterval> | null = null;
let convSearchTimer: ReturnType<typeof setTimeout> | null = null;
let convSig = '';
let msgSig = '';

// 缺失真实昵称的会话：自动调 refresh-user 拉取抖音真实昵称/头像后重渲染。
// 每个会话只尝试一次（成功或失败都记录），避免对未公开资料的用户反复打接口。
const peerResolveAttempted = new Set<string>();
let resolvingPeer = false;

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
  const uid = conv.peer_unique_id?.trim();
  if (uid && uid !== conv.peer_nickname?.trim()) return `@${uid}`;
  return '';
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
        `${c.id}:${c.last_message_at || ''}:${c.last_message_preview || ''}:${c.unread_count}:${c.peer_nickname || ''}:${c.peer_avatar || ''}`,
    )
    .join('|');
}

function messagesSignature(items: MessageItem[]) {
  return items.map((m) => `${m.id}:${m.received_at || ''}:${m.content || ''}`).join('|');
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

// 富媒体渲染辅助
// 注意：用户本地图片/视频是抖音加密 CDN 资源，直链多为密文无法直接 <img> 显示；
// 优先用消息自带的 inline_pic(base64 预览)，否则尝试直链，加载失败回退占位。
const brokenMedia = ref<Record<string, boolean>>({});
function onMediaError(id: string) {
  brokenMedia.value[id] = true;
}

function mediaKind(msg: MessageItem): string {
  const m = msg.media;
  if (m?.kind) return String(m.kind);
  if (msg.content_type === 'image') return 'image';
  if (msg.content_type === 'video') return 'video';
  if (msg.content_type === 'card') return 'share_video';
  return 'text';
}

function inlineDataUri(msg: MessageItem): string {
  const p = msg.media?.inline_pic;
  if (typeof p === 'string' && p.length > 0) {
    return `data:image/webp;base64,${p.replace(/\s+/g, '')}`;
  }
  return '';
}

// 加密图片/视频封面走后端解密代理；表情等公开资源用直链
function proxyImageUrl(msg: MessageItem): string {
  if (!activeAccountId.value || !activeConversationId.value) return '';
  return messageImageUrl(activeAccountId.value, activeConversationId.value, msg.id);
}

function imageSrc(msg: MessageItem): string {
  if (msg.media?.encrypted) return proxyImageUrl(msg);
  return inlineDataUri(msg) || msg.media?.url || msg.media?.cover_url || '';
}

function videoCover(msg: MessageItem): string {
  if (msg.media?.encrypted) return proxyImageUrl(msg);
  return inlineDataUri(msg) || msg.media?.cover_url || msg.media?.url || '';
}

function showImage(msg: MessageItem): boolean {
  return !brokenMedia.value[msg.id] && Boolean(imageSrc(msg));
}

function showVideoCover(msg: MessageItem): boolean {
  return !brokenMedia.value[msg.id] && Boolean(videoCover(msg));
}

function videoDurationLabel(msg: MessageItem): string {
  const sec = Math.round(Number(msg.media?.duration_s));
  return Number.isFinite(sec) && sec > 0 ? `${sec}s` : '视频';
}

function voiceDurationLabel(msg: MessageItem): string {
  const sec = Math.round(Number(msg.media?.duration_ms) / 1000);
  return Number.isFinite(sec) && sec > 0 ? `${sec}″` : '';
}

function openMedia(url?: string) {
  if (url) window.open(url, '_blank', 'noopener');
}

// 应用内图片预览（灯箱）
const previewUrl = ref('');
function previewImage(url?: string) {
  if (url) previewUrl.value = url;
}
function closePreview() {
  previewUrl.value = '';
}

// emoji 选择器（unicode 表情，走文本发送）
const replyTextarea = ref<HTMLTextAreaElement | null>(null);
const showEmoji = ref(false);
const EMOJI_LIST: string[] = [
  '😀', '😁', '😂', '🤣', '😊', '😍', '😘', '😎', '🤔', '😅',
  '😭', '😉', '😏', '😴', '🥰', '😋', '😜', '🤗', '🤭', '😇',
  '👍', '👎', '👌', '🙏', '👏', '💪', '🤝', '🙌', '✌️', '🤙',
  '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '💕', '💯', '🔥',
  '🎉', '🎁', '✨', '⭐', '🌟', '💰', '🧧', '🤑', '🛒', '📦',
  '😡', '😱', '😢', '😤', '🤧', '😷', '🥺', '😳', '🙄', '😬',
];
function toggleEmoji() {
  showEmoji.value = !showEmoji.value;
}
function insertEmoji(emoji: string) {
  const el = replyTextarea.value;
  if (!el) {
    replyText.value += emoji;
  } else {
    const start = el.selectionStart ?? replyText.value.length;
    const end = el.selectionEnd ?? replyText.value.length;
    replyText.value =
      replyText.value.slice(0, start) + emoji + replyText.value.slice(end);
    nextTick(() => {
      el.focus();
      const pos = start + emoji.length;
      el.setSelectionRange(pos, pos);
    });
  }
  showEmoji.value = false;
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
  const el = convsScrollEl.value;
  if (el) el.scrollTop = 0;
}

async function loadConversations(silent = false, append = false) {
  if (!activeAccountId.value) {
    conversations.value = [];
    convTotal.value = 0;
    convHasMore.value = false;
    return;
  }

  if (append) {
    if (!convHasMore.value || loadingMoreConvs.value || loadingConversations.value) return;
    loadingMoreConvs.value = true;
    try {
      const nextPage = convPage.value + 1;
      const res = await listConversations(activeAccountId.value, {
        page: nextPage,
        page_size: CONV_PAGE_SIZE,
        keyword: convKeyword.value.trim() || undefined,
      });
      const existing = new Set(conversations.value.map((c) => c.id));
      for (const item of res.items) {
        if (!existing.has(item.id)) conversations.value.push(item);
      }
      conversations.value = sortConversations(conversations.value);
      convPage.value = nextPage;
      convTotal.value = res.total;
      convHasMore.value = res.has_more;
    } catch (e) {
      if (!silent) toast.value = e instanceof Error ? e.message : String(e);
    } finally {
      loadingMoreConvs.value = false;
    }
    return;
  }

  if (!silent) loadingConversations.value = true;
  else syncing.value = true;
  try {
    const refreshSize =
      silent && conversations.value.length > CONV_PAGE_SIZE
        ? conversations.value.length
        : CONV_PAGE_SIZE;
    const res = await listConversations(activeAccountId.value, {
      page: 1,
      page_size: refreshSize,
      keyword: convKeyword.value.trim() || undefined,
    });
    const items = sortConversations(res.items);
    const sig = conversationsSignature(items);
    if (sig !== convSig) {
      convSig = sig;
      conversations.value = items;
      if (!silent) {
        await nextTick();
        resetConversationsScroll();
      }
    }
    convTotal.value = res.total;
    convHasMore.value = res.has_more;
    if (!silent) {
      convPage.value = Math.max(1, Math.ceil(items.length / CONV_PAGE_SIZE));
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
    void resolveMissingPeerInfo();
  } catch (e) {
    if (!silent) toast.value = e instanceof Error ? e.message : String(e);
    if (!silent) conversations.value = [];
  } finally {
    if (!silent) loadingConversations.value = false;
    syncing.value = false;
  }
}

function onConvsScroll() {
  const el = convsScrollEl.value;
  if (!el || !convHasMore.value || loadingMoreConvs.value) return;
  if (isNearBottom(el, 80)) void loadConversations(true, true);
}

function onConvSearchInput() {
  if (convSearchTimer) clearTimeout(convSearchTimer);
  convSearchTimer = setTimeout(() => {
    convPage.value = 1;
    convSig = '';
    void loadConversations();
  }, 300);
}

async function loadAccounts() {
  loadingAccounts.value = true;
  error.value = '';
  try {
    await ensureStatus();
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

async function resolveMissingPeerInfo() {
  if (resolvingPeer || !activeAccountId.value) return;
  const accountId = activeAccountId.value;
  const targets = conversations.value
    .filter((c) => !c.peer_nickname?.trim() && !peerResolveAttempted.has(c.id))
    .slice(0, 3);
  if (targets.length === 0) return;
  resolvingPeer = true;
  try {
    let changed = false;
    for (const c of targets) {
      peerResolveAttempted.add(c.id);
      try {
        const res = await refreshConversationUser(accountId, c.id);
        if (res.success) changed = true;
      } catch {
        // 单个会话拉取失败不影响其它会话，下次启动前不再重试该会话
      }
    }
    if (changed && accountId === activeAccountId.value) {
      convSig = '';
      await loadConversations(true);
    }
  } finally {
    resolvingPeer = false;
  }
}

async function loadMessages(silent = false, force = false) {
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
    if (force || sig !== msgSig) {
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
  convPage.value = 1;
  convTotal.value = 0;
  convHasMore.value = false;
  convKeyword.value = '';
  peerResolveAttempted.clear();
}

function selectConversation(id: string) {
  if (activeConversationId.value === id) {
    void loadMessages(true, true);
    return;
  }
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
  pollTimer = setInterval(pollTick, 3000);
}

function onVisibilityChange() {
  if (!document.hidden) pollTick();
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function refreshAfterSend(maxAttempts = 5, intervalMs = 1200) {
  for (let i = 0; i < maxAttempts; i += 1) {
    await sleep(intervalMs);
    await Promise.all([loadMessages(true, true), loadConversations(true)]);
  }
}

async function waitManualReplyResult(commandId: string, sentText: string) {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const st = await getWorkerCommandStatus(commandId);
    if (st.status === 'success') {
      msgSig = '';
      await Promise.all([loadMessages(true, true), loadConversations(true)]);
      return { ok: true as const };
    }
    if (st.status === 'failed') {
      return { ok: false as const, error: st.error || '发送失败' };
    }
    if (st.consumed && st.status === 'unknown') {
      await refreshAfterSend(3, 800);
      const latest = messages.value.filter((m) => m.direction === 'out').at(-1);
      if (latest?.content?.trim() === sentText.trim()) {
        return { ok: true as const };
      }
      return { ok: false as const, error: '发送结果未知，请刷新消息列表' };
    }
    await sleep(500);
  }
  await refreshAfterSend(3, 1000);
  const latest = messages.value.filter((m) => m.direction === 'out').at(-1);
  if (latest?.content?.trim() === sentText.trim()) {
    return { ok: true as const };
  }
  return { ok: false as const, error: '等待回执超时，请稍后刷新重试' };
}

async function onSend() {
  if (!license.value?.can_use_business) {
    toast.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法发送消息`;
    return;
  }
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
    toast.value = '正在投递消息至抖音云端...';
    if (res.command_id) {
      const outcome = await waitManualReplyResult(res.command_id, text);
      if (outcome.ok) {
        toast.value = '发送成功';
        window.setTimeout(() => {
          if (toast.value === '发送成功') toast.value = '';
        }, 2500);
        return;
      }
      toast.value = outcome.error || '发送失败';
      return;
    }
    msgSig = '';
    await refreshAfterSend();
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

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (previewUrl.value) closePreview();
    if (showEmoji.value) showEmoji.value = false;
  }
}

onMounted(async () => {
  await loadAccounts();
  await loadConversations();
  startPolling();
  document.addEventListener('visibilitychange', onVisibilityChange);
  document.addEventListener('keydown', onKeydown);
});

onUnmounted(() => {
  stopPolling();
  if (convSearchTimer) clearTimeout(convSearchTimer);
  document.removeEventListener('visibilitychange', onVisibilityChange);
  document.removeEventListener('keydown', onKeydown);
});
</script>

<template>
  <div class="chat-page">
    <!-- Left Column: Accounts -->
    <aside class="col accounts">
      <div class="col-head">
        <span>抖音号</span>
      </div>
      <div v-if="loadingAccounts" class="col-empty">
        <div class="dot-spinner"></div>
      </div>
      <div v-else-if="accounts.length === 0" class="col-empty font-small">
        暂无绑定账号<br/>请先在“抖音账号”页导入。
      </div>
      <div class="account-list-scroll">
        <button
          v-for="acc in accounts"
          :key="acc.id"
          class="acc-item"
          :class="{ active: acc.id === activeAccountId }"
          type="button"
          @click="selectAccount(acc.id)"
        >
          <div class="acc-avatar">
            <img v-if="acc.avatar" :src="acc.avatar" alt="avatar" />
            <span v-else>{{ avatarInitial(acc.nickname) }}</span>
          </div>
          <div class="acc-info">
            <span class="acc-name">{{ acc.nickname }}</span>
            <span class="acc-sub">今日 {{ acc.reply_today ?? 0 }} 次</span>
          </div>
        </button>
      </div>
    </aside>

    <!-- Middle Column: Conversations -->
    <aside class="col convs">
      <div class="col-head">
        <span>会话列表<span v-if="convCountLabel" class="conv-count"> · {{ convCountLabel }}</span></span>
        <div class="status-wrap">
          <span v-if="loadingConversations" class="loading-indicator">同步中</span>
          <span v-else-if="syncing" class="sync-dot" title="正在与抖音云端同步" />
        </div>
      </div>
      <div v-if="activeAccountId" class="conv-search-wrap">
        <input
          v-model="convKeyword"
          class="conv-search"
          type="search"
          placeholder="搜索昵称 / 抖音号"
          @input="onConvSearchInput"
        />
      </div>
      <div v-if="!activeAccountId" class="col-empty">请先选择托管账号</div>
      <div v-else-if="conversations.length === 0 && !loadingConversations" class="col-empty">
        当前暂无新会话<br/><span class="sub-hint">当客户发送私信且自动 Worker 运行时会在此显示。</span>
      </div>
      <div
        v-else
        ref="convsScrollEl"
        class="convs-list-scroll"
        @scroll="onConvsScroll"
      >
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
              <div class="conv-preview">{{ previewText(conv) }}</div>
            </div>
            <span v-if="conv.unread_count > 0" class="unread-badge">{{ conv.unread_count }}</span>
          </div>
        </button>
        <div v-if="loadingMoreConvs" class="conv-load-more">加载更多…</div>
        <div v-else-if="convHasMore" class="conv-load-more hint">向下滚动加载更多</div>
      </div>
    </aside>

    <!-- Main Section: Chat Interface -->
    <section class="chat-main">
      <div v-if="!activeConversation" class="chat-empty">
        <div class="empty-icon">💬</div>
        <h3>私信对话看板</h3>
        <p class="hint">请在左侧选择具体客户以查阅消息并进行手动交互</p>
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
          </div>
          <div v-if="activeAccount" class="chat-head-meta">
            <span class="account-badge">@ {{ activeAccount.nickname }}</span>
          </div>
        </header>

        <div ref="messagesEl" class="messages" @scroll="onMessagesScroll">
          <div v-if="loadingMessages && messages.length === 0" class="chat-loading inline">正在拉取记录...</div>
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
              <!-- 图片 / 表情贴纸 -->
              <template v-if="mediaKind(msg) === 'image' || mediaKind(msg) === 'emoji'">
                <img
                  v-if="showImage(msg)"
                  :src="imageSrc(msg)"
                  class="msg-media-img"
                  :class="{ emoji: mediaKind(msg) === 'emoji' }"
                  loading="lazy"
                  @click="previewImage(imageSrc(msg))"
                  @error="onMediaError(msg.id)"
                />
                <span v-else class="msg-media-fallback" @click="previewImage(imageSrc(msg))">
                  🖼️ {{ msg.content || '[图片]' }}
                </span>
              </template>

              <!-- 视频文件 / 分享视频卡片 -->
              <template v-else-if="mediaKind(msg) === 'video' || mediaKind(msg) === 'share_video'">
                <div class="msg-video" @click="previewImage(videoCover(msg))">
                  <img
                    v-if="showVideoCover(msg)"
                    :src="videoCover(msg)"
                    class="msg-media-img"
                    loading="lazy"
                    @error="onMediaError(msg.id)"
                  />
                  <span v-else class="msg-media-fallback">🎬 {{ msg.content || '[视频]' }}</span>
                  <span class="msg-video-tag">▶ {{ videoDurationLabel(msg) }}</span>
                </div>
              </template>

              <!-- 语音（仅展示占位+时长） -->
              <template v-else-if="mediaKind(msg) === 'voice'">
                <span class="msg-voice" @click="openMedia(msg.media?.url)">
                  🎤 语音 {{ voiceDurationLabel(msg) }}
                </span>
              </template>

              <!-- 文本 / 兜底 -->
              <p v-else class="text">{{ msg.content }}</p>

              <time>{{ formatTime(msg.received_at) }}</time>
            </div>
            <div v-if="msg.direction === 'out'" class="avatar xs out-av">
              <img v-if="activeAccount?.avatar" :src="activeAccount.avatar" alt="" />
              <span v-else>{{ avatarInitial(activeAccount?.nickname || '我') }}</span>
            </div>
          </div>
          <p v-if="messages.length === 0 && !loadingMessages" class="no-msg">暂无历史消息记录</p>
          <div ref="messagesEndRef" class="messages-end" aria-hidden="true" />
        </div>

        <footer class="composer">
          <p v-if="license && !license.can_use_business" class="toast-tip error">
            当前授权状态为「{{ license.state_label }}」，手动发送已限制。请先到“客户端授权”页处理。
          </p>
          <transition name="fade">
            <p v-if="toast" class="toast-tip" :class="{ error: toast.includes('失败') || toast.includes('超时') }">
              {{ toast }}
            </p>
          </transition>
          <div class="composer-row">
            <div class="composer-tools">
              <button
                type="button"
                class="emoji-btn"
                title="表情"
                :disabled="license ? !license.can_use_business : false"
                @click="toggleEmoji"
              >
                😀
              </button>
              <div v-if="showEmoji" class="emoji-panel">
                <button
                  v-for="e in EMOJI_LIST"
                  :key="e"
                  type="button"
                  class="emoji-item"
                  @click="insertEmoji(e)"
                >
                  {{ e }}
                </button>
              </div>
            </div>
            <textarea
              ref="replyTextarea"
              v-model="replyText"
              rows="2"
              placeholder="输入消息以手动回复，按 Enter 发送，Shift + Enter 换行..."
              :disabled="license ? !license.can_use_business : false"
              @keydown.enter.exact.prevent="onSend"
            />
            <button
              type="button"
              class="btn-glass btn-primary-glass send-btn"
              :disabled="sending || !replyText.trim() || (license ? !license.can_use_business : false)"
              @click="onSend"
            >
              {{ sending ? '投递中' : '发送' }}
            </button>
          </div>
        </footer>
      </template>
    </section>

    <p v-if="error" class="page-error">{{ error }}</p>

    <!-- 图片预览灯箱 -->
    <div v-if="previewUrl" class="img-preview-mask" @click="closePreview">
      <button class="img-preview-close" type="button" @click.stop="closePreview">
        ✕
      </button>
      <img :src="previewUrl" class="img-preview-img" alt="预览" @click.stop />
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: grid;
  grid-template-columns: 180px 280px 1fr;
  height: 100%;
  width: 100%;
  min-height: 0;
  overflow: hidden;
  border-radius: inherit;
}

.col {
  border-right: 1px solid rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.col-head {
  padding: 18px 16px;
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-primary);
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.col-empty {
  padding: 30px 16px;
  color: var(--text-muted);
  font-size: 0.85rem;
  line-height: 1.6;
  text-align: center;
}

.sub-hint {
  font-size: 0.72rem;
  color: var(--text-muted);
  display: block;
  margin-top: 6px;
}

.font-small {
  font-size: 0.75rem;
}

/* Account items */
.account-list-scroll, .convs-list-scroll {
  flex: 1;
  overflow-y: auto;
}

.acc-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: transparent;
  border: none;
  border-bottom: 1px solid rgba(0, 0, 0, 0.02);
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
  transition: var(--transition-quick);
}

.acc-item:hover {
  background: rgba(255, 255, 255, 0.25);
  color: var(--text-primary);
}

.acc-item.active {
  background: rgba(255, 255, 255, 0.55);
  color: var(--text-primary);
  border-left: 3px solid rgba(0, 0, 0, 0.45);
}

.acc-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.35);
  border: 1px solid rgba(0, 0, 0, 0.04);
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 0.8rem;
  color: var(--text-primary);
}
.acc-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.acc-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.acc-name {
  font-size: 0.82rem;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-sub {
  font-size: 0.7rem;
  color: var(--text-muted);
  margin-top: 2px;
}

/* Conversations List */
.conv-count {
  font-size: 0.72rem;
  color: var(--text-muted);
  font-weight: 500;
}

.conv-search-wrap {
  padding: 8px 10px 0;
}

.conv-search {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: rgba(255, 255, 255, 0.35);
  color: var(--text-primary);
  font-size: 0.78rem;
  outline: none;
}

.conv-search:focus {
  border-color: rgba(0, 0, 0, 0.18);
  background: rgba(255, 255, 255, 0.55);
}

.conv-load-more {
  text-align: center;
  padding: 10px 8px 14px;
  font-size: 0.72rem;
  color: var(--text-muted);
}

.conv-load-more.hint {
  opacity: 0.85;
}

.conv-item {
  width: 100%;
  display: block;
  background: transparent;
  border: none;
  border-bottom: 1px solid rgba(0, 0, 0, 0.02);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 12px 14px;
  text-align: left;
  transition: var(--transition-quick);
}

.conv-item:hover {
  background: rgba(255, 255, 255, 0.2);
}

.conv-item.active {
  background: rgba(255, 255, 255, 0.45);
  color: var(--text-primary);
}

.conv-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  position: relative;
}

.avatar {
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0.2));
  border: 1px solid rgba(0, 0, 0, 0.05);
  color: var(--text-primary);
  font-weight: 700;
}

.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar.sm {
  width: 38px;
  height: 38px;
  font-size: 0.8rem;
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
  background: rgba(255, 255, 255, 0.7);
}

.conv-body {
  flex: 1;
  min-width: 0;
}

.conv-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.conv-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  font-size: 0.68rem;
  color: var(--text-muted);
}

.conv-preview {
  font-size: 0.78rem;
  color: var(--text-secondary);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.unread-badge {
  background: rgba(0, 0, 0, 0.08);
  border: 1px solid rgba(0, 0, 0, 0.05);
  color: var(--text-primary);
  font-size: 0.68rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 99px;
  position: absolute;
  right: 0;
  bottom: 0;
}

/* Chat Main Pane */
.chat-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  flex: 1;
  background: rgba(255, 255, 255, 0.15);
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  text-align: center;
  padding: 40px;
}

.empty-icon {
  font-size: 3.5rem;
  margin-bottom: 12px;
  opacity: 0.6;
}

.chat-empty h3 {
  margin: 0;
  font-size: 1.15rem;
  color: var(--text-primary);
}

.chat-empty .hint {
  font-size: 0.82rem;
  color: var(--text-muted);
  margin-top: 6px;
}

.chat-head {
  padding: 14px 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.2);
}

.chat-head-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-head-text strong {
  font-size: 0.95rem;
  color: var(--text-primary);
}

.peer-sub {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin-top: 1px;
}

.chat-head-meta {
  margin-left: auto;
}

.account-badge {
  background: rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(0, 0, 0, 0.05);
  font-size: 0.75rem;
  padding: 3px 10px;
  border-radius: 99px;
  color: var(--text-secondary);
}

/* Chat Message Log */
.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.msg-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}

.msg-row.in {
  justify-content: flex-start;
}

.msg-row.out {
  justify-content: flex-end;
}

.bubble {
  max-width: min(70%, 500px);
  padding: 11px 15px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: 0 2px 10px rgba(0,0,0,0.02);
}

.msg-row.in .bubble {
  border-bottom-left-radius: 4px;
}

.msg-row.out .bubble {
  border-bottom-right-radius: 4px;
  background: rgba(255, 255, 255, 0.85);
  border-color: rgba(255, 255, 255, 0.95);
  box-shadow: 0 4px 12px rgba(0,0,0,0.03);
}

.bubble .text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.9rem;
  line-height: 1.45;
  color: var(--text-primary);
}

.msg-media-img {
  display: block;
  max-width: 200px;
  max-height: 260px;
  border-radius: 10px;
  object-fit: cover;
  cursor: zoom-in;
}

.msg-media-img.emoji {
  max-width: 120px;
  max-height: 120px;
}

.msg-video {
  position: relative;
  display: inline-block;
  cursor: pointer;
}

.msg-video-tag {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 2px 7px;
  font-size: 0.72rem;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 6px;
}

.msg-voice {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.9rem;
  color: var(--text-primary);
  cursor: pointer;
}

.msg-media-fallback {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 8px 12px;
  font-size: 0.86rem;
  color: var(--text-primary);
  background: rgba(0, 0, 0, 0.04);
  border-radius: 10px;
  cursor: pointer;
}

.bubble time {
  display: block;
  margin-top: 6px;
  font-size: 0.68rem;
  opacity: 0.7;
  color: var(--text-muted);
  text-align: right;
}

.no-msg {
  text-align: center;
  color: var(--text-muted);
  font-size: 0.82rem;
  margin: auto;
}

.chat-loading {
  text-align: center;
  color: var(--text-secondary);
  padding: 16px;
  font-size: 0.85rem;
}

.messages-end {
  height: 2px;
  flex-shrink: 0;
}

/* Composer Panel */
.composer {
  border-top: 1px solid rgba(0, 0, 0, 0.05);
  padding: 14px 20px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.25);
  position: relative;
}

.toast-tip {
  position: absolute;
  top: -36px;
  left: 20px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(0, 0, 0, 0.05);
  color: var(--text-primary);
  font-size: 0.78rem;
  padding: 5px 12px;
  border-radius: 8px;
  box-shadow: 0 4px 10px rgba(0,0,0,0.05);
  margin: 0;
  z-index: 10;
}

.toast-tip.error {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.2);
  color: #ef4444;
}

.composer-row {
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.composer textarea {
  flex: 1;
  resize: none;
  border-radius: 12px;
  border: 1px solid var(--glass-border);
  background: rgba(255, 255, 255, 0.55);
  color: var(--text-primary);
  padding: 10px 14px;
  font-family: inherit;
  font-size: 0.9rem;
  outline: none;
  transition: var(--transition-quick);
  line-height: 1.4;
}

.composer textarea:focus {
  border-color: rgba(0, 0, 0, 0.25);
  background: rgba(255, 255, 255, 0.85);
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.02), 0 0 10px rgba(255, 255, 255, 0.05);
}

.send-btn {
  padding: 10px 22px;
  font-size: 0.88rem;
  border-radius: 12px;
  height: 42px;
}

.sync-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
  animation: glow 1.5s ease-in-out infinite;
}

.loading-indicator {
  font-size: 0.72rem;
  color: var(--text-muted);
}

@keyframes glow {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

.page-error {
  position: fixed;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(239, 68, 68, 0.1);
  backdrop-filter: blur(10px);
  padding: 8px 18px;
  border-radius: 10px;
  font-size: 0.82rem;
  box-shadow: 0 4px 15px rgba(0,0,0,0.05);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: #ef4444;
  z-index: 1000;
  margin: 0;
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

.dot-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid rgba(0, 0, 0, 0.08);
  border-radius: 50%;
  border-top-color: var(--text-muted);
  animation: spin 1s infinite linear;
  margin: 10px auto;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .chat-page {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto 1fr;
    height: auto;
  }
  .col.accounts, .col.convs {
    max-height: 200px;
  }
}

/* 图片预览灯箱 */
.img-preview-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.78);
  cursor: zoom-out;
  animation: img-preview-fade 0.12s ease;
}
@keyframes img-preview-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
.img-preview-img {
  max-width: 92vw;
  max-height: 92vh;
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.5);
  cursor: default;
}
.img-preview-close {
  position: fixed;
  top: 20px;
  right: 24px;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease;
}
.img-preview-close:hover {
  background: rgba(255, 255, 255, 0.3);
}

/* emoji 选择器 */
.composer-tools {
  position: relative;
  display: flex;
  align-items: flex-end;
}
.emoji-btn {
  width: 40px;
  height: 40px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.6);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease;
}
.emoji-btn:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.05);
}
.emoji-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.emoji-panel {
  position: absolute;
  bottom: 48px;
  left: 0;
  z-index: 50;
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 2px;
  width: 320px;
  max-height: 200px;
  overflow-y: auto;
  padding: 8px;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 12px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.18);
}
.emoji-item {
  border: none;
  background: transparent;
  font-size: 20px;
  line-height: 1;
  padding: 4px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.emoji-item:hover {
  background: rgba(0, 0, 0, 0.07);
}
</style>
