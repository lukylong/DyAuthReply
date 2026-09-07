<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  AlertTriangle,
  FileText,
  RefreshCw,
  ChevronRight,
  X,
  CheckCircle2,
  CircleAlert,
  Clock,
  Filter,
} from "lucide-vue-next";
import { useClientRealtime } from "../composables/useClientRealtime";
import {
  getReplyLogStat,
  listAccounts,
  listReplyLogs,
  resultLabel,
  type DouyinAccount,
  type DouyinReplyLog,
  type DouyinReplyLogStat,
} from "../api/client";

const selected = ref<DouyinReplyLog | null>(null);
const detailDialog = ref<HTMLDialogElement | null>(null);
watch(selected, async (value) => {
  if (value) {
    await nextTick();
    detailDialog.value?.showModal();
  }
});
const loading = ref(true);
const sourceOptions = [
  { value: "automatic" as const, label: "自动回复" },
  { value: "manual" as const, label: "手动发送" },
  { value: "all" as const, label: "全部" },
];
const pendingCount = computed(
  () =>
    (stat.value?.pending ?? 0) +
    (stat.value?.uncertain ?? 0) +
    (stat.value?.partial ?? 0),
);
const skippedCount = computed(
  () =>
    (stat.value?.skipped ?? 0) +
    (stat.value?.cooldown ?? 0) +
    (stat.value?.quota_exceeded ?? 0) +
    (stat.value?.silent ?? 0),
);
function timeParts(value?: string | null) {
  const v = (value || "").replace("T", " ");
  return { day: v.slice(0, 10) || "—", time: v.slice(11, 19) || "—" };
}
function closeDetail(event: KeyboardEvent) {
  if (event.key === "Escape") selected.value = null;
}
function resetFilters() {
  filterAccountId.value = "";
  filterResult.value = "";
}

const error = ref("");
const accounts = ref<DouyinAccount[]>([]);
const logs = ref<DouyinReplyLog[]>([]);
const stat = ref<DouyinReplyLogStat | null>(null);
const filterAccountId = ref("");
const filterResult = ref("");
const filterMode = ref<"automatic" | "manual" | "all">("automatic");
const hasGap = ref(false);
let generation = 0;
let unsubscribe: (() => void) | undefined;
let disposed = false;
const page = ref(1);
const pageSize = ref(10);
const total = ref(0);
const PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100];

async function loadAccounts() {
  accounts.value = await listAccounts();
}

async function loadStat(current: number) {
  try {
    const value = await getReplyLogStat(
      filterAccountId.value || undefined,
      "all",
      filterMode.value,
    );
    if (!disposed && current === generation) stat.value = value;
  } catch {
    if (!disposed && current === generation) stat.value = null;
  }
}

async function loadLogs() {
  const current = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const res = await listReplyLogs({
      account_id: filterAccountId.value || undefined,
      mode: filterMode.value,
      result: filterResult.value || undefined,
      page: page.value,
      pageSize: pageSize.value,
    });
    if (disposed || current !== generation) return;
    hasGap.value = res.has_gap === true;
    logs.value = res.items ?? [];
    total.value = res.total ?? logs.value.length;
    await loadStat(current);
  } catch (e) {
    if (disposed || current !== generation) return;
    error.value =
      (e instanceof Error ? e.message : String(e)) || "回复记录读取失败";
    logs.value = [];
  } finally {
    if (current === generation) loading.value = false;
  }
}

function resultClass(result: string) {
  if (result === "success") return "ok";
  if (result === "failed") return "bad";
  return "muted";
}

watch([filterAccountId, filterResult, filterMode, pageSize], () => {
  page.value = 1;
  loadLogs();
});

onMounted(async () => {
  document.addEventListener("keydown", closeDetail);
  unsubscribe = useClientRealtime().subscribe({
    onReplyLogChanged: () => {
      if (!disposed) void loadLogs();
    },
  });
  try {
    await loadAccounts();
    await loadLogs();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    loading.value = false;
  }
});
onUnmounted(() => {
  document.removeEventListener("keydown", closeDetail);
  disposed = true;
  ++generation;
  unsubscribe?.();
});
</script>

