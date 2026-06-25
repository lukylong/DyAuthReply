<script setup lang="ts">
import { onBeforeRouteLeave } from 'vue-router';
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import AppModal from '../components/AppModal.vue';
import AccountProfileDrawer from '../components/AccountProfileDrawer.vue';
import {
  credentialLabel,
  deleteAccount,
  importCredential,
  listAccounts,
  patchAccount,
  quickCreateAccount,
  statusLabel,
  type DouyinAccount,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const loading = ref(true);
const error = ref('');
const accounts = ref<DouyinAccount[]>([]);
const { licenseStatus: license, ensureStatus } = useClientLicense();
const showImport = ref(false);
const bundle = ref('');
const submitting = ref(false);
const importError = ref('');
const importSuccess = ref('');
const reimportTarget = ref<DouyinAccount | null>(null);
const savingId = ref('');

const showProfile = ref(false);
const profileTarget = ref<DouyinAccount | null>(null);

function openProfile(account: DouyinAccount) {
  profileTarget.value = account;
  showProfile.value = true;
}

function closeProfile() {
  showProfile.value = false;
}

const showDelete = ref(false);
const deleteTarget = ref<DouyinAccount | null>(null);
const deleting = ref(false);
const deleteError = ref('');

function openDelete(account: DouyinAccount) {
  deleteTarget.value = account;
  deleteError.value = '';
  showDelete.value = true;
}

function closeDelete() {
  if (deleting.value) return;
  showDelete.value = false;
  deleteTarget.value = null;
}

async function confirmDelete() {
  const acc = deleteTarget.value;
  if (!acc) return;
  deleting.value = true;
  deleteError.value = '';
  try {
    await deleteAccount(acc.id);
    accounts.value = accounts.value.filter((a) => a.id !== acc.id);
    showDelete.value = false;
    deleteTarget.value = null;
  } catch (e) {
    deleteError.value = e instanceof Error ? e.message : String(e);
  } finally {
    deleting.value = false;
  }
}

const RECOMMENDED_MAX_ACCOUNTS = 10;
const overAccountLimit = computed(
  () => accounts.value.length > RECOMMENDED_MAX_ACCOUNTS,
);

async function load() {
  loading.value = true;
  error.value = '';
  try {
    await ensureStatus();
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
  if (e.key !== 'Escape') return;
  if (showDelete.value) closeDelete();
  else if (showProfile.value) closeProfile();
  else if (showImport.value) closeImport();
}

watch([showImport, showProfile, showDelete], ([imp, prof, del]) => {
  if (imp || prof || del) {
    document.addEventListener('keydown', onEscapeKey);
  } else {
    document.removeEventListener('keydown', onEscapeKey);
  }
});

onBeforeRouteLeave(() => {
  closeImport();
  closeProfile();
  showDelete.value = false;
});

onUnmounted(() => {
  document.removeEventListener('keydown', onEscapeKey);
});

async function submitImport() {
  if (!license.value?.can_use_business) {
    importError.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法导入账号`;
    return;
  }
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
      importSuccess.value = res.message || '登录凭证已成功更新';
    } else {
      const acc = await quickCreateAccount({ bundle: text, auto_reply_enabled: false });
      importSuccess.value = `已成功接入账号：${acc.nickname}`;
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
  if (!license.value?.can_use_business) {
    error.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法切换自动回复`;
    await load();
    return;
  }
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
  if (!license.value?.can_use_business) {
    error.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法修改配额`;
    return;
  }
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

function avatarInitial(name: string) {
  const t = name.trim();
  if (!t) return '?';
  return t.slice(0, 1).toUpperCase();
}

onMounted(load);
</script>

<template>
  <div class="accounts-page">
    <div class="head">
      <div>
        <h2>我的抖音号</h2>
        <p class="sub">通过浏览器凭证提取插件获取一键导入串，在此粘贴并托管抖音私信消息。</p>
        <p v-if="license && !license.can_use_business" class="license-tip">
          当前授权状态为「{{ license.state_label }}」，账号导入、凭证更新与自动回复托管已限制。
        </p>
        <p v-if="overAccountLimit" class="license-tip warn-limit">
          已托管 {{ accounts.length }} 个账号，超过单机建议上限（{{ RECOMMENDED_MAX_ACCOUNTS }} 个）。账号过多会增加内存占用、回复延迟与风控关联风险，建议分散到多台设备托管。
        </p>
      </div>
      <button type="button" class="btn-glass btn-primary-glass" @click="openImport()">
        <span>+</span> 导入抖音号
      </button>
    </div>

    <div v-if="loading" class="loading-state glass-panel">
      <div class="dot-spinner"></div>
      <p>正在同步账号凭据，请稍候...</p>
    </div>
    
    <div v-else-if="error" class="card error glass-panel">
      <span class="icon">⚠️</span>
      <div class="err-text">
        <h4>获取数据失败</h4>
        <p>{{ error }}</p>
      </div>
    </div>
    
    <div v-else-if="accounts.length === 0" class="empty-state glass-panel">
      <div class="empty-icon">📱</div>
      <h3>暂无绑定的抖音号</h3>
      <p>导入您的第一个抖音号来配置私信的自动回复任务</p>
      <button type="button" class="btn-glass btn-primary-glass mt-16" @click="openImport()">
        立即导入首个账号
      </button>
    </div>

    <section v-else class="accounts-grid">
      <article v-for="acc in accounts" :key="acc.id" class="account-card glass-panel" :class="{ disabled: !acc.auto_reply_enabled }">
        <div class="account-header">
          <div class="profile-group">
            <div class="avatar md">
              <img v-if="acc.avatar" :src="acc.avatar" alt="" />
              <span v-else>{{ avatarInitial(acc.nickname) }}</span>
            </div>
            <div class="name-status">
              <strong>{{ acc.nickname }}</strong>
              <div class="tags-row">
                <span class="badge" :class="{ success: acc.status === 1, warn: acc.status !== 1 }">
                  {{ statusLabel(acc.status) }}
                </span>
                <span class="badge" :class="{ success: acc.credential_state === 'sendable', danger: acc.credential_state === 'invalid' }">
                  {{ credentialLabel(acc.credential_state) }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div class="quota-setting">
          <div class="quota-info">
            <span class="lbl">今日已回复</span>
            <span class="val">{{ acc.reply_today ?? 0 }} 次</span>
          </div>
          <div class="quota-input-wrap">
            <span class="lbl">日回复限额</span>
            <input
              class="input-glass quota-input"
              type="number"
              min="0"
              :value="acc.daily_reply_quota ?? 200"
              :disabled="savingId === acc.id || !!(license && !license.can_use_business)"
              @change="saveQuota(acc, $event)"
            />
          </div>
        </div>

        <div class="account-actions">
          <label class="ios-switch">
            <input
              type="checkbox"
              :checked="acc.auto_reply_enabled"
              :disabled="savingId === acc.id || !!(license && !license.can_use_business)"
              @change="toggleAutoReply(acc, $event)"
            />
            <span class="slider"></span>
            <span class="switch-lbl">自动回复</span>
          </label>
          <div class="action-btns">
            <button type="button" class="btn-glass btn-action" @click="openProfile(acc)">
              查看主页
            </button>
            <button type="button" class="btn-glass btn-action" @click="openImport(acc)">
              更新凭证
            </button>
            <button
              type="button"
              class="btn-glass btn-icon btn-delete"
              title="删除账号"
              aria-label="删除账号"
              @click="openDelete(acc)"
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M3 6h18" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <line x1="10" y1="11" x2="10" y2="17" />
                <line x1="14" y1="11" x2="14" y2="17" />
              </svg>
            </button>
          </div>
        </div>
      </article>
    </section>

    <!-- AppModal for Import -->
    <AppModal
      :open="showImport"
      :title="reimportTarget ? `更新「${reimportTarget.nickname}」的凭证` : '绑定抖音账号'"
      @close="closeImport"
    >
      <div class="import-modal-content">
        <div class="tips-box">
          <p>请在一键导入扩展中复制最新的凭据，并粘贴在下方文本框中。</p>
          <p class="small">凭据串应该以 <code>DYCRED1.</code> 作为开头。</p>
        </div>
        <textarea
          class="input-glass bundle-textarea"
          v-model="bundle"
          rows="7"
          placeholder="在此处粘贴 DYCRED1. 开头的一键导入串..."
          spellcheck="false"
        />
        <transition name="fade">
          <p v-if="importError" class="msg-box error-msg">{{ importError }}</p>
        </transition>
        <transition name="fade">
          <p v-if="importSuccess" class="msg-box success-msg">{{ importSuccess }}</p>
        </transition>
        <div class="actions">
          <button type="button" class="btn-glass" :disabled="submitting" @click="closeImport">
            取消
          </button>
          <button type="button" class="btn-glass btn-primary-glass" :disabled="submitting" @click="submitImport">
            {{ submitting ? '验证导入中...' : '确认绑定' }}
          </button>
        </div>
      </div>
    </AppModal>

    <AccountProfileDrawer
      :open="showProfile"
      :account="profileTarget"
      @close="closeProfile"
    />

    <AppModal
      :open="showDelete"
      :title="`删除「${deleteTarget?.nickname ?? ''}」`"
      dialog-role="alertdialog"
      @close="closeDelete"
    >
      <div class="delete-modal-content">
        <p class="del-desc">
          确认删除该抖音号吗？删除后将停止托管、清除其本地登录凭证与托管配置，此操作不可恢复。
        </p>
        <p v-if="deleteTarget?.auto_reply_enabled" class="msg-box warn-msg">
          该账号自动回复仍处于开启状态，请先在卡片上关闭「自动回复」后再删除。
        </p>
        <transition name="fade">
          <p v-if="deleteError" class="msg-box error-msg">{{ deleteError }}</p>
        </transition>
        <div class="actions">
          <button type="button" class="btn-glass" :disabled="deleting" @click="closeDelete">
            取消
          </button>
          <button
            type="button"
            class="btn-glass btn-danger-glass"
            :disabled="deleting || deleteTarget?.auto_reply_enabled"
            @click="confirmDelete"
          >
            {{ deleting ? '删除中...' : '确认删除' }}
          </button>
        </div>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
.accounts-page {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
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

.license-tip {
  margin: 8px 0 0;
  color: #b45309;
  font-size: 0.88rem;
}

.license-tip.warn-limit {
  color: #c2410c;
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

.mt-16 {
  margin-top: 16px;
}

.card.error {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 16px 20px;
  border-color: rgba(239, 68, 68, 0.2);
  background: rgba(239, 68, 68, 0.03);
}

.card.error .icon {
  font-size: 1.5rem;
}

.err-text h4 {
  margin: 0 0 4px;
  color: #ef4444;
}

.err-text p {
  margin: 0;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

/* Accounts Grid Layout */
.accounts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 20px;
}

.account-card {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  transition: var(--transition-smooth);
}

.account-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.05);
}

.account-card.disabled {
  opacity: 0.85;
}

.profile-group {
  display: flex;
  gap: 16px;
  align-items: center;
  min-width: 0;
}

.avatar {
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, rgba(0, 0, 0, 0.05), rgba(0, 0, 0, 0.01));
  border: 1px solid rgba(0, 0, 0, 0.08);
  color: var(--text-primary);
  font-weight: 700;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.avatar.md {
  width: 50px;
  height: 50px;
  font-size: 1.1rem;
}

.name-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.name-status strong {
  font-size: 1rem;
  color: var(--text-primary);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tags-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.badge {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 99px;
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid rgba(0, 0, 0, 0.06);
  color: var(--text-muted);
}
.badge.success {
  background: rgba(34, 197, 94, 0.1);
  border-color: rgba(34, 197, 94, 0.2);
  color: #16803d;
}
.badge.warn {
  background: rgba(234, 179, 8, 0.1);
  border-color: rgba(234, 179, 8, 0.2);
  color: #a16207;
}
.badge.danger {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.2);
  color: #b91c1c;
}

/* Quota parameters styling */
.quota-setting {
  display: grid;
  grid-template-columns: 1fr 1fr;
  background: rgba(255, 255, 255, 0.25);
  padding: 12px 16px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.03);
  gap: 16px;
}

.quota-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.quota-info .lbl, .quota-input-wrap .lbl {
  font-size: 0.72rem;
  color: var(--text-muted);
  font-weight: 600;
}

.quota-info .val {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-primary);
}

.quota-input-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.quota-input {
  width: 100%;
  padding: 4px 10px;
  border-radius: 8px;
  font-size: 0.85rem;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(0, 0, 0, 0.08);
  color: var(--text-primary);
}

/* Card Actions Panel */
.account-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 4px;
}

.action-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-action {
  padding: 8px 14px;
  font-size: 0.8rem;
  border-radius: 10px;
}

.btn-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border-radius: 10px;
}

.btn-icon svg {
  display: block;
}

.btn-delete {
  color: var(--text-muted);
}
.btn-delete:hover {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.25);
  color: #b91c1c;
}

/* iOS Toggle Switch */
.ios-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  user-select: none;
}

.ios-switch input {
  opacity: 0;
  width: 0;
  height: 0;
  position: absolute;
}

.ios-switch .slider {
  position: relative;
  width: 44px;
  height: 24px;
  background: rgba(0, 0, 0, 0.08);
  border: 1px solid rgba(0, 0, 0, 0.05);
  border-radius: 99px;
  transition: var(--transition-quick);
}

.ios-switch .slider:before {
  content: "";
  position: absolute;
  height: 18px;
  width: 18px;
  left: 2px;
  bottom: 2px;
  background-color: #fff;
  border-radius: 50%;
  transition: var(--transition-quick);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.15);
}

.ios-switch input:checked + .slider {
  background: #22c55e;
  border-color: rgba(34, 197, 94, 0.1);
}

.ios-switch input:checked + .slider:before {
  transform: translateX(20px);
  background-color: #fff;
}

.ios-switch .switch-lbl {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-secondary);
}

.ios-switch input:checked ~ .switch-lbl {
  color: var(--text-primary);
}

/* Modal Content Panel */
.import-modal-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tips-box {
  background: rgba(255, 255, 255, 0.4);
  border-radius: 12px;
  padding: 12px 16px;
  border-left: 3px solid var(--accent-indigo);
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.5;
  border-top: 1px solid rgba(255, 255, 255, 0.3);
  border-right: 1px solid rgba(255, 255, 255, 0.3);
  border-bottom: 1px solid rgba(255, 255, 255, 0.3);
}

.tips-box p {
  margin: 0;
}
.tips-box p.small {
  margin-top: 4px;
  font-size: 0.75rem;
  color: var(--text-muted);
}

.bundle-textarea {
  width: 100%;
  resize: vertical;
  min-height: 120px;
  font-family: monospace;
  font-size: 0.8rem;
  line-height: 1.5;
}

.msg-box {
  margin: 0;
  font-size: 0.8rem;
  padding: 8px 12px;
  border-radius: 8px;
}

.error-msg {
  background: rgba(239, 68, 68, 0.06);
  color: #ef4444;
  border: 1px solid rgba(239, 68, 68, 0.15);
}

.success-msg {
  background: rgba(34, 197, 94, 0.06);
  color: #15803d;
  border: 1px solid rgba(34, 197, 94, 0.15);
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}

.delete-modal-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.del-desc {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.6;
  color: var(--text-secondary);
}

.warn-msg {
  background: rgba(234, 179, 8, 0.08);
  color: #a16207;
  border: 1px solid rgba(234, 179, 8, 0.2);
}

.btn-danger-glass {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.25);
  color: #b91c1c;
}
.btn-danger-glass:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.2);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
