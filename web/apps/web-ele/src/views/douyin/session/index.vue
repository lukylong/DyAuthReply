<script lang="ts" setup>
import type {
  DouyinConversationItem,
  DouyinMessageItem,
  DouyinSession,
} from '#/api/core/douyin';

import { computed, onMounted, onUnmounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElDialog,
  ElDrawer,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElProgress,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  controlSession,
  getLiveSessions,
  getSessionConversations,
  getSessionMessages,
  sendManualReply,
  triggerAutoReplyTest,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinSession' });

const sessions = ref<DouyinSession[]>([]);
const loading = ref(false);
let timer: null | ReturnType<typeof setInterval> = null;
const manualDrawerVisible = ref(false);
const manualLoading = ref(false);
const manualSending = ref(false);
const activeSession = ref<DouyinSession | null>(null);
const conversations = ref<DouyinConversationItem[]>([]);
const activeConversationId = ref('');
const messages = ref<DouyinMessageItem[]>([]);
const manualReplyText = ref('');
const autoReplyTestText = ref('');

const activeConversation = computed(
  () => conversations.value.find((item) => item.id === activeConversationId.value) || null,
);

async function loadSessions() {
  loading.value = true;
  try {
    sessions.value = await getLiveSessions();
  } finally {
    loading.value = false;
  }
}

function statusType(status: string) {
  return (
    {
      idle: 'info',
      running: 'success',
      paused: 'warning',
      error: 'danger',
      stopped: '',
    }[status] || 'info'
  );
}

function statusLabel(status: string) {
  return (
    {
      idle: '空闲',
      running: '运行中',
      paused: '已暂停',
      error: '异常',
      stopped: '已停止',
    }[status] || status
  );
}

async function onControl(
  row: DouyinSession,
  action: 'pause' | 'restart' | 'resume' | 'stop',
) {
  const actionLabel = { pause: '暂停', resume: '恢复', stop: '停止', restart: '重启' }[action];
  await ElMessageBox.confirm(
    `确定 ${actionLabel} 账号 "${row.account_nickname}" 的会话？`,
    '提示',
    { type: 'warning' },
  );
  const res = await controlSession(row.id, action);
  if (res.success) ElMessage.success(res.message);
  else ElMessage.error(res.message);
  await loadSessions();
}

function timeSince(dt?: null | string): string {
  if (!dt) return '-';
  const diff = Date.now() - new Date(dt).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h`;
}

async function openManualReply(row: DouyinSession) {
  activeSession.value = row;
  manualDrawerVisible.value = true;
  activeConversationId.value = '';
  messages.value = [];
  manualReplyText.value = '';
  manualLoading.value = true;
  try {
    conversations.value = await getSessionConversations(row.id);
  } finally {
    manualLoading.value = false;
  }
}

async function loadConversationMessages(conversationId: string) {
  if (!activeSession.value || !conversationId) return;
  manualLoading.value = true;
  try {
    messages.value = await getSessionMessages(activeSession.value.id, conversationId);
    activeConversationId.value = conversationId;
  } finally {
    manualLoading.value = false;
  }
}

async function onSendManualReply() {
  if (!activeSession.value || !activeConversationId.value) {
    ElMessage.warning('请先选择一个会话');
    return;
  }
  if (!manualReplyText.value.trim()) {
    ElMessage.warning('请输入回复内容');
    return;
  }
  manualSending.value = true;
  try {
    const res = await sendManualReply(
      activeSession.value.id,
      activeConversationId.value,
      manualReplyText.value,
    );
    if (res.success) {
      ElMessage.success(res.message || '手动回复指令已下发');
      manualReplyText.value = '';
      await loadConversationMessages(activeConversationId.value);
    } else {
      ElMessage.error(res.message || '手动回复失败');
    }
  } finally {
    manualSending.value = false;
  }
}

async function onTriggerAutoReplyTest() {
  if (!activeSession.value || !activeConversationId.value) {
    ElMessage.warning('请先选择一个会话');
    return;
  }
  if (!autoReplyTestText.value.trim()) {
    ElMessage.warning('请输入一条模拟用户消息');
    return;
  }
  manualSending.value = true;
  try {
    const res = await triggerAutoReplyTest(
      activeSession.value.id,
      activeConversationId.value,
      autoReplyTestText.value,
    );
    if (res.success) {
      ElMessage.success(res.message || '已触发自动回复测试');
      autoReplyTestText.value = '';
      await loadConversationMessages(activeConversationId.value);
    } else {
      ElMessage.error(res.message || '自动回复测试失败');
    }
  } finally {
    manualSending.value = false;
  }
}

onMounted(() => {
  loadSessions();
  timer = setInterval(loadSessions, 5000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <Page
    title="并发会话监控"
    description="实时显示 worker 托管的每个账号浏览器会话状态，5 秒自动刷新"
  >
    <ElCard shadow="never">
      <template #header>
        <div class="card-header">
          <span>
            在线会话
            <ElTag type="success">{{ sessions.length }}</ElTag>
          </span>
          <ElButton size="small" @click="loadSessions">立即刷新</ElButton>
        </div>
      </template>

      <ElTable :data="sessions" v-loading="loading" stripe>
        <ElTableColumn prop="account_nickname" label="账号" min-width="140" />
        <ElTableColumn label="状态" width="90">
          <template #default="{ row }">
            <ElTag :type="statusType(row.status) as any" size="small">
              {{ statusLabel(row.status) }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="心跳" width="120">
          <template #default="{ row }">
            <span v-if="row.is_alive" class="alive-dot" />
            <span>{{ timeSince(row.last_heartbeat) }} 前</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="worker_id" label="Worker" min-width="160" show-overflow-tooltip />
        <ElTableColumn label="今日消息" width="90">
          <template #default="{ row }">
            {{ row.messages_today }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="今日回复" width="90">
          <template #default="{ row }">
            <span :class="{ 'text-warning': row.errors_today > 0 }">
              {{ row.replies_today }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="错误" width="70">
          <template #default="{ row }">
            <ElTag v-if="row.errors_today > 0" type="danger" size="small">
              {{ row.errors_today }}
            </ElTag>
            <span v-else>-</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="CPU" width="110">
          <template #default="{ row }">
            <ElProgress
              :percentage="Math.min(100, Math.round(row.cpu_percent))"
              :stroke-width="4"
              :show-text="true"
              :text-inside="false"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn label="内存" width="90">
          <template #default="{ row }">
            {{ Math.round(row.memory_mb) }} MB
          </template>
        </ElTableColumn>
        <ElTableColumn prop="proxy_url" label="代理" min-width="140" show-overflow-tooltip />
        <ElTableColumn label="操作" width="320" fixed="right">
          <template #default="{ row }">
            <ElButton
              link
              type="primary"
              size="small"
              @click="openManualReply(row)"
            >
              手动回复
            </ElButton>
            <ElButton
              v-if="row.status === 'running'"
              link
              type="warning"
              size="small"
              @click="onControl(row, 'pause')"
            >
              暂停
            </ElButton>
            <ElButton
              v-if="row.status === 'paused'"
              link
              type="success"
              size="small"
              @click="onControl(row, 'resume')"
            >
              恢复
            </ElButton>
            <ElButton
              link
              type="primary"
              size="small"
              @click="onControl(row, 'restart')"
            >
              重启
            </ElButton>
            <ElButton
              link
              type="danger"
              size="small"
              @click="onControl(row, 'stop')"
            >
              停止
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElDrawer
      v-model="manualDrawerVisible"
      :title="`手动回复测试 · ${activeSession?.account_nickname || ''}`"
      size="900px"
    >
      <div class="manual-layout">
        <ElCard class="manual-conv" shadow="never" header="会话列表">
          <ElTable
            :data="conversations"
            v-loading="manualLoading"
            highlight-current-row
            @row-click="(row) => loadConversationMessages(row.id)"
          >
            <ElTableColumn prop="peer_nickname" label="对方" min-width="120">
              <template #default="{ row }">
                {{ row.peer_nickname || row.peer_sec_uid }}
              </template>
            </ElTableColumn>
            <ElTableColumn prop="last_message_preview" label="最近消息" min-width="180" show-overflow-tooltip />
            <ElTableColumn prop="unread_count" label="未读" width="70" />
          </ElTable>
        </ElCard>

        <ElCard class="manual-chat" shadow="never" header="消息与发送">
          <div class="manual-chat-header">
            <span>当前会话：{{ activeConversation?.peer_nickname || activeConversation?.peer_sec_uid || '未选择' }}</span>
            <ElButton
              size="small"
              :disabled="!activeConversationId"
              @click="loadConversationMessages(activeConversationId)"
            >
              刷新消息
            </ElButton>
          </div>
          <div class="manual-messages" v-loading="manualLoading">
            <div
              v-for="msg in messages"
              :key="msg.id"
              class="manual-message"
              :class="msg.direction === 'out' ? 'is-out' : 'is-in'"
            >
              <div class="manual-message-meta">
                <ElTag :type="msg.direction === 'out' ? 'success' : 'info'" size="small">
                  {{ msg.direction === 'out' ? '我方' : '对方' }}
                </ElTag>
                <span>{{ msg.received_at || '-' }}</span>
              </div>
              <div class="manual-message-content">{{ msg.content }}</div>
            </div>
          </div>
          <div class="manual-editor">
            <ElInput
              v-model="manualReplyText"
              type="textarea"
              :rows="4"
              placeholder="输入一条测试回复，点击发送后由 worker 使用当前账号上下文发送"
            />
            <div class="manual-editor-actions">
              <ElButton
                type="primary"
                :loading="manualSending"
                @click="onSendManualReply"
              >
                发送测试回复
              </ElButton>
            </div>
          </div>
          <div class="manual-editor auto-reply-test">
            <ElInput
              v-model="autoReplyTestText"
              type="textarea"
              :rows="3"
              placeholder="输入一条模拟用户消息，按当前规则触发一次自动回复测试"
            />
            <div class="manual-editor-actions">
              <ElButton
                type="success"
                plain
                :loading="manualSending"
                @click="onTriggerAutoReplyTest"
              >
                按规则测试自动回复
              </ElButton>
            </div>
          </div>
        </ElCard>
      </div>
    </ElDrawer>
  </Page>
</template>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.alive-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  background: #67c23a;
  border-radius: 50%;
  box-shadow: 0 0 6px #67c23a;
  animation: pulse 1.6s infinite;
}

.text-warning {
  color: #e6a23c;
}

.manual-layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 180px);
}

.manual-conv {
  width: 340px;
  flex-shrink: 0;
}

.manual-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.manual-chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.manual-messages {
  flex: 1;
  overflow: auto;
  padding: 8px 0;
}

.manual-message {
  padding: 10px 12px;
  margin-bottom: 10px;
  border-radius: 10px;
  border: 1px solid #e5e7eb;
  background: #f8fafc;
}

.manual-message.is-out {
  background: #ecfdf3;
  border-color: #bbf7d0;
}

.manual-message-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
  color: #6b7280;
  font-size: 12px;
}

.manual-message-content {
  white-space: pre-wrap;
  word-break: break-word;
  color: #111827;
}

.manual-editor {
  margin-top: 12px;
}

.auto-reply-test {
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px dashed #d1d5db;
}

.manual-editor-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

@keyframes pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.4);
    opacity: 0.6;
  }
}
</style>
