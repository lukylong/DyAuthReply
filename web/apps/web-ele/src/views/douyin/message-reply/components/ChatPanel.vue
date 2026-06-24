<script lang="ts" setup>
import type {
  DouyinConversationItem,
  DouyinMessageItem,
} from '#/api/core/douyin';

type ChatMessage = DouyinMessageItem & {
  send_status?: 'sending' | 'failed';
  send_error?: string;
};

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';

import {
  ElAvatar,
  ElButton,
  ElEmpty,
  ElImageViewer,
  ElInput,
  ElMessage,
  ElPopover,
} from 'element-plus';

import { getMessageImageObjectUrl } from '#/api/core/douyin/session';

defineOptions({ name: 'ChatPanel' });

const props = defineProps<{
  accountId?: string;
  conversationId?: string;
  conversation?: DouyinConversationItem | null;
  messages: ChatMessage[];
  loading?: boolean;
}>();

const emit = defineEmits<{
  refresh: [];
  sendMessage: [text: string];
  retryMessage: [text: string];
  dismissPending: [localId: string];
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
  return colors[hash % colors.length] || '#1890ff';
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

function handleKeydown(event: Event | KeyboardEvent) {
  // Enter 发送，Shift+Enter 换行
  const keyboardEvent = event as KeyboardEvent;
  if (keyboardEvent.key === 'Enter' && !keyboardEvent.shiftKey) {
    keyboardEvent.preventDefault();
    onSendMessage();
  }
}

// 富媒体渲染判定
// 用户本地图片/视频是抖音加密 CDN 资源，直链多为密文无法直接 <img> 显示；
// 优先用消息自带 inline_pic(base64 预览)，否则尝试直链，加载失败回退占位。
const brokenMedia = ref<Record<string, boolean>>({});
function onMediaError(id: string) {
  brokenMedia.value[id] = true;
}

function mediaKind(msg: DouyinMessageItem): string {
  const m = msg.media;
  if (m?.kind) return String(m.kind);
  if (msg.content_type === 'image') return 'image';
  if (msg.content_type === 'video') return 'video';
  if (msg.content_type === 'card') return 'share_video';
  return 'text';
}

function inlineDataUri(msg: DouyinMessageItem): string {
  const p = msg.media?.inline_pic;
  if (typeof p === 'string' && p.length > 0) {
    return `data:image/webp;base64,${p.replace(/\s+/g, '')}`;
  }
  return '';
}

// 加密图片(用户本地图/视频封面)需后端解密：web 端 <img> 无法携带 JWT，
// 故用 axios(blob) 拉取解密后的图片转 object URL；解密到达前先用 inline_pic 预览。
const decryptedUrls = ref<Record<string, string>>({});
const decrypting = ref<Record<string, boolean>>({});

async function ensureDecrypted(msg: DouyinMessageItem) {
  if (!msg.media?.encrypted) return;
  if (decryptedUrls.value[msg.id] || decrypting.value[msg.id]) return;
  if (!props.accountId || !props.conversationId) return;
  decrypting.value[msg.id] = true;
  try {
    decryptedUrls.value[msg.id] = await getMessageImageObjectUrl(
      props.accountId,
      props.conversationId,
      msg.id,
    );
  } catch {
    // 解密/下载失败：若无 inline 预览则回退占位
    if (!inlineDataUri(msg)) onMediaError(msg.id);
  } finally {
    decrypting.value[msg.id] = false;
  }
}

function revokeDecrypted() {
  for (const url of Object.values(decryptedUrls.value)) {
    if (url) URL.revokeObjectURL(url);
  }
  decryptedUrls.value = {};
  decrypting.value = {};
}

watch(
  () => props.messages,
  (list) => {
    for (const m of list || []) {
      if (m.media?.encrypted) void ensureDecrypted(m);
    }
  },
  { immediate: true },
);

watch(
  () => props.conversationId,
  () => {
    revokeDecrypted();
    brokenMedia.value = {};
  },
);

onBeforeUnmount(revokeDecrypted);

function imageSrc(msg: DouyinMessageItem): string {
  if (msg.media?.encrypted) {
    return decryptedUrls.value[msg.id] || inlineDataUri(msg) || '';
  }
  return inlineDataUri(msg) || msg.media?.url || msg.media?.cover_url || '';
}

function videoCover(msg: DouyinMessageItem): string {
  if (msg.media?.encrypted) {
    return decryptedUrls.value[msg.id] || inlineDataUri(msg) || '';
  }
  return inlineDataUri(msg) || msg.media?.cover_url || msg.media?.url || '';
}

function showImage(msg: DouyinMessageItem): boolean {
  return !brokenMedia.value[msg.id] && Boolean(imageSrc(msg));
}

function showVideoCover(msg: DouyinMessageItem): boolean {
  return !brokenMedia.value[msg.id] && Boolean(videoCover(msg));
}

function videoDurationLabel(msg: DouyinMessageItem): string {
  const d = msg.media?.duration_s;
  const sec = d == null ? Number.NaN : Math.round(Number(d));
  return Number.isFinite(sec) && sec > 0 ? `${sec}s` : '视频';
}

function voiceDurationLabel(msg: DouyinMessageItem): string {
  const d = msg.media?.duration_ms;
  const sec = d == null ? Number.NaN : Math.round(Number(d) / 1000);
  return Number.isFinite(sec) && sec > 0 ? `${sec}″` : '';
}

function openMedia(url?: string) {
  if (url) window.open(url, '_blank', 'noopener');
}

// 应用内图片预览（Element Plus 图片查看器）
const previewUrl = ref('');
function previewImage(url?: string) {
  if (url) previewUrl.value = url;
}
function closePreview() {
  previewUrl.value = '';
}

// emoji 选择器（unicode 表情，走文本发送）
const messageInputRef = ref();
const emojiVisible = ref(false);
const EMOJI_LIST: string[] = [
  '😀', '😁', '😂', '🤣', '😊', '😍', '😘', '😎', '🤔', '😅',
  '😭', '😉', '😏', '😴', '🥰', '😋', '😜', '🤗', '🤭', '😇',
  '👍', '👎', '👌', '🙏', '👏', '💪', '🤝', '🙌', '✌️', '🤙',
  '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '💕', '💯', '🔥',
  '🎉', '🎁', '✨', '⭐', '🌟', '💰', '🧧', '🤑', '🛒', '📦',
  '😡', '😱', '😢', '😤', '🤧', '😷', '🥺', '😳', '🙄', '😬',
];
function insertEmoji(emoji: string) {
  const el: HTMLTextAreaElement | undefined =
    messageInputRef.value?.textarea;
  if (!el) {
    messageInput.value += emoji;
  } else {
    const start = el.selectionStart ?? messageInput.value.length;
    const end = el.selectionEnd ?? messageInput.value.length;
    messageInput.value =
      messageInput.value.slice(0, start) +
      emoji +
      messageInput.value.slice(end);
    nextTick(() => {
      el.focus();
      const pos = start + emoji.length;
      el.setSelectionRange(pos, pos);
    });
  }
  emojiVisible.value = false;
}

function humanizeSendError(raw?: string | null): string {
  if (!raw) return '未知错误';
  const s = raw.trim();
  if (s.includes('7911') || s.includes('机房')) return '风控拦截(7911)，请换网络或稍后重试';
  if (s.includes('8101')) return '发送被限制(8101)';
  if (s.includes('登录失效') || s.includes('401') || s.includes('LoginExpired')) {
    return '登录失效，请重新导入登录态';
  }
  if (s.includes('超时')) return '等待回执超时';
  if (s.includes('signer') || s.includes('签名')) return '签名引擎未就绪';
  return s.length > 56 ? `${s.slice(0, 56)}…` : s;
}

function sendStatus(msg: ChatMessage): 'sending' | 'failed' | null {
  return msg.send_status ?? null;
}

function pendingLocalId(msg: ChatMessage): string | null {
  if (!msg.id.startsWith('local:')) return null;
  return msg.id.slice(6);
}

function retryFailed(msg: ChatMessage) {
  const id = pendingLocalId(msg);
  if (id) emit('dismissPending', id);
  emit('retryMessage', msg.content);
}

function dismissFailed(msg: ChatMessage) {
  const id = pendingLocalId(msg);
  if (id) emit('dismissPending', id);
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

  // 修复：跨年消息显示年份
  const isSameYear = date.getFullYear() === now.getFullYear();

  return date.toLocaleString('zh-CN', {
    year: isSameYear ? undefined : 'numeric',
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
          :class="{
            'is-mine': msg.direction === 'out',
            'send-failed': sendStatus(msg) === 'failed',
          }"
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
            <div
              class="message-bubble"
              :class="{
                'is-media': mediaKind(msg) !== 'text',
                'is-pending': sendStatus(msg) === 'sending',
              }"
            >
              <!-- 图片 / 表情贴纸 -->
              <template
                v-if="mediaKind(msg) === 'image' || mediaKind(msg) === 'emoji'"
              >
                <img
                  v-if="showImage(msg)"
                  :src="imageSrc(msg)"
                  class="msg-media-img"
                  :class="{ 'is-emoji': mediaKind(msg) === 'emoji' }"
                  loading="lazy"
                  @click="previewImage(imageSrc(msg))"
                  @error="onMediaError(msg.id)"
                />
                <span
                  v-else
                  class="msg-media-fallback"
                  @click="previewImage(imageSrc(msg))"
                >
                  🖼️ {{ msg.content || '[图片]' }}
                </span>
              </template>

              <!-- 视频文件 / 分享视频卡片（流加密，仅展示封面+时长，不可播放） -->
              <template
                v-else-if="
                  mediaKind(msg) === 'video' || mediaKind(msg) === 'share_video'
                "
              >
                <div
                  class="msg-video"
                  @click="previewImage(videoCover(msg))"
                >
                  <img
                    v-if="showVideoCover(msg)"
                    :src="videoCover(msg)"
                    class="msg-media-img"
                    loading="lazy"
                    @error="onMediaError(msg.id)"
                  />
                  <span v-else class="msg-media-fallback">
                    🎬 {{ msg.content || '[视频]' }}
                  </span>
                  <span class="msg-video-tag">▶ {{ videoDurationLabel(msg) }}</span>
                </div>
              </template>

              <!-- 语音（不接播放器，展示占位+时长，可点开音频源） -->
              <template v-else-if="mediaKind(msg) === 'voice'">
                <span
                  class="msg-voice"
                  @click="openMedia(msg.media?.url)"
                >
                  🎤 语音 {{ voiceDurationLabel(msg) }}
                </span>
              </template>

              <!-- 文本 / 兜底 -->
              <template v-else>
                {{ msg.content }}
              </template>
              <div
                v-if="msg.direction === 'out' && sendStatus(msg)"
                class="send-status"
                :class="sendStatus(msg)!"
              >
                <template v-if="sendStatus(msg) === 'sending'">发送中…</template>
                <template v-else>
                  发送失败 · {{ humanizeSendError(msg.send_error) }}
                  <ElButton link type="primary" size="small" @click.stop="retryFailed(msg)">
                    重试
                  </ElButton>
                  <ElButton link size="small" @click.stop="dismissFailed(msg)">
                    关闭
                  </ElButton>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="chat-footer">
      <ElInput
        ref="messageInputRef"
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
          <ElPopover
            v-model:visible="emojiVisible"
            :width="328"
            placement="top-start"
            trigger="click"
            popper-class="emoji-popover"
          >
            <template #reference>
              <button
                type="button"
                class="emoji-trigger"
                title="表情"
                :disabled="!conversationId"
              >
                😀
              </button>
            </template>
            <div class="emoji-grid">
              <button
                v-for="e in EMOJI_LIST"
                :key="e"
                type="button"
                class="emoji-grid-item"
                @click="insertEmoji(e)"
              >
                {{ e }}
              </button>
            </div>
          </ElPopover>
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

    <ElImageViewer
      v-if="previewUrl"
      :url-list="[previewUrl]"
      teleported
      @close="closePreview"
    />
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

  &.is-media {
    padding: 6px;
    background: transparent;
  }

  &.is-pending {
    opacity: 0.82;
  }
}

.message-item.send-failed .message-bubble {
  box-shadow: inset 0 0 0 1px rgba(245, 34, 45, 0.35);
}

.send-status {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;

  &.sending {
    color: #8c8c8c;
  }

  &.failed {
    color: #cf1322;
  }
}

.msg-media-img {
  display: block;
  max-width: 180px;
  max-height: 240px;
  border-radius: 8px;
  object-fit: cover;
  cursor: zoom-in;

  &.is-emoji {
    max-width: 120px;
    max-height: 120px;
  }
}

.msg-video {
  position: relative;
  display: inline-block;
  cursor: pointer;
}

.msg-video-placeholder {
  width: 160px;
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e8e8e8;
  border-radius: 8px;
  color: #595959;
}

.msg-video-tag {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 2px 6px;
  font-size: 12px;
  color: #fff;
  background: rgb(0 0 0 / 50%);
  border-radius: 4px;
}

.msg-voice {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 8px 14px;
  background: #f5f5f5;
  border-radius: 12px;
  cursor: pointer;
}

.msg-media-fallback {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 8px 12px;
  background: #f0f0f0;
  border-radius: 10px;
  font-size: 13px;
  color: #595959;
  cursor: pointer;
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

.emoji-trigger {
  border: none;
  background: transparent;
  font-size: 18px;
  line-height: 1;
  padding: 2px 4px;
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.12s ease;
}
.emoji-trigger:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.06);
}
.emoji-trigger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.emoji-grid {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 2px;
  max-height: 200px;
  overflow-y: auto;
}
.emoji-grid-item {
  border: none;
  background: transparent;
  font-size: 18px;
  line-height: 1;
  padding: 4px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.emoji-grid-item:hover {
  background: rgba(0, 0, 0, 0.07);
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
