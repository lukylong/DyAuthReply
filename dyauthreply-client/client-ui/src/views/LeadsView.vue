<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Download, Info, Phone, RefreshCw, Search } from 'lucide-vue-next';
import AppModal from '../components/AppModal.vue';
import {
  deleteCustomerLead, exportCustomerLeads, getLeadSources, getLeadStatus, listCustomerLeads,
  scanLeadHistory, setLeadCollection, type CustomerLead, type LeadFilters, type LeadSource, type LeadStatus,
} from '../api/client';
const router = useRouter();
const status = ref<LeadStatus | null>(null);
const items = ref<CustomerLead[]>([]);
const total = ref(0);
const page = ref(1);
const account = ref('');
const keyword = ref('');
const fromDate = ref('');
const toDate = ref('');
const loading = ref(true);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const scanOpen = ref(false);
const infoOpen = ref(false);
const deleteTarget = ref<CustomerLead | null>(null);
const sourceTarget = ref<CustomerLead | null>(null);
const sources = ref<LeadSource[]>([]);
const sourcePage = ref(1);
const sourceTotal = ref(0);
const sourceLoading = ref(false);
const sourceError = ref('');
const pages = computed(() => Math.max(1, Math.ceil(total.value / 50)));
let disposed = false;
let sequence = 0;
let sourceSequence = 0;
let timer: ReturnType<typeof setInterval> | undefined;
function dateMs(value: string, nextDay = false) {
  if (!value) return 0;
  const date = new Date(`${value}T00:00:00`);
  if (nextDay) date.setDate(date.getDate() + 1);
  return date.getTime();
}
function filters(): LeadFilters {
  if (fromDate.value && toDate.value && fromDate.value > toDate.value) throw new Error('结束日期应不早于开始日期');
  return { account: account.value, keyword: keyword.value.trim(), from_ms: dateMs(fromDate.value), to_ms: dateMs(toDate.value, true), page: page.value };
}
function message(e: unknown) { return e instanceof Error ? e.message : String(e); }
function time(value: string | number) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}
async function load(silent = false) {
  const current = ++sequence;
  if (!silent) loading.value = true;
  try {
    const query = filters();
    const [result, state] = await Promise.all([listCustomerLeads(query), getLeadStatus()]);
    if (disposed || current !== sequence) return;
    items.value = result.items; total.value = result.total; status.value = state;
    if (!silent) error.value = '';
    if (page.value > pages.value) { page.value = pages.value; await load(silent); }
  } catch (e) { if (!disposed && current === sequence) error.value = message(e); }
  finally { if (!disposed && current === sequence) loading.value = false; }
}
function search() { page.value = 1; void load(); }
async function action(fn: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true; error.value = ''; notice.value = '';
  try { await fn(); } catch (e) { error.value = message(e); }
  finally { busy.value = false; }
}
function toggle() {
  void action(async () => {
    if (!status.value) return;
    status.value = await setLeadCollection(!status.value.enabled);
    notice.value = status.value.enabled ? '已启用：采集此刻起的新私信，旧消息可手动扫描' : '已暂停新增采集；已入队任务会继续处理，已保存记录保留';
    await load(true);
  });
}
function scan() {
  void action(async () => {
    const result = await scanLeadHistory(account.value);
    scanOpen.value = false;
    notice.value = `已加入 ${result.queued} 条本地消息，后台提取中；重复消息不会重复计数`;
    await load(true);
  });
}
function remove() {
  void action(async () => {
    if (!deleteTarget.value) return;
    await deleteCustomerLead(deleteTarget.value.id); deleteTarget.value = null;
    notice.value = '记录及来源已删除；历史重扫不会恢复，客户未来再次发送号码可重新记录';
    await load(true);
  });
}
async function showSources(row: CustomerLead, nextPage = 1) {
  sourceTarget.value = row; sourcePage.value = nextPage; sources.value = []; sourceLoading.value = true; sourceError.value = '';
  const current = ++sourceSequence;
  try {
    const result = await getLeadSources(row.id, nextPage);
    if (disposed || current !== sourceSequence) return;
    sources.value = result.items; sourceTotal.value = result.total;
  } catch (e) { if (!disposed && current === sourceSequence) sourceError.value = message(e); }
  finally { if (!disposed && current === sourceSequence) sourceLoading.value = false; }
}
function closeSources() { ++sourceSequence; sourceTarget.value = null; }
function copy(row: CustomerLead) {
  void action(async () => { await navigator.clipboard.writeText(row.phone); notice.value = '手机号已复制'; });
}
function exportCsv() {
  void action(async () => {
    const result = await exportCustomerLeads(filters());
    const url = URL.createObjectURL(new Blob([result.csv], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a'); link.href = url; link.download = `客户留资-${new Date().toLocaleDateString('sv-SE')}.csv`;
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    notice.value = `已导出当前筛选下 ${result.total} 条记录（CSV）`;
  });
}
function openChat(row: CustomerLead) {
  void router.push({ path: '/chat', query: { account: row.account_id, conversation: row.conversation_id } });
}
onMounted(() => {
  void load();
  timer = setInterval(() => { if (!document.hidden && !busy.value && !loading.value) void load(true); }, 5000);
});
onUnmounted(() => { disposed = true; ++sequence; ++sourceSequence; if (timer) clearInterval(timer); });
</script>

<template>
  <div class="leads-page">
    <header class="page-head">
      <div class="heading-group">
        <h1><Phone :size="20" /> 客户留资</h1>
        <div class="inline-stats" aria-label="留资统计">
          <span><strong>{{ total }}</strong> 条记录</span>
          <span class="pending"><strong data-testid="lead-pending">{{ status?.pending ?? '—' }}</strong> 待提取</span>
        </div>
      </div>
      <div class="head-actions">
        <span v-if="status" class="status" :class="{ paused: !status.enabled }">{{ status.enabled ? '采集中' : '已暂停' }}</span>
        <button :disabled="busy || !status" @click="toggle">{{ status?.enabled ? '暂停采集' : '启用采集' }}</button>
        <button class="help-button" aria-label="采集说明" title="采集说明" @click="infoOpen = true"><Info :size="17" /></button>
      </div>
    </header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <section class="list-panel" aria-label="客户留资列表">
      <form class="filters" @submit.prevent="search">
        <div class="filter-main">
          <label class="account-filter"><span class="sr-only">接收账号</span><select v-model="account" aria-label="接收账号"><option value="">全部账号</option><option v-for="a in status?.accounts || []" :key="a.id" :value="a.id">{{ a.nickname || a.id }}</option></select></label>
          <label class="search"><span class="sr-only">搜索</span><input v-model="keyword" aria-label="搜索" maxlength="256" placeholder="搜索手机号 / 备注 / 客户昵称 / 抖音号" /></label>
          <button type="submit" class="primary" :disabled="loading"><Search :size="15" /> 查询</button>
        </div>
        <div class="filter-secondary">
          <div class="date-filters"><span class="date-label">最近留资</span>
            <label><span class="sr-only">最近留资开始日期</span><input v-model="fromDate" aria-label="最近留资开始日期" type="date" /></label>
            <span class="date-separator">至</span>
            <label><span class="sr-only">结束日期</span><input v-model="toDate" aria-label="结束日期" type="date" /></label>
          </div>
          <div class="filter-actions">
            <button type="button" :disabled="busy || !status" @click="scanOpen = true"><RefreshCw :size="14" /> 扫描本地历史</button>
            <button type="button" :disabled="busy || total === 0" @click="exportCsv"><Download :size="14" /> 导出筛选结果</button>
          </div>
        </div>
      </form>
      <div class="table-scroll" :aria-busy="loading" tabindex="0" role="region" aria-label="留资表格，可滚动查看更多记录和字段">
        <table>
          <colgroup><col class="col-phone" /><col class="col-remark" /><col class="col-customer" /><col class="col-account" /><col class="col-date" /><col class="col-count" /><col class="col-actions" /></colgroup>
          <thead><tr><th scope="col" class="pinned-phone">手机号</th><th scope="col">备注</th><th scope="col">客户 / 抖音号</th><th scope="col">接收账号</th><th scope="col">留资时间</th><th scope="col" class="count">次数</th><th scope="col" class="pinned-actions">操作</th></tr></thead>
          <tbody>
            <tr v-for="row in items" :key="row.id">
              <td class="phone selectable pinned-phone">{{ row.phone }}</td>
              <td class="remark selectable"><button class="remark-content" :title="row.remark || ''" :aria-label="row.remark ? `查看完整备注：${row.remark}` : '无备注'" :disabled="!row.remark" @click="showSources(row)">{{ row.remark || '—' }}</button></td>
              <td class="customer"><strong class="truncate" :title="row.nickname">{{ row.nickname || '昵称待获取' }}</strong><small class="selectable truncate" :title="`抖音号：${row.douyin_id || '待获取'}`">{{ row.douyin_id || '抖音号待获取' }}</small></td>
              <td class="account-name"><span class="clamp-two" :title="row.account_name">{{ row.account_name || '账号待获取' }}</span></td>
              <td class="date"><div class="date-line"><span>首次</span><time :datetime="row.first_at">{{ time(row.first_at) }}</time></div><div class="date-line recent"><span>最近</span><time :datetime="row.last_at">{{ time(row.last_at) }}</time></div></td>
              <td class="count">{{ row.count }}</td>
              <td class="pinned-actions"><div class="row-actions"><button @click="showSources(row)">来源</button><button :disabled="busy" @click="copy(row)">复制</button><button @click="openChat(row)">会话</button><button class="danger" :disabled="busy" @click="deleteTarget = row">删除</button></div></td>
            </tr>
            <tr v-if="!items.length"><td colspan="7" class="empty"><Phone :size="30" /><strong>{{ loading ? '正在读取留资记录…' : '暂无匹配的留资记录' }}</strong><span v-if="!loading">客户发来手机号后会自动显示，也可手动扫描本地保留的历史消息。</span></td></tr>
          </tbody>
        </table>
      </div>
      <footer><small>本机时区 · 每页 50 条<span class="scroll-hint"> · 左右滚动查看完整字段</span></small><div><button :disabled="page <= 1 || loading" @click="page--; load()">上一页</button><span>{{ page }} / {{ pages }}</span><button :disabled="page >= pages || loading" @click="page++; load()">下一页</button></div></footer>
    </section>
    <AppModal :open="infoOpen" title="采集说明" @close="infoOpen = false">
      <div class="help-copy"><p>自动提取客户私信文本中的大陆手机号候选，数据仅保存在本机。消息中出现号码，不代表号码归属已验证。</p><p>同一接收账号、同一客户、同一号码合并为一条记录。手机号前方的文字会记为备注；昵称和抖音号缺失时，会在后台补全。</p><p>未命中回复规则或自动回复暂停时，收到的消息仍可提取。历史扫描只处理本机保留的消息。</p><p>筛选按最近留资的消息时间计算，日期与时间以本机时区显示。点击备注或“来源”可查看完整内容。</p></div>
      <div class="modal-actions"><button class="primary" @click="infoOpen = false">知道了</button></div>
    </AppModal>
    <AppModal :open="scanOpen" title="扫描本地历史消息" @close="!busy && (scanOpen = false)">
      <p>扫描{{ account ? '所选接收账号' : '全部接收账号' }}本机仍保留的私信文本，不向抖音请求历史消息，不触发自动回复。</p>
      <p>日期与关键词筛选不影响扫描范围。重复来源不会重复计数；已清理的聊天记录不包含在扫描中。</p>
      <div class="modal-actions"><button :disabled="busy" @click="scanOpen = false">取消</button><button class="primary" :disabled="busy" @click="scan">{{ busy ? '提交中…' : '开始扫描' }}</button></div>
    </AppModal>
    <AppModal :open="!!deleteTarget" title="删除这条留资记录？" @close="!busy && (deleteTarget = null)">
      <p>将删除 {{ deleteTarget?.phone }} 在此客户下的全部留资来源，不删除原聊天消息。</p><p>历史重扫不会恢复此记录；客户未来再次发送号码时可重新记录。</p>
      <div class="modal-actions"><button :disabled="busy" @click="deleteTarget = null">取消</button><button class="danger" :disabled="busy" @click="remove">确认删除</button></div>
    </AppModal>
    <AppModal :open="!!sourceTarget" :title="`留资来源 · ${sourceTarget?.phone || ''}`" @close="closeSources">
      <p v-if="sourceLoading">正在读取来源…</p><p v-if="sourceError" class="error">{{ sourceError }}</p>
      <div class="sources"><article v-for="source in sources" :key="source.message_id"><p class="selectable source-text">{{ source.content }}</p><small v-if="source.remark" class="selectable">备注：{{ source.remark }}</small><small>消息时间：{{ time(source.message_at) }}</small><small>提取时间：{{ time(source.extracted_at) }}</small></article></div>
      <div v-if="sourceTarget" class="modal-actions"><button :disabled="sourcePage <= 1 || sourceLoading" @click="showSources(sourceTarget, sourcePage - 1)">上一页</button><span>{{ sourcePage }} / {{ Math.max(1, Math.ceil(sourceTotal / 50)) }}</span><button :disabled="sourcePage * 50 >= sourceTotal || sourceLoading" @click="showSources(sourceTarget, sourcePage + 1)">下一页</button></div>
    </AppModal>
  </div>
</template>

<style scoped>
/* This route is a fixed-height workbench. Prevent keyboard focus from scrolling
   the outer shell when its navigation is taller than a small window. */
:global(.app-layout:has(.leads-page)) { overflow:clip; }
:global(.app-layout:has(.leads-page) .nav-links) { min-height:0; overflow-y:auto; }
:global(.app-layout:has(.leads-page) .nav-item) { flex-shrink:0; }
.scroll-hint { display:none; }
@media(max-width:1160px) { .scroll-hint { display:inline; } }

.leads-page { height:100%; min-height:0; display:flex; flex-direction:column; gap:12px; padding:18px; color:var(--text-primary); }
.page-head,.heading-group,.head-actions,.inline-stats,.filter-main,.filter-secondary,.date-filters,.filter-actions,footer,footer>div,.modal-actions { display:flex; align-items:center; }
.page-head,footer,.filter-secondary { justify-content:space-between; }
.page-head { flex:none; gap:12px; min-height:34px; }
.heading-group { gap:18px; min-width:0; } h1 { display:flex; align-items:center; gap:8px; font-size:20px; line-height:1.4; margin:0; white-space:nowrap; }
.inline-stats { gap:12px; color:var(--text-secondary); font-size:12px; white-space:nowrap; } .inline-stats strong { color:var(--text-primary); font-size:14px; font-weight:600; font-variant-numeric:tabular-nums; } .pending { border-left:1px solid var(--border-strong); padding-left:12px; }
.head-actions { gap:8px; } button { display:inline-flex; align-items:center; justify-content:center; gap:5px; min-height:32px; border:1px solid var(--border-subtle); border-radius:7px; background:white; color:var(--text-secondary); padding:5px 10px; cursor:pointer; white-space:nowrap; font:inherit; font-size:12px; }
button:hover:not(:disabled) { background:var(--bg-app); border-color:var(--border-strong); } button:disabled { opacity:.45; cursor:not-allowed; }
button.primary { background:var(--brand-primary); border-color:var(--brand-primary); color:white; } button.primary:hover:not(:disabled) { background:var(--brand-primary-hover); }
button.danger { color:var(--danger); } .help-button { width:32px; padding:0; background:transparent; } .status { color:var(--success); background:var(--success-soft); padding:4px 9px; border-radius:20px; font-size:12px; white-space:nowrap; } .status.paused { color:var(--text-secondary); background:var(--border-subtle); }
.list-panel { flex:1; min-height:0; display:flex; flex-direction:column; background:white; border:1px solid var(--border-subtle); border-radius:10px; overflow:hidden; }
.filters { flex:none; display:flex; flex-direction:column; gap:8px; padding:12px; border-bottom:1px solid var(--border-subtle); }
.filter-main { gap:8px; } .account-filter { width:152px; flex:none; } label { display:block; min-width:0; } label.search { flex:1; }
input,select { width:100%; height:32px; min-width:0; padding:5px 9px; border:1px solid var(--border-strong); border-radius:7px; background:white; font:inherit; font-size:12px; color:var(--text-primary); }
input:focus,select:focus,button:focus-visible,.table-scroll:focus-visible { outline:2px solid var(--brand-primary); outline-offset:2px; }
.filter-secondary { gap:12px; } .date-filters { gap:8px; } .date-filters label { width:128px; } .date-label,.date-separator { font-size:12px; color:var(--text-secondary); white-space:nowrap; } .filter-actions { gap:8px; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.table-scroll { flex:1; min-height:0; overflow:auto; overscroll-behavior:contain; position:relative; }
table { width:100%; min-width:840px; table-layout:fixed; border-spacing:0; border-collapse:separate; font-size:12px; }
.col-phone { width:116px; } .col-remark { width:auto; } .col-customer { width:142px; } .col-account { width:124px; } .col-date { width:177px; } .col-count { width:42px; } .col-actions { width:136px; }
th { position:sticky; top:0; z-index:2; text-align:left; background:var(--bg-app); height:36px; padding:8px 10px; color:var(--text-secondary); font-size:12px; font-weight:500; white-space:nowrap; border-bottom:1px solid var(--border-subtle); }
td { padding:8px 10px; height:54px; border-bottom:1px solid var(--border-subtle); background:var(--bg-card); vertical-align:middle; line-height:18px; } tbody tr:hover td { background:#fafbfc; }
.pinned-phone { position:sticky; left:0; z-index:1; border-right:1px solid var(--border-subtle); } .pinned-actions { position:sticky; right:0; z-index:1; border-left:1px solid var(--border-subtle); } th.pinned-phone,th.pinned-actions { z-index:3; }
.phone { font-weight:600; font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap; } .customer strong { display:block; font-weight:500; } small { display:block; color:var(--text-secondary); font-size:12px; line-height:18px; }
.truncate { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; } .clamp-two,.remark-content { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; overflow-wrap:anywhere; }
button.remark-content { padding:0; min-height:0; border:0; border-radius:0; white-space:normal; text-align:left; line-height:18px; background:transparent; color:var(--text-secondary); width:100%; } button.remark-content:hover:not(:disabled) { color:var(--brand-primary); background:transparent; text-decoration:underline; } button.remark-content:disabled { opacity:1; color:var(--text-muted); cursor:default; }
.date { font-variant-numeric:tabular-nums; } .date-line { display:flex; align-items:center; gap:6px; font-size:12px; white-space:nowrap; color:var(--text-secondary); } .date-line>span { color:var(--text-muted); font-size:11px; } .date-line.recent time { color:var(--text-primary); }
.count { text-align:center; font-variant-numeric:tabular-nums; } .row-actions { display:flex; gap:8px; align-items:center; } .row-actions button { padding:3px 0; min-height:28px; border:0; border-radius:3px; color:var(--brand-primary); background:none; } .row-actions button.danger { color:var(--danger); }
td.empty { text-align:center; height:220px; color:var(--text-muted); } .empty svg { display:block; margin:0 auto 12px; } .empty strong,.empty span { display:block; margin:8px; font-weight:400; }
footer { flex:none; min-height:44px; gap:12px; padding:6px 12px; border-top:1px solid var(--border-subtle); background:white; } footer>div { gap:12px; } footer span { font-size:12px; } footer button { min-height:30px; }
.error,.notice { flex:none; margin:0; padding:8px 12px; border-radius:7px; font-size:12px; line-height:18px; } .error { color:var(--danger); background:var(--danger-soft); } .notice { color:var(--success); background:var(--success-soft); }
.modal-actions { justify-content:flex-end; gap:12px; margin-top:20px; } .sources { max-height:45vh; overflow:auto; } .sources article { padding:12px 0; border-bottom:1px solid var(--border-subtle); } .source-text { white-space:pre-wrap; overflow-wrap:anywhere; margin:0 0 10px; } .help-copy p { font-size:13px; line-height:1.7; margin:0 0 12px; }
@media(max-width:1050px) { .leads-page { padding:12px; gap:10px; } .heading-group { gap:12px; } .inline-stats { gap:8px; } .pending { padding-left:8px; } .filters { padding:10px; } .date-filters label { width:116px; } .date-filters { gap:6px; } .filter-actions { gap:6px; } .filter-actions button { padding:5px 8px; } }
@media(max-width:800px) { .page-head,.filter-secondary { flex-wrap:wrap; } .heading-group { flex-wrap:wrap; } .filter-actions { margin-left:auto; } .account-filter { width:120px; } footer small { max-width:140px; } }
</style>
