<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import AppModal from '../components/AppModal.vue';
import {
  createRule,
  deleteRule,
  listAccounts,
  listRules,
  listRulesByAccount,
  matchTypeLabel,
  patchRule,
  type DouyinAccount,
  type DouyinRule,
  type RuleLink,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

function ruleHasLinks(links: { url: string }[] | undefined) {
  return (links || []).some((l) => (l.url || '').trim());
}

const loading = ref(true);
const error = ref('');
const { licenseStatus: license, ensureStatus } = useClientLicense();
const accounts = ref<DouyinAccount[]>([]);
const rules = ref<DouyinRule[]>([]);
const filterAccountId = ref('');
const showForm = ref(false);
const editing = ref<DouyinRule | null>(null);
const submitting = ref(false);
const formError = ref('');
const pendingDelete = ref<DouyinRule | null>(null);
const deleting = ref(false);

const form = ref({
  name: '',
  match_type: 'contains',
  keywordsText: '',
  reply_text: '',
  links: [] as RuleLink[],
  send_mode: 'multi_message' as 'merged' | 'multi_message',
  priority: 0,
  cooldown_seconds: 300,
  status: true,
  account_id: '',
});

function normalizeLinks(raw?: DouyinRule['links']): RuleLink[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      if (typeof item === 'string') {
        return { title: '', url: item.trim() };
      }
      const url = (item.url || '').trim();
      let title = (item.title || '').trim();
      // 历史数据 title 被回填成 url，编辑时还原为「仅 URL 框有值」
      if (title && url && title === url) {
        title = '';
      }
      return { title, url };
    })
    .filter((item) => item.url);
}

function addLinkRow() {
  form.value.links.push({ title: '', url: '' });
}

function removeLinkRow(index: number) {
  form.value.links.splice(index, 1);
}

function formatLinksPreview(links?: DouyinRule['links']) {
  const rows = normalizeLinks(links);
  if (rows.length === 0) return '';
  return rows.map((l) => l.title || l.url).join(' · ');
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (detail instanceof Error) return detail.message;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
      .join('；');
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return String(detail ?? '保存失败');
}

const filterLabel = computed(() => {
  if (!filterAccountId.value) return '全部账号';
  return accounts.value.find((a) => a.id === filterAccountId.value)?.nickname || '已选账号';
});

async function loadAccounts() {
  await ensureStatus();
  accounts.value = await listAccounts();
}

