<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import {
  getReplyLogStat,
  listAccounts,
  listReplyLogs,
  resultLabel,
  type DouyinAccount,
  type DouyinReplyLog,
  type DouyinReplyLogStat,
} from '../api/client';

const loading = ref(true);
const error = ref('');
const accounts = ref<DouyinAccount[]>([]);
const logs = ref<DouyinReplyLog[]>([]);
const stat = ref<DouyinReplyLogStat | null>(null);
const filterAccountId = ref('');
const filterResult = ref('');
const page = ref(1);
const pageSize = ref(10);
const total = ref(0);
const PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100];

async function loadAccounts() {
  accounts.value = await listAccounts();
}

async function loadStat() {
  try {
    stat.value = await getReplyLogStat(filterAccountId.value || undefined);
  } catch {
    stat.value = null;
  }
}

async function loadLogs() {
  loading.value = true;
  error.value = '';
  try {
    const res = await listReplyLogs({
      account_id: filterAccountId.value || undefined,
      result: filterResult.value || undefined,
      page: page.value,
      pageSize: pageSize.value,
    });
    logs.value = res.items ?? [];
    total.value = res.total ?? logs.value.length;
    await loadStat();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    logs.value = [];
  } finally {
    loading.value = false;
  }
}

function resultClass(result: string) {
  if (result === 'success') return 'ok';
  if (result === 'failed') return 'bad';
  return 'muted';
}

watch([filterAccountId, filterResult, pageSize], () => {
  page.value = 1;
  loadLogs();
});

onMounted(async () => {
  try {
    await loadAccounts();
    await loadLogs();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    loading.value = false;
  }
});
</script>

<template>
  <div class="logs-page">
    <div class="head">
      <div>
        <h2>自动回复记录</h2>
        <p class="sub">查看触发自动回复策略的私信请求审计，监控投递的成功率及过滤详情。</p>
      </div>
    </div>

    <!-- Stats Grid cards -->
    <section v-if="stat" class="stats-panel-grid">
      <div class="stat-glass-card glass-panel">
        <span class="num">{{ stat.total }}</span>
        <span class="lbl">触发总量</span>
      </div>
      <div class="stat-glass-card glass-panel ok">
        <span class="num">{{ stat.success }}</span>
        <span class="lbl">发送成功</span>
      </div>
      <div class="stat-glass-card glass-panel bad">
        <span class="num">{{ stat.failed }}</span>
        <span class="lbl">发送失败</span>
      </div>
      <div class="stat-glass-card glass-panel muted">
        <span class="num">{{ stat.skipped }}</span>
        <span class="lbl">风控/跳过</span>
      </div>
    </section>

    <!-- Toolbar controls -->
    <div class="toolbar glass-panel">
      <div class="filters-row">
        <label class="control-label">
          <span>所属账号</span>
          <select class="select-glass" v-model="filterAccountId">
            <option value="">全部抖音号</option>
            <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
          </select>
        </label>
        
        <label class="control-label">
          <span>回复状态</span>
          <select class="select-glass" v-model="filterResult">
            <option value="">全部记录</option>
            <option value="success">发送成功</option>
            <option value="failed">发送失败</option>
            <option value="skipped">过滤跳过</option>
            <option value="cooldown">频发防刷冷却</option>
            <option value="quota_exceeded">超出日额度限额</option>
            <option value="silent">运营静默时段</option>
          </select>
        </label>

        <label class="control-label">
          <span>每页</span>
          <select class="select-glass" v-model.number="pageSize">
            <option v-for="n in PAGE_SIZE_OPTIONS" :key="n" :value="n">{{ n }} 条</option>
          </select>
        </label>
      </div>
      <span class="toolbar-hint">当前条件下共 {{ total }} 条记录</span>
    </div>

    <div v-if="loading" class="loading-state glass-panel">
      <div class="dot-spinner"></div>
      <p>正在同步回复记录，请稍候...</p>
    </div>
    
    <div v-else-if="error" class="card error glass-panel">
      <span class="icon">⚠️</span>
      <div class="err-text">
        <h4>获取日志失败</h4>
        <p>{{ error }}</p>
      </div>
    </div>
    
    <div v-else-if="logs.length === 0" class="empty-state glass-panel">
      <div class="empty-icon">📝</div>
      <h3>暂无符合要求的触发记录</h3>
      <p>这里将记录每次关键词匹配或自动回复事件的触发时间与详细执行结果。</p>
    </div>

    <!-- Table List -->
    <section v-else class="table-container glass-panel">
      <div class="table-responsive">
        <table class="logs-table">
          <thead>
            <tr>
              <th>触发时间</th>
              <th>账号</th>
              <th>客户</th>
              <th>匹配结果</th>
              <th>回复详情 / 跳过解释</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id">
              <td class="time">{{ log.sys_create_datetime || '—' }}</td>
              <td class="account-name">{{ log.account_nickname || '—' }}</td>
              <td class="peer-name">{{ log.peer_nickname || '—' }}</td>
              <td>
                <span class="badge" :class="resultClass(log.result)">
                  {{ log.result_display || resultLabel(log.result) }}
                </span>
              </td>
              <td class="detail-cell">
                <div v-if="log.reply_text" class="reply-msg">{{ log.reply_text }}</div>
                <div v-if="log.error_message" class="error-msg">{{ log.error_message }}</div>
                <div v-if="log.trigger_message_content" class="trigger-msg">
                  <span class="tag">触发词</span>{{ log.trigger_message_content }}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Pagination -->
    <div v-if="total > pageSize" class="pager">
      <button type="button" class="btn-glass" :disabled="page <= 1" @click="page--; loadLogs()">
        ← 上一页
      </button>
      <span class="pager-lbl">第 {{ page }} / {{ Math.max(1, Math.ceil(total / pageSize)) }} 页</span>
      <button
        type="button"
        class="btn-glass"
        :disabled="page * pageSize >= total"
        @click="page++; loadLogs()"
      >
        下一页 →
      </button>
    </div>
  </div>
