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
const total = ref(0);

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
      pageSize: 30,
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

watch([filterAccountId, filterResult], () => {
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
  <div>
    <div class="head">
      <div>
        <h2>回复记录</h2>
        <p class="sub">自动 / 手动回复的执行结果与跳过原因</p>
      </div>
    </div>

    <section v-if="stat" class="stats">
      <div class="stat card">
        <span class="num">{{ stat.total }}</span>
        <span class="lbl">总计</span>
      </div>
      <div class="stat card ok">
        <span class="num">{{ stat.success }}</span>
        <span class="lbl">成功</span>
      </div>
      <div class="stat card bad">
        <span class="num">{{ stat.failed }}</span>
        <span class="lbl">失败</span>
      </div>
      <div class="stat card muted">
        <span class="num">{{ stat.skipped }}</span>
        <span class="lbl">跳过</span>
      </div>
    </section>

    <div class="toolbar card">
      <label>
        账号
        <select v-model="filterAccountId">
          <option value="">全部</option>
          <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
        </select>
      </label>
      <label>
        结果
        <select v-model="filterResult">
          <option value="">全部</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
          <option value="skipped">跳过</option>
          <option value="cooldown">冷却</option>
          <option value="quota_exceeded">超配额</option>
          <option value="silent">静默</option>
        </select>
      </label>
      <span class="hint">共 {{ total }} 条</span>
    </div>

    <section v-if="loading" class="card">加载中…</section>
    <section v-else-if="error" class="card error">{{ error }}</section>
    <section v-else-if="logs.length === 0" class="card empty">暂无回复记录</section>
    <section v-else class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>账号</th>
            <th>对方</th>
            <th>结果</th>
            <th>回复 / 原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in logs" :key="log.id">
            <td class="time">{{ log.sys_create_datetime || '—' }}</td>
            <td>{{ log.account_nickname || '—' }}</td>
            <td>{{ log.peer_nickname || '—' }}</td>
            <td>
              <span class="pill" :class="resultClass(log.result)">
                {{ log.result_display || resultLabel(log.result) }}
              </span>
            </td>
            <td class="detail">
              <div v-if="log.reply_text">{{ log.reply_text }}</div>
              <div v-if="log.error_message" class="err">{{ log.error_message }}</div>
              <div v-if="log.trigger_message_content" class="trigger">
                触发：{{ log.trigger_message_content }}
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="total > 30" class="pager">
      <button type="button" class="btn ghost sm" :disabled="page <= 1" @click="page--; loadLogs()">
        上一页
      </button>
      <span>第 {{ page }} 页</span>
      <button
        type="button"
        class="btn ghost sm"
        :disabled="page * 30 >= total"
        @click="page++; loadLogs()"
      >
        下一页
      </button>
    </div>
  </div>
</template>

<style scoped>
.head {
  margin-bottom: 16px;
}

.head h2 {
  margin: 0 0 6px;
}

.sub {
  margin: 0;
  color: #94a3b8;
  font-size: 0.9rem;
}

.card {
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-radius: 16px;
  padding: 18px 20px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}

.stat {
  text-align: center;
  padding: 14px;
}

.stat .num {
  display: block;
  font-size: 1.5rem;
  font-weight: 700;
}

.stat .lbl {
  color: #94a3b8;
  font-size: 0.82rem;
}

.stat.ok .num {
  color: #86efac;
}

.stat.bad .num {
  color: #fca5a5;
}

.stat.muted .num {
  color: #cbd5e1;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.toolbar label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.88rem;
  color: #94a3b8;
}

select {
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  background: #0f172a;
  color: #e2e8f0;
  padding: 8px 10px;
}

.hint {
  color: #64748b;
  font-size: 0.85rem;
}

.table-wrap {
  padding: 0;
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

th,
td {
  padding: 12px 14px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  text-align: left;
  vertical-align: top;
}

th {
  color: #94a3b8;
  font-weight: 600;
}

.time {
  white-space: nowrap;
  color: #94a3b8;
}

.pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 0.75rem;
}

.pill.ok {
  background: rgba(34, 197, 94, 0.15);
  color: #86efac;
}

.pill.bad {
  background: rgba(239, 68, 68, 0.12);
  color: #fca5a5;
}

.pill.muted {
  background: rgba(148, 163, 184, 0.12);
  color: #cbd5e1;
}

.detail .err {
  color: #fca5a5;
  margin-top: 4px;
}

.detail .trigger {
  color: #64748b;
  margin-top: 4px;
  font-size: 0.8rem;
}

.card.error {
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

.card.empty {
  text-align: center;
  color: #94a3b8;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 14px;
  color: #94a3b8;
}

.btn {
  border: none;
  border-radius: 10px;
  padding: 10px 16px;
  cursor: pointer;
}

.btn.sm {
  padding: 6px 12px;
  font-size: 0.82rem;
}

.btn.ghost {
  background: rgba(148, 163, 184, 0.12);
  color: #e2e8f0;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-width: 720px) {
  .stats {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