async function loadRules() {
  loading.value = true;
  error.value = '';
  try {
    if (filterAccountId.value) {
      rules.value = await listRulesByAccount(filterAccountId.value);
    } else {
      const page = await listRules({ page: 1, pageSize: 200 });
      rules.value = page.items ?? [];
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    rules.value = [];
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  form.value = {
    name: '',
    match_type: 'contains',
    keywordsText: '',
    reply_text: '',
    links: [],
    send_mode: 'multi_message',
    priority: 0,
    cooldown_seconds: 300,
    status: true,
    account_id: filterAccountId.value,
  };
  formError.value = '';
  showForm.value = true;
}

function openEdit(rule: DouyinRule) {
  editing.value = rule;
  form.value = {
    name: rule.name,
    match_type: rule.match_type,
    keywordsText: (rule.keywords || []).join('\n'),
    reply_text: rule.reply_text || '',
    links: normalizeLinks(rule.links),
    send_mode:
      ruleHasLinks(normalizeLinks(rule.links)) || rule.send_mode === 'multi_message'
        ? 'multi_message'
        : 'merged',
    priority: rule.priority ?? 0,
    cooldown_seconds: rule.cooldown_seconds ?? 300,
    status: rule.status,
    account_id: rule.account_id || '',
  };
  formError.value = '';
  showForm.value = true;
}

function closeForm() {
  showForm.value = false;
  editing.value = null;
}

function onEscapeKey(e: KeyboardEvent) {
  if (e.key !== 'Escape') return;
  if (pendingDelete.value) {
    closeDeleteConfirm();
    return;
  }
  if (showForm.value) closeForm();
}

const dialogOpen = computed(() => showForm.value || Boolean(pendingDelete.value));

watch(dialogOpen, (open) => {
  if (open) {
    document.addEventListener('keydown', onEscapeKey);
  } else {
    document.removeEventListener('keydown', onEscapeKey);
  }
});

onBeforeRouteLeave(() => {
  closeForm();
  closeDeleteConfirm();
});

onUnmounted(() => {
  document.removeEventListener('keydown', onEscapeKey);
});

function parseKeywords(text: string) {
  return text
    .split(/[\n,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function submitForm() {
  if (!license.value?.can_use_business) {
    formError.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法保存规则`;
    return;
  }
  if (!form.value.name.trim()) {
    formError.value = '请填写规则名称';
    return;
  }
  if (form.value.match_type === 'contains' && parseKeywords(form.value.keywordsText).length === 0) {
    formError.value = '包含关键词模式下请至少填写一个关键词';
    return;
  }
  if (form.value.match_type === 'regex' && !form.value.keywordsText.trim()) {
    formError.value = '请填写正则表达式';
    return;
  }
  if (!form.value.reply_text.trim() && form.value.match_type !== 'default') {
    formError.value = '请填写回复内容';
    return;
  }
  submitting.value = true;
  formError.value = '';
  try {
    const payload = {
      name: form.value.name.trim(),
      match_type: form.value.match_type,
      keywords: form.value.match_type === 'contains' ? parseKeywords(form.value.keywordsText) : [],
      regex_pattern: form.value.match_type === 'regex' ? form.value.keywordsText.trim() : null,
      reply_text: form.value.reply_text.trim(),
      links: form.value.links
        .filter((l) => l.url.trim())
        .map((l) => ({
          title: (l.title || '').trim(),
          url: l.url.trim(),
        })),
      send_mode: ruleHasLinks(form.value.links) ? 'multi_message' : form.value.send_mode,
      priority: Number(form.value.priority) || 0,
      cooldown_seconds: Number(form.value.cooldown_seconds) || 300,
      status: form.value.status,
      account_id: form.value.account_id || null,
    };
    if (editing.value) {
      await patchRule(editing.value.id, payload);
    } else {
      await createRule(payload);
    }
    closeForm();
    await loadRules();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : formatApiDetail(e);
  } finally {
    submitting.value = false;
  }
}

async function toggleStatus(rule: DouyinRule) {
  try {
    await patchRule(rule.id, { status: !rule.status });
    rule.status = !rule.status;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function askRemoveRule(rule: DouyinRule) {
  pendingDelete.value = rule;
}

function closeDeleteConfirm() {
  pendingDelete.value = null;
}

async function confirmRemoveRule() {
  const rule = pendingDelete.value;
  if (!rule) return;
  deleting.value = true;
  error.value = '';
  try {
    await deleteRule(rule.id);
    closeDeleteConfirm();
    await loadRules();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    deleting.value = false;
  }
}

watch(filterAccountId, loadRules);

watch(
  () => form.value.links,
  (links) => {
    if (ruleHasLinks(links)) {
      form.value.send_mode = 'multi_message';
    }
  },
  { deep: true },
);

onMounted(async () => {
  try {
    await loadAccounts();
    await loadRules();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    loading.value = false;
  }
});
</script>

<template>
  <div class="rules-page">
    <div class="head">
      <div>
        <h2>自动回复规则</h2>
        <p class="sub">通过精细化匹配规则设置，当客户消息触发特定关键词时进行自动分发投递。</p>
        <p v-if="license && !license.can_use_business" class="license-tip">
          当前授权状态为「{{ license.state_label }}」，规则编辑已限制。
        </p>
      </div>
      <button type="button" class="btn-glass btn-primary-glass" @click="openCreate">
        + 新增匹配规则
      </button>
    </div>

    <!-- Toolbar controls -->
    <div class="toolbar glass-panel">
      <div class="filter-group">
        <label>
          <span>抖音号筛选</span>
          <select class="select-glass" v-model="filterAccountId">
            <option value="">全部托管账号</option>
            <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
          </select>
        </label>
      </div>
      <span class="toolbar-hint">当前：{{ filterLabel }} · 共 {{ rules.length }} 条规则</span>
    </div>

    <div v-if="loading" class="loading-state glass-panel">
      <div class="dot-spinner"></div>
      <p>正在同步回复规则，请稍候...</p>
    </div>
    
    <div v-else-if="error" class="card error glass-panel">
      <span class="icon">⚠️</span>
      <div class="err-text">
        <h4>数据读取失败</h4>
        <p>{{ error }}</p>
      </div>
    </div>
    
    <div v-else-if="rules.length === 0" class="empty-state glass-panel">
      <div class="empty-icon">🎯</div>
      <h3>暂无触发规则</h3>
      <p>配置自动回复规则以在满足条件时自动化向客户发送特定的文案或商品卡片。</p>
      <button type="button" class="btn-glass btn-primary-glass mt-16" @click="openCreate">
        创建第一条匹配规则
      </button>
    </div>

    <!-- Rules List -->
    <section v-else class="rules-list">
      <article v-for="rule in rules" :key="rule.id" class="rule-card glass-panel" :class="{ inactive: !rule.status }">
        <div class="rule-card-header">
          <div class="title-section">
            <strong>{{ rule.name }}</strong>
            <span class="badge match-type">{{ matchTypeLabel(rule.match_type) }}</span>
            <span class="badge status-tag" :class="{ active: rule.status }">
              {{ rule.status ? '已启用' : '已停用' }}
            </span>
          </div>
          <div class="actions-group">
            <button type="button" class="btn-glass btn-action" @click="toggleStatus(rule)">
              {{ rule.status ? '停用' : '启用' }}
            </button>
            <button type="button" class="btn-glass btn-action" @click="openEdit(rule)">
              编辑
            </button>
            <button type="button" class="btn-glass btn-action danger-btn" @click="askRemoveRule(rule)">
              删除
            </button>
          </div>
        </div>

        <div class="rule-meta">
          <span>所属账号：<strong>{{ rule.account_nickname || '全部全局通用' }}</strong></span>
          <span>优先级：<strong>{{ rule.priority }}</strong></span>
          <span>防刷冷却：<strong>{{ rule.cooldown_seconds ?? 0 }} 秒</strong></span>
        </div>

        <div class="rule-content">
          <p v-if="rule.keywords?.length" class="keywords-row">
            <span class="lbl">匹配词：</span>
            <span class="kw-tag" v-for="(kw, i) in rule.keywords" :key="i">{{ kw }}</span>
          </p>
          <div class="reply-text-row">
            <span class="lbl">回复内容：</span>
            <span class="reply-body">{{ rule.reply_text || '（仅发送商品/名片链接）' }}</span>
          </div>
          <p v-if="formatLinksPreview(rule.links)" class="links-row">
            <span class="lbl">下发链接：</span>
            <span class="link-preview">{{ formatLinksPreview(rule.links) }}</span>
          </p>
        </div>
      </article>
    </section>

    <!-- Editor Modal -->
    <AppModal
      :open="showForm"
      :title="editing ? '修改匹配规则' : '创建匹配规则'"
      @close="closeForm"
    >
      <div class="form-container">
        <label class="form-field">
          <span class="field-label">规则名称</span>
          <input class="input-glass" v-model="form.name" type="text" placeholder="例如：新粉询价自动回复" />
        </label>
        
        <label class="form-field">
          <span class="field-label">应用范围</span>
          <select class="select-glass" v-model="form.account_id">
            <option value="">全部托管抖音号（全局）</option>
            <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
          </select>
        </label>
        
        <label class="form-field">
          <span class="field-label">匹配逻辑</span>
          <select class="select-glass" v-model="form.match_type">
            <option value="contains">关键字模糊包含</option>
            <option value="regex">正则表达式高级匹配</option>
            <option value="default">全局兜底（无任何规则命中时触发）</option>
          </select>
        </label>
        
        <label v-if="form.match_type === 'contains'" class="form-field">
          <span class="field-label">触发关键词 (每行输入一个词)</span>
          <textarea class="input-glass" v-model="form.keywordsText" rows="3" placeholder="价格&#10;怎么卖&#10;多少钱" />
        </label>
        
        <label v-if="form.match_type === 'regex'" class="form-field">
          <span class="field-label">正则表达式模式</span>
          <input class="input-glass" v-model="form.keywordsText" type="text" placeholder="例如：.*价格.*" />
        </label>
        
        <label class="form-field">
          <span class="field-label">自动回复文本内容</span>
          <textarea class="input-glass" v-model="form.reply_text" rows="4" placeholder="支持使用占位符：{{nickname}} 标识客户昵称，{{link_1}} 引用附件链接。" />
        </label>
        
        <div class="form-field links-field">
          <span class="field-label">附件链接 (名片/卡片，可选)</span>
          <div v-if="form.links.length === 0" class="link-empty">暂无附件链接，点击下方按钮添加。</div>
          <div v-for="(_, idx) in form.links" :key="idx" class="link-row">
            <input class="input-glass title-input" v-model="form.links[idx].title" type="text" placeholder="名片名称（如：名片，可选）" />
            <input class="input-glass url-input" v-model="form.links[idx].url" type="url" placeholder="链接地址 (https://...)" />
            <button type="button" class="btn-glass delete-btn" @click="removeLinkRow(idx)">删除</button>
          </div>
          <button type="button" class="btn-glass add-link-btn" @click="addLinkRow">+ 添加链接</button>
        </div>
        
        <label class="form-field">
          <span class="field-label">下发策略</span>
          <select
            class="select-glass"
            v-model="form.send_mode"
            :disabled="ruleHasLinks(form.links)"
          >
            <option value="multi_message">分条发送 (文本与链接各自独立一条消息)</option>
            <option v-if="!ruleHasLinks(form.links)" value="merged">合并发送 (纯文本时合并为一条)</option>
          </select>
          <p v-if="ruleHasLinks(form.links)" class="field-hint">
            含附件链接时固定分条发送：先文本，再每条链接各发一条。
          </p>
        </label>
        
        <div class="row-fields">
          <label class="form-field">
            <span class="field-label">规则权重优先级 (数字越大越优先)</span>
            <input class="input-glass" v-model.number="form.priority" type="number" min="0" />
          </label>
          <label class="form-field">
            <span class="field-label">防刷频冷却时间 (秒)</span>
            <input class="input-glass" v-model.number="form.cooldown_seconds" type="number" min="0" />
          </label>
        </div>
        
        <label class="checkbox-label">
          <input type="checkbox" v-model="form.status" />
          <span class="checkbox-lbl">保存后立即启用此匹配规则</span>
        </label>
        
        <transition name="fade">
          <p v-if="formError" class="error-msg-box">{{ formError }}</p>
        </transition>
        
        <div class="actions-footer">
          <button type="button" class="btn-glass" :disabled="submitting" @click="closeForm">取消</button>
          <button type="button" class="btn-glass btn-primary-glass" :disabled="submitting" @click="submitForm">
            {{ submitting ? '正在写入规则...' : '保存规则' }}
          </button>
        </div>
      </div>
    </AppModal>

    <!-- Delete Confirmation Modal -->
    <AppModal
      :open="Boolean(pendingDelete)"
      title="确认删除规则"
      dialog-role="alertdialog"
      @close="closeDeleteConfirm"
    >
      <div class="delete-modal-content">
        <p class="confirm-text">确定彻底清除匹配规则「<strong>{{ pendingDelete?.name }}</strong>」？此操作不可逆，将立刻停止此条自动回复的匹配。</p>
        <div class="actions-footer">
          <button type="button" class="btn-glass" :disabled="deleting" @click="closeDeleteConfirm">保留规则</button>
          <button type="button" class="btn-glass btn-primary-glass danger" :disabled="deleting" @click="confirmRemoveRule">
            {{ deleting ? '正在彻底卸载...' : '确认销毁' }}
          </button>
        </div>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
.rules-page {
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

/* Toolbar Control Panel */
.toolbar {
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.filter-group label {
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

/* Rules Grid List */
.rules-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.rule-card {
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: var(--transition-smooth);
}

.rule-card:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border-active);
  transform: translateY(-1px);
}

.rule-card.inactive {
  opacity: 0.65;
}

.rule-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.title-section {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.title-section strong {
  font-size: 1.05rem;
  color: var(--text-primary);
  font-weight: 700;
}

.badge {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-muted);
}
.badge.status-tag.active {
  background: rgba(34, 197, 94, 0.1);
  border-color: rgba(34, 197, 94, 0.2);
  color: #4ade80;
}

.actions-group {
  display: flex;
  gap: 8px;
}

.btn-action {
  padding: 6px 12px;
  font-size: 0.8rem;
  border-radius: 8px;
}

.danger-btn {
  color: #fca5a5;
}
.danger-btn:hover {
  background: rgba(239, 68, 68, 0.15) !important;
  border-color: rgba(239, 68, 68, 0.25) !important;
}

.rule-meta {
  display: flex;
  gap: 16px;
  font-size: 0.78rem;
  color: var(--text-muted);
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  padding-bottom: 8px;
}
.rule-meta strong {
  color: var(--text-secondary);
}

.rule-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 0.85rem;
  line-height: 1.5;
}

.rule-content .lbl {
  color: var(--text-muted);
  font-weight: 600;
  flex-shrink: 0;
  width: 70px;
  display: inline-block;
}

.keywords-row, .reply-text-row, .links-row {
  margin: 0;
  display: flex;
  align-items: flex-start;
}

.kw-tag {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.06);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.75rem;
  margin-right: 6px;
  color: var(--text-secondary);
}

.reply-body {
  color: var(--text-primary);
}

.link-preview {
  color: var(--accent-teal);
  word-break: break-all;
}

/* Form Styling inside Modal */
.form-container {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: #334155;
}

.field-hint {
  margin: 6px 0 0;
  font-size: 0.75rem;
  color: #64748b;
  line-height: 1.4;
}

.form-field input, .form-field select, .form-field textarea {
  width: 100%;
}

.links-field {
  background: rgba(0, 0, 0, 0.02);
  padding: 14px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.05);
}

.link-empty {
  color: var(--text-muted);
  font-size: 0.78rem;
  margin-bottom: 6px;
}

.link-row {
  display: grid;
  grid-template-columns: 1fr 1.6fr auto;
  gap: 8px;
  margin-bottom: 8px;
}

.title-input, .url-input {
  font-size: 0.8rem;
  padding: 6px 10px;
}

.delete-btn {
  font-size: 0.78rem;
  padding: 6px 10px;
  color: #fca5a5;
  border-radius: 8px;
}
.delete-btn:hover {
  background: rgba(239, 68, 68, 0.1);
}

.add-link-btn {
  align-self: flex-start;
  font-size: 0.75rem;
  padding: 5px 12px;
  border-radius: 8px;
}

.row-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  margin: 6px 0;
}

.checkbox-lbl {
  font-size: 0.82rem;
  color: var(--text-secondary);
}

.error-msg-box {
  background: rgba(239, 68, 68, 0.1);
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.2);
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 0.8rem;
  margin: 0;
}

.actions-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 10px;
}

.delete-modal-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.confirm-text {
  font-size: 0.88rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0;
}

.confirm-text strong {
  color: var(--text-primary);
}

.danger {
  background: linear-gradient(135deg, #dc2626, #ef4444);
}
.danger:hover {
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3) !important;
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