</template>

<style scoped>
.logs-page {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.head h2 {
  margin: 0 0 6px;
  font-size: 1.6rem;
  font-weight: 800;
}

.sub {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 40px;
  text-align: center;
  gap: 16px;
}

.dot-spinner {
  width: 32px;
  height: 32px;
  border: 2px solid rgba(255, 255, 255, 0.08);
  border-radius: 50%;
  border-top-color: var(--accent-crimson);
  animation: spin 1s infinite linear;
}

.empty-icon {
  font-size: 3rem;
  opacity: 0.8;
}

.empty-state h3 {
  margin: 0;
  font-size: 1.15rem;
  color: var(--text-primary);
}

.empty-state p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 0.88rem;
  max-width: 380px;
}

.card.error {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 16px 20px;
  border-color: rgba(239, 68, 68, 0.25);
  background: rgba(239, 68, 68, 0.05);
}

.card.error .icon {
  font-size: 1.5rem;
}

.err-text h4 {
  margin: 0 0 4px;
  color: #fca5a5;
}

.err-text p {
  margin: 0;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

/* Statistics Grid */
.stats-panel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
}

.stat-glass-card {
  text-align: center;
  padding: 18px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-glass-card .num {
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-glass-card .lbl {
  font-size: 0.78rem;
  color: var(--text-muted);
  font-weight: 600;
}

.stat-glass-card.ok .num {
  color: #4ade80;
  text-shadow: 0 0 10px rgba(74, 222, 128, 0.2);
}

.stat-glass-card.bad .num {
  color: #f87171;
  text-shadow: 0 0 10px rgba(248, 113, 113, 0.2);
}

.stat-glass-card.muted .num {
  color: #cbd5e1;
}

/* Toolbar Controls */
.toolbar {
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.filters-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.control-label {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.85rem;
  color: var(--text-secondary);
}


.toolbar-hint {
  font-size: 0.8rem;
  color: var(--text-muted);
}

/* Responsive Glass Table */
.table-container {
  overflow: hidden;
  border: 1px solid var(--glass-border);
  background: var(--glass-bg);
}

.table-responsive {
  width: 100%;
  overflow-x: auto;
}

.logs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
  text-align: left;
}

.logs-table th {
  padding: 14px 18px;
  font-weight: 600;
  color: var(--text-secondary);
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  background: rgba(0, 0, 0, 0.02);
}

.logs-table td {
  padding: 14px 18px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.03);
  vertical-align: top;
  color: var(--text-secondary);
}

.logs-table tr:hover td {
  background: rgba(255, 255, 255, 0.25);
}

.logs-table .time {
  white-space: nowrap;
  color: var(--text-muted);
  font-weight: 500;
}

.account-name, .peer-name {
  font-weight: 600;
  color: var(--text-primary);
}

.badge {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-muted);
  display: inline-block;
}

.badge.ok {
  background: rgba(34, 197, 94, 0.1);
  border-color: rgba(34, 197, 94, 0.2);
  color: #4ade80;
}

.badge.bad {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.2);
  color: #f87171;
}

.detail-cell {
  line-height: 1.5;
}

.reply-msg {
  color: var(--text-primary);
  font-weight: 500;
}

.error-msg {
  color: #f87171;
  font-size: 0.8rem;
  background: rgba(239, 68, 68, 0.05);
  padding: 4px 8px;
  border-radius: 6px;
  margin-top: 4px;
  display: inline-block;
  border: 1px solid rgba(239, 68, 68, 0.1);
}

.trigger-msg {
  font-size: 0.78rem;
  color: var(--text-muted);
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.trigger-msg .tag {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 0.7rem;
}

/* Pagination panel */
.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 8px;
}

.pager-lbl {
  font-size: 0.85rem;
  color: var(--text-secondary);
  font-weight: 500;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