<template>
  <div class="logs-page">
    <header class="head">
      <div>
        <p class="eyebrow">运营记录</p>
        <h1>发送与回复记录</h1>
        <p class="sub">追踪每次发送的结果，快速定位未回复的原因。</p>
      </div>
      <button class="btn-glass" @click="loadLogs" :disabled="loading">
        <RefreshCw :size="15" />刷新记录
      </button>
    </header>
    <section class="summary-strip" aria-label="回复统计">
      <div>
        <span><FileText :size="15" />触发总量</span
        ><b>{{ stat?.total ?? "—" }}</b>
      </div>
      <div class="ok">
        <span><CheckCircle2 :size="15" />发送成功</span
        ><b>{{ stat?.success ?? "—" }}</b>
      </div>
      <div class="bad">
        <span><CircleAlert :size="15" />发送失败</span
        ><b>{{ stat?.failed ?? "—" }}</b>
      </div>
      <div>
        <span><Filter :size="15" />规则跳过</span
        ><b>{{ stat ? skippedCount : "—" }}</b>
      </div>
      <div>
        <span><Clock :size="15" />待处理 / 部分送达</span
        ><b>{{ stat ? pendingCount : "—" }}</b>
      </div>
    </section>
    <section class="record-panel">
      <div class="filter-top">
        <div class="source-tabs" aria-label="记录来源">
          <button
            v-for="source in sourceOptions"
            :key="source.value"
            :aria-pressed="filterMode === source.value"
            :class="{ active: filterMode === source.value }"
            @click="filterMode = source.value"
          >
            {{ source.label }}
          </button>
        </div>
        <span class="record-count">{{ total }} 条记录</span>
      </div>
      <div class="filters">
        <Filter :size="16" class="filter-icon" /><label
          ><span class="sr-only">所属账号</span
          ><select v-model="filterAccountId">
            <option value="">全部账号</option>
            <option v-for="acc in accounts" :key="acc.id" :value="acc.id">
              {{ acc.nickname }}
            </option>
          </select></label
        >
        <label
          ><span class="sr-only">回复状态</span
          ><select v-model="filterResult">
            <option value="">全部状态</option>
            <option value="success">发送成功</option>
            <option value="failed">发送失败</option>
            <option value="pending">等待发送</option>
            <option value="uncertain">结果待核验</option>
            <option value="partial">部分送达</option>
            <option value="skipped">过滤跳过</option>
            <option value="cooldown">冷却跳过</option>
            <option value="quota_exceeded">超出日额度</option>
            <option value="silent">静默时段</option>
          </select></label
        >
        <button
          v-if="filterAccountId || filterResult"
          class="text-button"
          @click="resetFilters"
        >
          清除筛选
        </button>
        <span class="retention"
          >保留最近 30 天<span v-if="hasGap"> · 旧记录已轮转</span></span
        >
      </div>
      <div v-if="error" class="empty error" role="alert">
        <AlertTriangle :size="26" /><b>记录读取失败</b>
        <p>{{ error }}</p>
        <button class="btn-glass" @click="loadLogs">重新加载</button>
      </div>
      <div v-else-if="loading && !logs.length" class="empty">
        <RefreshCw :size="24" />
        <p>正在加载记录…</p>
      </div>
      <div v-else-if="!logs.length" class="empty">
        <FileText :size="30" /><b>暂无符合条件的记录</b>
        <p>试试切换记录来源，或清除筛选条件。</p>
      </div>
      <div v-else class="table-scroll" :aria-busy="loading">
        <table>
          <colgroup>
            <col class="col-time" />
            <col class="col-person" />
            <col class="col-result" />
            <col />
            <col class="col-action" />
          </colgroup>
          <thead>
            <tr>
              <th>触发时间</th>
              <th>发送账号 / 联系人</th>
              <th>发送结果</th>
              <th>回复内容与原因</th>
              <th><span class="sr-only">详情</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="log in logs"
              :key="log.id"
              :class="{ selected: selected?.id === log.id }"
            >
              <td class="time">
                <b>{{ timeParts(log.sys_create_datetime).time }}</b
                ><small>{{ timeParts(log.sys_create_datetime).day }}</small>
              </td>
              <td class="person">
                <b :title="log.account_nickname || ''">{{
                  log.account_nickname || "未知账号"
                }}</b
                ><small :title="log.peer_nickname || ''"
                  >发给 {{ log.peer_nickname || "未知联系人" }}</small
                >
              </td>
              <td>
                <span class="result-badge" :class="resultClass(log.result)"
                  ><i />{{
                    log.result_display || resultLabel(log.result)
                  }}</span
                >
              </td>
              <td class="content">
                <b :class="{ 'error-text': !!log.error_message }">{{
                  log.error_message ||
                  log.reply_text ||
                  log.result_display ||
                  resultLabel(log.result)
                }}</b
                ><small v-if="log.trigger_message_content"
                  >收到：{{ log.trigger_message_content }}</small
                ><small v-else
                  >{{ log.mode === "manual" ? "手动发送" : "自动回复"
                  }}<span v-if="log.batch_id">
                    · 尝试 {{ log.attempt_count }} 次</span
                  ></small
                >
              </td>
              <td>
                <button
                  class="row-open"
                  @click="selected = log"
                  :aria-label="
                    '查看记录详情 ' + timeParts(log.sys_create_datetime).time
                  "
                >
                  <ChevronRight :size="18" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="pagination">
        <label
          >每页
          <select v-model.number="pageSize" aria-label="每页条数">
            <option v-for="n in PAGE_SIZE_OPTIONS" :key="n" :value="n">
              {{ n }} 条
            </option>
          </select></label
        ><span
          >第 {{ page }} /
          {{ Math.max(1, Math.ceil(total / pageSize)) }} 页</span
        >
        <div>
          <button
            class="btn-glass"
            :disabled="loading || page <= 1"
            @click="
              page--;
              loadLogs();
            "
          >
            上一页</button
          ><button
            class="btn-glass"
            :disabled="loading || page * pageSize >= total"
            @click="
              page++;
              loadLogs();
            "
          >
            下一页
          </button>
        </div>
      </footer>
    </section>
    <p class="footnote">
      “结果待核验”不等于发送失败，请勿重复发送。最多保留 10 万条记录。
    </p>
    <dialog
      v-if="selected"
      ref="detailDialog"
      class="detail-overlay"
      aria-labelledby="detail-title"
      @cancel="selected = null"
      @click.self="selected = null"
    >
      <section class="detail-panel">
        <header>
          <div>
            <p class="eyebrow">发送详情</p>
            <h2 id="detail-title">
              {{ selected.mode === "manual" ? "手动发送" : "自动回复" }}
            </h2>
          </div>
          <button
            class="row-open"
            aria-label="关闭记录详情"
            @click="selected = null"
          >
            <X :size="20" />
          </button>
        </header>
        <span class="result-badge" :class="resultClass(selected.result)"
          ><i />{{
            selected.result_display || resultLabel(selected.result)
          }}</span
        >
        <dl>
          <div>
            <dt>发送账号</dt>
            <dd>{{ selected.account_nickname || "—" }}</dd>
          </div>
          <div>
            <dt>联系人</dt>
            <dd>{{ selected.peer_nickname || "—" }}</dd>
          </div>
          <div>
            <dt>触发时间</dt>
            <dd>{{ selected.sys_create_datetime }}</dd>
          </div>
          <div>
            <dt>发送尝试</dt>
            <dd>{{ selected.attempt_count ?? 0 }} 次</dd>
          </div>
        </dl>
        <div v-if="selected.trigger_message_content" class="message-section">
          <h3>收到的消息</h3>
          <p>{{ selected.trigger_message_content }}</p>
        </div>
        <div v-if="selected.reply_text" class="message-section">
          <h3>回复内容</h3>
          <p>{{ selected.reply_text }}</p>
        </div>
        <div
          v-if="selected.error_message"
          class="message-section error-section"
        >
          <h3>未完成原因</h3>
          <p>{{ selected.error_message }}</p>
        </div>
        <p class="footnote">发送结果以平台回执和接收核验为准。</p>
      </section>
    </dialog>
  </div>
