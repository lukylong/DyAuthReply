<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  adminEmergencyStop,
  adminLogout,
  clearAdminToken,
  getAdminDashboard,
  isAdminLoggedIn,
  listRuntimeLogFiles,
  tailRuntimeLogs,
  type AdminDashboard,
  type RuntimeLogFile,
} from '../api/client';

const router = useRouter();
const tab = ref<'overview' | 'process' | 'logs' | 'database' | 'stop'>('overview');
const loading = ref(true);
const error = ref('');
const dashboard = ref<AdminDashboard | null>(null);

const logContent = ref('');
const logHint = ref('');
const logFiles = ref<RuntimeLogFile[]>([]);
const selectedLogFile = ref('');
const logLoading = ref(false);
const autoRefresh = ref(true);
const stopping = ref(false);
const stopResult = ref('');

let refreshTimer: ReturnType<typeof setInterval> | null = null;

const accounts = computed(() => dashboard.value?.service.accounts ?? []);
const sessions = computed(() => dashboard.value?.service.sessions ?? []);
const db = computed(() => dashboard.value?.database ?? {});
const processes = computed(() => dashboard.value?.processes ?? null);

async function loadDashboard() {
  if (!isAdminLoggedIn()) {
    router.replace('/home');
    return;
  }
  try {
    dashboard.value = await getAdminDashboard();
    error.value = '';
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    if (String(e).includes('401') || String(e).includes('管理员')) {
      clearAdminToken();
      router.replace('/home');
    }
  } finally {
    loading.value = false;
  }
}

async function loadLogs() {
  logLoading.value = true;
  try {
    const res = await tailRuntimeLogs({
      lines: 500,
      file: selectedLogFile.value || undefined,
    });
    logContent.value = res.content || res.message || '（空）';
    logHint.value = res.message || '';
  } catch (e) {
    logContent.value = e instanceof Error ? e.message : String(e);
  } finally {
    logLoading.value = false;
  }
}

async function loadLogFiles() {
  try {
    const res = await listRuntimeLogFiles();
    logFiles.value = res.items ?? [];
  } catch {
    logFiles.value = [];
  }
}

async function onEmergencyStop() {
  if (!window.confirm('确认急停？将关闭所有账号自动回复、暂停会话并清空待发命令。')) return;
  stopping.value = true;
  stopResult.value = '';
  try {
    const res = await adminEmergencyStop('管理员手动急停');
    stopResult.value = `已急停：${res.accounts_stopped} 个账号，清空 ${res.commands_cleared} 条命令，标记 ${res.messages_marked_processed} 条消息`;
    await loadDashboard();
  } catch (e) {
    stopResult.value = e instanceof Error ? e.message : String(e);
  } finally {
    stopping.value = false;
  }
}

async function onLogout() {
  try {
    await adminLogout();
  } catch {
    // ignore
  }
  clearAdminToken();
  router.replace('/home');
}

function goBack() {
  router.back();
}

onMounted(async () => {
  if (!isAdminLoggedIn()) {
    router.replace('/home');
    return;
  }
  await loadDashboard();
  await loadLogFiles();
  await loadLogs();
  refreshTimer = setInterval(async () => {
    if (!autoRefresh.value) return;
    await loadDashboard();
    if (tab.value === 'logs') await loadLogs();
  }, 5000);
});

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});
</script>

