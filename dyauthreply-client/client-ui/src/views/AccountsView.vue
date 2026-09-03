<script setup lang="ts">
import { onBeforeRouteLeave } from 'vue-router';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { Ellipsis, Plus, Search, Smartphone, TriangleAlert } from 'lucide-vue-next';
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
const searchQuery = ref('');

const filteredAccounts = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return accounts.value;
  return accounts.value.filter((a) => a.nickname.toLowerCase().includes(q));
});

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

// ---- 卡片溢出菜单（查看主页 / 更新凭证 / 删除账号）----
const openMenuId = ref('');

function toggleMenu(id: string, event: Event) {
  event.stopPropagation();
  openMenuId.value = openMenuId.value === id ? '' : id;
}

function closeMenu() {
  openMenuId.value = '';
}

function onDocumentClick() {
  closeMenu();
}

function onEscapeKey(e: KeyboardEvent) {
  if (e.key !== 'Escape') return;
  if (openMenuId.value) closeMenu();
  else if (showDelete.value) closeDelete();
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
  closeMenu();
  showDelete.value = false;
});

onMounted(() => {
  document.addEventListener('click', onDocumentClick);
  document.addEventListener('keydown', onEscapeKey);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick);
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
      <div class="head-title">
        <h2>抖音账号</h2>
        <span class="count-badge">{{ accounts.length }} 个账号</span>
      </div>
      <div class="head-actions">
        <div class="search-box">
          <Search class="search-icon" :size="16" />
          <input v-model="searchQuery" type="text" class="input-glass search-input" placeholder="搜索昵称" />
        </div>
        <button type="button" class="btn-glass btn-primary-glass" @click="openImport()">
          <Plus :size="16" /> 导入抖音号
        </button>
      </div>
    </div>
    <p v-if="license && !license.can_use_business" class="license-tip">
      当前授权状态为「{{ license.state_label }}」，账号导入、凭证更新与自动回复托管已限制。
    </p>
    <p v-if="overAccountLimit" class="license-tip warn-limit">
      已托管 {{ accounts.length }} 个账号，超过单机建议上限（{{ RECOMMENDED_MAX_ACCOUNTS }} 个）。账号过多会增加内存占用、回复延迟与风控关联风险，建议分散到多台设备托管。
    </p>

    <div v-if="loading" class="loading-state glass-panel">
      <div class="dot-spinner"></div>
      <p>正在同步账号凭据，请稍候...</p>
    </div>

    <div v-else-if="error" class="card error glass-panel">
      <TriangleAlert class="icon" :size="24" />
      <div class="err-text">
        <h4>获取数据失败</h4>
        <p>{{ error }}</p>
      </div>
    </div>

    <div v-else-if="accounts.length === 0" class="empty-state glass-panel">
      <Smartphone class="empty-icon" :size="40" />
      <h3>暂无绑定的抖音号</h3>
      <p>导入您的第一个抖音号来配置私信的自动回复任务</p>
      <button type="button" class="btn-glass btn-primary-glass mt-16" @click="openImport()">
        立即导入首个账号
      </button>
    </div>

    <div v-else-if="filteredAccounts.length === 0" class="empty-state glass-panel">
      <Search class="empty-icon" :size="40" />
      <h3>没有匹配的账号</h3>
      <p>换个昵称关键词试试</p>
    </div>

    <section v-else class="accounts-grid">
      <article
        v-for="acc in filteredAccounts"
        :key="acc.id"
        class="account-card-sm glass-panel"
        :class="{ disabled: !acc.auto_reply_enabled }"
        @click="openProfile(acc)"
      >
        <div class="card-top">
          <div class="avatar sm">
            <img v-if="acc.avatar" :src="acc.avatar" alt="" />
            <span v-else>{{ avatarInitial(acc.nickname) }}</span>
          </div>
          <span class="status-dot" :class="{ online: acc.status === 1 }" :title="statusLabel(acc.status)"></span>
          <div class="menu-wrap">
            <button
              type="button"
              class="menu-trigger"
              title="更多操作"
              aria-label="更多操作"
              @click.stop="toggleMenu(acc.id, $event)"
            >
              <Ellipsis :size="16" />
            </button>
            <div v-if="openMenuId === acc.id" class="menu-dropdown" @click.stop>
              <button type="button" @click="openProfile(acc); closeMenu()">查看主页</button>
              <button type="button" @click="openImport(acc); closeMenu()">更新凭证</button>
              <button type="button" class="danger" @click="openDelete(acc); closeMenu()">删除账号</button>
            </div>
          </div>
        </div>

        <strong class="nickname" :title="acc.nickname">{{ acc.nickname }}</strong>
        <span class="credential-badge" :class="{ success: acc.credential_state === 'sendable', danger: acc.credential_state === 'invalid' }">
          {{ credentialLabel(acc.credential_state) }}
        </span>

        <div class="card-divider"></div>

        <div class="card-bottom">
          <span class="reply-count">今日回复 {{ acc.reply_today ?? 0 }} 次</span>
          <label class="ios-switch sm" @click.stop>
            <input
              type="checkbox"
              :checked="acc.auto_reply_enabled"
              :disabled="savingId === acc.id || !!(license && !license.can_use_business)"
              @change="toggleAutoReply(acc, $event)"
            />
            <span class="slider"></span>
          </label>
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
  gap: 16px;
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.head-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.head-title h2 {
  margin: 0;
  font-size: 1.4rem;
  font-weight: 800;
}

.count-badge {
  font-size: 0.85rem;
  color: var(--text-muted);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-box {
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 12px;
  color: var(--text-muted);
  pointer-events: none;
}

.search-input {
  padding-left: 36px;
  width: 200px;
}

.license-tip {
  margin: 0;
  color: var(--warning);
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
  border: 2px solid var(--border-subtle);
  border-radius: 50%;
  border-top-color: var(--brand-primary);
  animation: spin 1s infinite linear;
}

.empty-icon {
  color: var(--text-muted);
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
  border-color: var(--danger-soft);
  background: var(--danger-soft);
}

.card.error .icon {
  color: var(--danger);
  flex-shrink: 0;
}

.err-text h4 {
  margin: 0 0 4px;
  color: var(--danger);
}

.err-text p {
  margin: 0;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

/* Accounts Grid Layout: 4 列固定 */
.accounts-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

.account-card-sm {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  cursor: pointer;
}

.account-card-sm.disabled {
  opacity: 0.85;
}

.card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.avatar {
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  font-weight: 700;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.avatar.sm {
  width: 36px;
  height: 36px;
  font-size: 0.95rem;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}

.status-dot.online {
  background: var(--success);
}

.menu-wrap {
  position: relative;
  margin-left: auto;
}

.menu-trigger {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  border-radius: 8px;
  cursor: pointer;
  transition: var(--transition-quick);
}

.menu-trigger:hover {
  background: var(--bg-app);
  color: var(--text-primary);
}

.menu-dropdown {
  position: absolute;
  top: 30px;
  right: 0;
  z-index: 20;
  display: flex;
  flex-direction: column;
  min-width: 132px;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.12);
  padding: 6px;
}

.menu-dropdown button {
  border: none;
  background: transparent;
  text-align: left;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 0.85rem;
  color: var(--text-primary);
  cursor: pointer;
}

.menu-dropdown button:hover {
  background: var(--bg-app);
}

.menu-dropdown button.danger {
  color: var(--danger);
}

.menu-dropdown button.danger:hover {
  background: var(--danger-soft);
}

.nickname {
  font-size: 0.92rem;
  color: var(--text-primary);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.credential-badge {
  align-self: flex-start;
  font-size: 0.68rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 99px;
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
}
.credential-badge.success {
  background: var(--success-soft);
  border-color: rgba(5, 150, 105, 0.2);
  color: var(--success);
}
.credential-badge.danger {
  background: var(--danger-soft);
  border-color: rgba(220, 38, 38, 0.2);
  color: var(--danger);
}

.card-divider {
  height: 1px;
  background: var(--border-subtle);
}

.card-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.reply-count {
  font-size: 0.76rem;
  color: var(--text-secondary);
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
  width: 40px;
  height: 22px;
  background: var(--border-strong);
  border-radius: 99px;
  transition: var(--transition-quick);
}

.ios-switch .slider:before {
  content: "";
  position: absolute;
  height: 16px;
  width: 16px;
  left: 3px;
  bottom: 3px;
  background-color: #fff;
  border-radius: 50%;
  transition: var(--transition-quick);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.ios-switch input:checked + .slider {
  background: var(--success);
}

.ios-switch input:checked + .slider:before {
  transform: translateX(18px);
}

/* Modal Content Panel */
.import-modal-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tips-box {
  background: var(--bg-app);
  border-radius: 12px;
  padding: 12px 16px;
  border: 1px solid var(--border-subtle);
  border-left: 3px solid var(--violet);
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.5;
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
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid rgba(220, 38, 38, 0.2);
}

.success-msg {
  background: var(--success-soft);
  color: var(--success);
  border: 1px solid rgba(5, 150, 105, 0.2);
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
  background: var(--warning-soft);
  color: var(--warning);
  border: 1px solid rgba(217, 119, 6, 0.2);
}

.btn-danger-glass {
  background: var(--danger-soft);
  border-color: rgba(220, 38, 38, 0.3);
  color: var(--danger);
}
.btn-danger-glass:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.15);
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

@media (max-width: 1200px) {
  .accounts-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 860px) {
  .accounts-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .head {
    flex-direction: column;
    align-items: flex-start;
  }
  .search-input {
    width: 160px;
  }
}
</style>