</template>
<style scoped>
.logs-page {
  display: grid;
  gap: 20px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.eyebrow {
  font-size: 12px;
  color: var(--text-muted);
  letter-spacing: 0.08em;
  margin: 0 0 8px;
}
h1 {
  margin: 0;
  font-size: 26px;
  letter-spacing: -0.7px;
}
.sub {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 7px 0 0;
}
.btn-glass {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  white-space: nowrap;
}
.summary-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
  border-radius: 14px;
  padding: 18px 0;
}
.summary-strip > div {
  padding: 0 16px;
  border-right: 1px solid var(--border-subtle);
}
.summary-strip > div:last-child {
  border: 0;
}
.summary-strip span {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--text-secondary);
}
.summary-strip b {
  display: block;
  font-size: 26px;
  margin-top: 8px;
  font-variant-numeric: tabular-nums;
}
.ok {
  color: var(--success);
}
.bad {
  color: var(--danger);
}
.record-panel {
  min-width: 0;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  overflow: hidden;
}
.filter-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-subtle);
}
.source-tabs {
  display: flex;
  gap: 3px;
  padding: 3px;
  border-radius: 8px;
  background: var(--bg-app);
}
.source-tabs button {
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  padding: 8px 13px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}
.source-tabs button.active {
  background: var(--bg-card);
  color: var(--brand-primary);
  box-shadow: 0 1px 3px var(--border-subtle);
}
.record-count,
.retention {
  font-size: 12px;
  color: var(--text-muted);
}
.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  flex-wrap: wrap;
}
.filters select {
  max-width: 210px;
}
.filter-icon {
  color: var(--text-muted);
}
select {
  border: 1px solid var(--border-subtle);
  border-radius: 7px;
  padding: 8px 28px 8px 10px;
  background: var(--bg-card);
  color: var(--text-primary);
  font-size: 12px;
}
.retention {
  margin-left: auto;
}
.text-button {
  border: 0;
  background: transparent;
  color: var(--brand-primary);
  font-size: 12px;
  cursor: pointer;
}
.table-scroll {
  overflow: auto;
}
table {
  width: 100%;
  min-width: 690px;
  border-collapse: collapse;
  table-layout: fixed;
  text-align: left;
}
.col-time {
  width: 125px;
}
.col-person {
  width: 170px;
}
.col-result {
  width: 128px;
}
.col-action {
  width: 48px;
}
th {
  padding: 12px 18px;
  background: var(--bg-app);
  font-size: 11px;
  color: var(--text-secondary);
  font-weight: 500;
  white-space: nowrap;
}
td {
  padding: 16px 18px;
  border-top: 1px solid var(--border-subtle);
  vertical-align: middle;
  font-size: 13px;
}
tbody tr:hover,
tbody tr.selected {
  background: var(--brand-primary-soft);
}
td b,
td small {
  display: block;
}
td b {
  font-weight: 500;
}
.time {
  font-variant-numeric: tabular-nums;
}
.time b {
  font-size: 13px;
}
.time small {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 5px;
}
.person b,
.person small,
.content b,
.content small {
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.person small,
.content small {
  color: var(--text-muted);
  font-size: 12px;
  margin-top: 6px;
}
.content .error-text {
  color: var(--danger);
}
.result-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  word-break: keep-all;
  border-radius: 6px;
  font-size: 11px;
  padding: 5px 7px;
  background: var(--bg-app);
  color: var(--text-secondary);
}
.result-badge i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.result-badge.ok {
  background: var(--success-soft);
  color: var(--success);
}
.result-badge.bad {
  background: var(--danger-soft);
  color: var(--danger);
}
.row-open {
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 7px;
  padding: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}
