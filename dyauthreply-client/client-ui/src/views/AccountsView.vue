<script setup lang="ts">
import { onBeforeRouteLeave } from 'vue-router';
import { onMounted, onUnmounted, ref, watch } from 'vue';
import AppModal from '../components/AppModal.vue';
import {
  credentialLabel,
  importCredential,
  listAccounts,
  patchAccount,
  quickCreateAccount,
  statusLabel,
  type DouyinAccount,
} from '../api/client';

const loading = ref(true);
const error = ref('');
const accounts = ref<DouyinAccount[]>([]);
const showImport = ref(false);
const bundle = ref('');
const submitting = ref(false);
const importError = ref('');
const importSuccess = ref('');
const reimportTarget = ref<DouyinAccount | null>(null);
const savingId = ref('');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    accounts.value = await listAccounts();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

function openImport(account?: DouyinAccount) {
  reimportTarget.value = account ?? null;
  bundle.value = '';
  importError.value = '';
  importSuccess.value = '';
  showImport.value = true;
}

function closeImport() {
  showImport.value = false;
  reimportTarget.value = null;
}

function onEscapeKey(e: KeyboardEvent) {
  if (e.key === 'Escape') closeImport();
}

watch(showImport, (open) => {
  if (open) {
    document.addEventListener('keydown', onEscapeKey);
  } else {
    document.removeEventListener('keydown', onEscapeKey);
  }
});

onBeforeRouteLeave(() => {
  closeImport();
});

onUnmounted(() => {
  document.removeEventListener('keydown', onEscapeKey);
});

async function submitImport() {
  const text = bundle.value.trim();
  if (!text) {
    importError.value = '请粘贴 DYCRED1 开头的一键导入串';
    return;
  }
  submitting.value = true;
  importError.value = '';
  importSuccess.value = '';
  try {
    if (reimportTarget.value) {
      const res = await importCredential(reimportTarget.value.id, { bundle: text });
      importSuccess.value = res.message || '登录态已更新';
    } else {
      const acc = await quickCreateAccount({ bundle: text, auto_reply_enabled: false });
      importSuccess.value = `已添加账号：${acc.nickname}`;
    }
    await load();
    setTimeout(closeImport, 800);
  } catch (e) {
    importError.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}

async function toggleAutoReply(acc: DouyinAccount, event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  savingId.value = acc.id;
  try {
    const updated = await patchAccount(acc.id, { auto_reply_enabled: checked });
    acc.auto_reply_enabled = updated.auto_reply_enabled;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    await load();
  } finally {
    savingId.value = '';
  }
}

async function saveQuota(acc: DouyinAccount, event: Event) {
  const input = event.target as HTMLInputElement;
  const quota = Number(input.value);
  if (!Number.isFinite(quota) || quota < 0) return;
  savingId.value = acc.id;
  try {
    const updated = await patchAccount(acc.id, { daily_reply_quota: quota });
    acc.daily_reply_quota = updated.daily_reply_quota;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    savingId.value = '';
  }
}

onMounted(load);
</script>

<template>
  <div>
    <div class="head">
      <div>
        <h2>我的抖音号</h2>
        <p class="sub">粘贴浏览器扩展生成的一键导入串即可添加或更新登录态</p>
      </div>
      <button type="button" class="btn primary" @click="openImport()">+ 导入账号</button>
    </div>

    <section v-if="loading" class="card">加载中…</section>
    <section v-else-if="error" class="card error">{{ error }}</section>
    <section v-else-if="accounts.length === 0" class="card empty">
      <p>还没有抖音账号</p>
      <button type="button" class="btn primary" @click="openImport()">导入第一个账号</button>
    </section>
    <section v-else class="list">
      <article v-for="acc in accounts" :key="acc.id" class="card account">
        <div class="info">
          <strong>{{ acc.nickname }}</strong>
          <span class="tag">{{ statusLabel(acc.status) }}</span>
          <span class="tag muted">{{ credentialLabel(acc.credential_state) }}</span>
        </div>
        <div class="meta">
          今日回复 {{ acc.reply_today ?? 0 }} /
          <input
            class="quota-input"
            type="number"
            min="0"
            :value="acc.daily_reply_quota ?? 200"
            :disabled="savingId === acc.id"
            @change="saveQuota(acc, $event)"
          />
        </div>
        <div class="controls">
          <label class="switch">
            <input
              type="checkbox"
              :checked="acc.auto_reply_enabled"
              :disabled="savingId === acc.id"
              @change="toggleAutoReply(acc, $event)"
            />
            自动回复
          </label>
          <button type="button" class="btn ghost" @click="openImport(acc)">更新登录态</button>
        </div>
      </article>
    </section>

    <AppModal
      :open="showImport"
      :title="reimportTarget ? `更新「${reimportTarget.nickname}」` : '导入抖音账号'"
      @close="closeImport"
    >
        <p class="hint">
          在 creator.douyin.com 私信页打开扩展 → 复制一键导入串（DYCRED1. 开头）粘贴 below
        </p>
        <textarea
          v-model="bundle"
          rows="8"
          placeholder="DYCRED1.xxxxx..."
          spellcheck="false"
        />
        <p v-if="importError" class="msg error">{{ importError }}</p>
        <p v-if="importSuccess" class="msg ok">{{ importSuccess }}</p>
        <div class="actions">
          <button type="button" class="btn ghost" :disabled="submitting" @click="closeImport">
            取消
          </button>
          <button type="button" class="btn primary" :disabled="submitting" @click="submitImport">
            {{ submitting ? '导入中…' : '确认导入' }}
          </button>
        </div>
    </AppModal>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
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

.card.error {
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

.card.empty {
  text-align: center;
  padding: 40px 20px;
}

.list {
  display: grid;
  gap: 12px;
}

.account .info {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.tag {
  font-size: 0.75rem;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.15);
  color: #86efac;
}

.tag.muted {
  background: rgba(148, 163, 184, 0.15);
  color: #cbd5e1;
}

.meta {
  color: #94a3b8;
  font-size: 0.85rem;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.quota-input {
  width: 72px;
  border-radius: 6px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  background: #0f172a;
  color: #e2e8f0;
  padding: 4px 6px;
  font: inherit;
}

.controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.88rem;
  color: #cbd5e1;
  cursor: pointer;
}

.btn {
  border: none;
  border-radius: 10px;
  padding: 10px 16px;
  cursor: pointer;
  font-size: 0.92rem;
}

.btn.primary {
  background: linear-gradient(135deg, #fe2c55, #ff6b35);
  color: #fff;
  font-weight: 600;
}

.btn.ghost {
  background: rgba(148, 163, 184, 0.12);
  color: #e2e8f0;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.hint {
  color: #94a3b8;
  font-size: 0.88rem;
  margin: 0 0 12px;
}

textarea {
  width: 100%;
  box-sizing: border-box;
  border-radius: 10px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  font-family: ui-monospace, monospace;
  font-size: 0.8rem;
  resize: vertical;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.msg {
  margin: 10px 0 0;
  font-size: 0.88rem;
}

.msg.error {
  color: #fca5a5;
}

.msg.ok {
  color: #86efac;
}
</style>