<template>
  <div class="admin-page">
    <header class="head">
      <div>
        <button type="button" class="btn-back" @click="goBack">← 返回</button>
        <h2>管理员控制台</h2>
        <p class="sub">进程 / 日志 / 数据库监控 · 急停</p>
      </div>
      <div class="head-actions">
        <label class="chk"><input v-model="autoRefresh" type="checkbox" />自动刷新</label>
        <button type="button" class="btn ghost" @click="loadDashboard">刷新</button>
        <button type="button" class="btn ghost" @click="onLogout">退出管理</button>
      </div>
    </header>

    <nav class="tabs">
      <button :class="{ active: tab === 'overview' }" @click="tab = 'overview'">概览</button>
      <button :class="{ active: tab === 'stop' }" @click="tab = 'stop'">急停</button>
      <button :class="{ active: tab === 'process' }" @click="tab = 'process'">进程</button>
      <button :class="{ active: tab === 'logs' }" @click="tab = 'logs'">日志</button>
      <button :class="{ active: tab === 'database' }" @click="tab = 'database'">数据库</button>
    </nav>

    <p v-if="error" class="banner error">{{ error }}</p>
    <p v-if="loading" class="banner">加载中...</p>

    <section v-if="tab === 'overview' && dashboard" class="panel">
      <div class="cards">
        <div class="card">
          <span class="label">在线账号</span>
          <strong>{{ dashboard.service.accounts_online }} / {{ dashboard.service.accounts_total }}</strong>
        </div>
        <div class="card">
          <span class="label">自动回复开启</span>
          <strong>{{ dashboard.service.accounts_auto_reply_on }}</strong>
        </div>
        <div class="card">
          <span class="label">待发命令</span>
          <strong>{{ db.pending_worker_commands ?? 0 }}</strong>
        </div>
        <div class="card">
          <span class="label">未处理入向消息</span>
          <strong>{{ db.unprocessed_inbound_messages ?? 0 }}</strong>
        </div>
      </div>
      <h3>账号状态</h3>
      <table class="table">
        <thead>
          <tr>
            <th>昵称</th>
            <th>状态</th>
            <th>自动回复</th>
            <th>凭证</th>
            <th>今日回复</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="acc in accounts" :key="String(acc.id)">
            <td>{{ acc.nickname || acc.id }}</td>
            <td>{{ acc.status }}</td>
            <td>{{ acc.auto_reply_enabled ? '开' : '关' }}</td>
            <td>{{ acc.credential_state }}</td>
            <td>{{ acc.reply_today }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="tab === 'stop'" class="panel stop-panel">
      <h3>急停操作</h3>
      <p>立即关闭所有账号自动回复，向 Worker 下发暂停指令，并清空 SQLite 中未消费的命令队列。</p>
      <button type="button" class="btn danger" :disabled="stopping" @click="onEmergencyStop">
        {{ stopping ? '执行中...' : '一键急停' }}
      </button>
      <p v-if="stopResult" class="stop-result">{{ stopResult }}</p>
    </section>

    <section v-if="tab === 'process' && processes" class="panel">
      <h3>API 进程</h3>
      <pre class="mono">{{ JSON.stringify(processes.api, null, 2) }}</pre>
      <h3>系统资源</h3>
      <pre class="mono">{{ JSON.stringify(processes.system, null, 2) }}</pre>
      <h3>相关进程</h3>
      <table class="table">
        <thead>
          <tr><th>PID</th><th>名称</th><th>内存 MB</th><th>状态</th><th>命令行</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in processes.related_processes" :key="String(p.pid)">
            <td>{{ p.pid }}</td>
            <td>{{ p.name }}</td>
            <td>{{ p.memory_mb }}</td>
            <td>{{ p.status }}</td>
            <td class="cmd">{{ p.cmdline }}</td>
          </tr>
        </tbody>
      </table>
      <h3>Worker 会话</h3>
      <pre class="mono">{{ JSON.stringify(sessions, null, 2) }}</pre>
    </section>

    <section v-if="tab === 'logs'" class="panel logs-panel">
      <div class="toolbar">
        <select v-model="selectedLogFile" @change="loadLogs">
          <option value="">全部日志</option>
          <option v-for="f in logFiles" :key="f.name" :value="f.name">
            {{ f.name }} ({{ Math.round(f.size / 1024) }} KB)
          </option>
        </select>
        <button type="button" class="btn ghost" :disabled="logLoading" @click="loadLogs">刷新日志</button>
      </div>
      <p v-if="logHint" class="hint">{{ logHint }}</p>
      <pre class="log-box">{{ logContent || '暂无日志' }}</pre>
    </section>

    <section v-if="tab === 'database'" class="panel">
      <h3>SQLite 概览</h3>
      <pre class="mono">{{ JSON.stringify(db, null, 2) }}</pre>
    </section>
  </div>
</template>

<style scoped>
.admin-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 80px);
}

.head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.head h2 { margin: 8px 0 4px; }
.sub { margin: 0; color: var(--text-muted); font-size: 0.85rem; }
.btn-back { border: none; background: transparent; color: var(--text-secondary); cursor: pointer; padding: 0; }

.head-actions, .toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tabs button {
  border: 1px solid rgba(0,0,0,0.08);
  background: rgba(255,255,255,0.6);
  border-radius: 999px;
  padding: 8px 14px;
  cursor: pointer;
}

.tabs button.active {
  background: #2563eb;
  color: #fff;
  border-color: #2563eb;
}

.panel {
  background: rgba(255,255,255,0.55);
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: 16px;
  padding: 16px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.card {
  background: rgba(255,255,255,0.8);
  border-radius: 12px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.card .label { color: var(--text-muted); font-size: 0.8rem; }
.card strong { font-size: 1.2rem; }

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.table th, .table td {
  border-bottom: 1px solid rgba(0,0,0,0.06);
  padding: 8px;
  text-align: left;
  vertical-align: top;
}

.table .cmd {
  max-width: 360px;
  word-break: break-all;
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
}

.mono {
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 10px;
  overflow: auto;
  font-size: 12px;
}

.log-box {
  background: #0f172a;
  color: #e2e8f0;
  padding: 16px;
  border-radius: 12px;
  min-height: 360px;
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
}

.btn {
  border: none;
  border-radius: 10px;
  padding: 10px 14px;
  cursor: pointer;
  font-weight: 600;
}

.btn.ghost { background: rgba(0,0,0,0.05); }
.btn.danger { background: #dc2626; color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }

.chk { font-size: 0.85rem; color: var(--text-secondary); display: flex; gap: 6px; align-items: center; }

.banner { margin: 0; padding: 10px 12px; border-radius: 10px; background: rgba(0,0,0,0.04); }
.banner.error { background: #fef2f2; color: #b91c1c; }

.stop-panel p { color: var(--text-secondary); line-height: 1.6; }
.stop-result { margin-top: 12px; color: #15803d; }
.hint { color: var(--text-muted); font-size: 0.85rem; }
</style>