.row-open:hover {
  background: var(--border-subtle);
}
.pagination {
  display: flex;
  gap: 18px;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
  color: var(--text-secondary);
}
.pagination > div {
  display: flex;
  gap: 8px;
}
.pagination select {
  margin-left: 5px;
}
.pagination button {
  padding: 7px 12px;
  font-size: 12px;
}
.footnote {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.7;
  margin: 0;
}
.empty {
  display: grid;
  justify-items: center;
  text-align: center;
  padding: 55px 20px;
  gap: 12px;
  color: var(--text-muted);
}
.empty p {
  margin: 0;
  font-size: 13px;
}
.empty b {
  color: var(--text-secondary);
}
.error {
  color: var(--danger);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}
.detail-overlay {
  margin: 0 0 0 auto;
  padding: 0;
  border: 0;
  max-height: none;
  max-width: none;
  width: min(440px, 100vw);
  height: 100%;
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, var(--text-primary) 25%, transparent);
  z-index: 50;
  display: flex;
  justify-content: flex-end;
}
.detail-overlay::backdrop {
  background: color-mix(in srgb, var(--text-primary) 25%, transparent);
}
.detail-panel {
  width: min(440px, 100vw);
  height: 100%;
  box-sizing: border-box;
  overflow: auto;
  padding: 28px;
  background: var(--bg-card);
  box-shadow: -8px 0 30px var(--border-subtle);
}
.detail-panel header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}
.detail-panel h2 {
  font-size: 21px;
  margin: 0;
}
.detail-panel dl {
  margin: 24px 0;
}
.detail-panel dl > div {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  padding: 11px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--border-subtle);
}
dt {
  color: var(--text-muted);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.message-section {
  margin-bottom: 22px;
}
.message-section h3 {
  font-size: 12px;
  color: var(--text-secondary);
}
.message-section p {
  font-size: 13px;
  line-height: 1.8;
  background: var(--bg-app);
  padding: 14px;
  border-radius: 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.error-section p {
  background: var(--danger-soft);
  color: var(--danger);
}
button:focus-visible,
select:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 2px;
}
@media (max-width: 700px) {
  .summary-strip {
    grid-template-columns: repeat(2, 1fr);
    gap: 18px;
  }
  .summary-strip > div:nth-child(2) {
    border: 0;
  }
  .head {
    align-items: flex-start;
  }
  .head h1 {
    font-size: 22px;
  }
  .retention {
    width: 100%;
    margin-left: 0;
  }
  .pagination {
    flex-wrap: wrap;
  }
  .filters select {
    max-width: 165px;
  }
  .head > .btn-glass {
    font-size: 12px;
    padding: 8px;
  }
}
</style>
