<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import AppModal from '../components/AppModal.vue';
import {
  cloneRule,
  createRule,
  deleteRule,
  dryRunMatch,
  listAccounts,
  listRules,
  listRulesByAccount,
  matchTypeLabel,
  parseAccountConflict,
  patchRule,
  type DouyinAccount,
  type DouyinRule,
  type DryRunMatchResult,
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
  account_ids: [] as string[],
  time_window_start: '',
  time_window_end: '',
  weekday_mask: '1111111',
});

// 账号冲突确认状态
const pendingConflicts = ref<import('../api/client').RuleAccountConflict[]>([]);
const movingAccounts = ref(false);

// dry-run 状态
const dryRunVisible = ref(false);
const dryRunText = ref('');
const dryRunAccountId = ref('');
const dryRunResult = ref<DryRunMatchResult | null>(null);
const dryRunning = ref(false);

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
    account_ids: filterAccountId.value ? [filterAccountId.value] : [],
    time_window_start: '',
    time_window_end: '',
    weekday_mask: '1111111',
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
    account_ids: Array.isArray(rule.account_ids) ? [...rule.account_ids] : [],
    time_window_start: rule.time_window_start || '',
    time_window_end: rule.time_window_end || '',
    weekday_mask: rule.weekday_mask || '1111111',
  };
  formError.value = '';
  showForm.value = true;
}

function toggleFormAccount(accId: string) {
  const idx = form.value.account_ids.indexOf(accId);
  if (idx >= 0) {
    form.value.account_ids.splice(idx, 1);
  } else {
    form.value.account_ids.push(accId);
  }
}

function closeForm() {
  showForm.value = false;
  editing.value = null;
}

function onEscapeKey(e: KeyboardEvent) {
  if (e.key !== 'Escape') return;
  if (pendingConflicts.value.length) {
    cancelMoveAccounts();
    return;
  }
  if (pendingDelete.value) {
    closeDeleteConfirm();
    return;
  }
  if (showForm.value) closeForm();
}

const dialogOpen = computed(
  () => showForm.value || Boolean(pendingDelete.value) || pendingConflicts.value.length > 0,
);

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
  const basePayload = {
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
    account_ids: [...form.value.account_ids],
    weekday_mask: form.value.weekday_mask || '1111111',
    time_window_start: form.value.time_window_start || null,
    time_window_end: form.value.time_window_end || null,
  };

  const doSave = async (forceMove: boolean) => {
    const payload = { ...basePayload, force_move: forceMove };
    if (editing.value) {
      await patchRule(editing.value.id, payload);
    } else {
      await createRule(payload);
    }
  };

  try {
    await doSave(false);
    closeForm();
    await loadRules();
  } catch (e) {
    const conflicts = parseAccountConflict(e);
    if (conflicts && conflicts.length) {
      // 暂存待确认的冲突，弹确认框
      pendingConflicts.value = conflicts;
      submitting.value = false;
      return;
    }
    formError.value = e instanceof Error ? e.message : formatApiDetail(e);
  } finally {
    submitting.value = false;
  }
}

async function confirmMoveAccounts() {
  movingAccounts.value = true;
  formError.value = '';
  const basePayload = {
    name: form.value.name.trim(),
    match_type: form.value.match_type,
    keywords: form.value.match_type === 'contains' ? parseKeywords(form.value.keywordsText) : [],
    regex_pattern: form.value.match_type === 'regex' ? form.value.keywordsText.trim() : null,
    reply_text: form.value.reply_text.trim(),
    links: form.value.links
      .filter((l) => l.url.trim())
      .map((l) => ({ title: (l.title || '').trim(), url: l.url.trim() })),
    send_mode: ruleHasLinks(form.value.links) ? 'multi_message' : form.value.send_mode,
    priority: Number(form.value.priority) || 0,
    cooldown_seconds: Number(form.value.cooldown_seconds) || 300,
    status: form.value.status,
    account_ids: [...form.value.account_ids],
    weekday_mask: form.value.weekday_mask || '1111111',
    time_window_start: form.value.time_window_start || null,
    time_window_end: form.value.time_window_end || null,
    force_move: true,
  };
  try {
    if (editing.value) {
      await patchRule(editing.value.id, basePayload);
    } else {
      await createRule(basePayload);
    }
    pendingConflicts.value = [];
    closeForm();
    await loadRules();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : formatApiDetail(e);
  } finally {
    movingAccounts.value = false;
  }
}

function cancelMoveAccounts() {
  pendingConflicts.value = [];
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

async function onCloneRule(rule: DouyinRule) {
  error.value = '';
  try {
    await cloneRule(rule.id);
    await loadRules();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function onDryRun() {
  if (!dryRunText.value.trim()) return;
  dryRunning.value = true;
  dryRunResult.value = null;
  try {
    dryRunResult.value = await dryRunMatch({
      text: dryRunText.value.trim(),
      account_id: dryRunAccountId.value || undefined,
    });
  } catch (e) {
    dryRunResult.value = {
      matched: false,
      miss_reasons: [e instanceof Error ? e.message : String(e)],
    };
  } finally {
    dryRunning.value = false;
  }
}

// 星期快选辅助
const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

function weekdayOn(idx: number): boolean {
  return (form.value.weekday_mask || '1111111')[idx] === '1';
}

function toggleWeekday(idx: number) {
  const arr = (form.value.weekday_mask || '1111111').split('');
  arr[idx] = arr[idx] === '1' ? '0' : '1';
  form.value.weekday_mask = arr.join('');
}

// 冷却快选
const COOLDOWN_PRESETS = [
  { label: '不限', value: 0 },
  { label: '5 分钟', value: 300 },
  { label: '30 分钟', value: 1800 },
  { label: '1 小时', value: 3600 },
];

function weekdayLabel(mask: string): string {
  if (!mask || mask === '1111111') return '全周';
  const days = ['一', '二', '三', '四', '五', '六', '日'];
  const on = days.filter((_, i) => mask[i] === '1');
  if (on.length === 0) return '无生效日';
  return '周' + on.join('、');
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
      <div class="toolbar-right">
        <span class="toolbar-hint">当前：{{ filterLabel }} · 共 {{ rules.length }} 条规则</span>
        <button type="button" class="btn-glass btn-dryrun" @click="dryRunVisible = !dryRunVisible">
          {{ dryRunVisible ? '收起测试' : '🧪 测试规则' }}
        </button>
      </div>
    </div>

    <!-- Dry-run Panel -->
    <div v-if="dryRunVisible" class="dryrun-panel glass-panel">
      <h4 class="dryrun-title">规则匹配测试</h4>
      <p class="dryrun-desc">输入一条模拟消息，查看会命中哪条规则并预览回复内容。</p>
      <div class="dryrun-controls">
        <select class="select-glass dryrun-account" v-model="dryRunAccountId">
          <option value="">全局规则（不指定账号）</option>
          <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
        </select>
        <input
          class="input-glass dryrun-input"
          v-model="dryRunText"
          type="text"
          placeholder="输入测试消息，例如：你好，这个多少钱？"
          @keydown.enter="onDryRun"
        />
        <button type="button" class="btn-glass btn-primary-glass" :disabled="dryRunning || !dryRunText.trim()" @click="onDryRun">
          {{ dryRunning ? '匹配中...' : '开始测试' }}
        </button>
      </div>
      <div v-if="dryRunResult" class="dryrun-result" :class="dryRunResult.matched ? 'hit' : 'miss'">
        <template v-if="dryRunResult.matched">
          <span class="result-icon">✅</span>
          <div class="result-body">
            <div class="result-rule">命中规则：<strong>{{ dryRunResult.rule_name }}</strong>（{{ dryRunResult.match_type }}）</div>
            <div class="result-preview">回复预览：<span class="preview-text">{{ dryRunResult.reply_preview || '（无回复文案）' }}</span></div>
            <div v-if="dryRunResult.miss_reasons.length" class="result-notes">
              <span v-for="(r, i) in dryRunResult.miss_reasons" :key="i" class="note-item">ℹ️ {{ r }}</span>
            </div>
          </div>
        </template>
        <template v-else>
          <span class="result-icon">❌</span>
          <div class="result-body">
            <div class="result-rule">未命中任何规则</div>
            <div v-if="dryRunResult.miss_reasons.length" class="result-notes">
              <span v-for="(r, i) in dryRunResult.miss_reasons" :key="i" class="note-item">{{ r }}</span>
            </div>
          </div>
        </template>
      </div>
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
          </div>
          <div class="actions-group">
            <!-- Toggle 开关 -->
            <button
              type="button"
              class="toggle-btn"
              :class="{ 'toggle-on': rule.status }"
              :title="rule.status ? '点击停用' : '点击启用'"
              @click="toggleStatus(rule)"
            >
              <span class="toggle-knob"></span>
            </button>
            <button type="button" class="btn-glass btn-action" @click="openEdit(rule)">编辑</button>
            <button type="button" class="btn-glass btn-action" @click="onCloneRule(rule)" title="复制规则">复制</button>
            <button type="button" class="btn-glass btn-action danger-btn" @click="askRemoveRule(rule)">删除</button>
          </div>
        </div>

        <div class="rule-meta">
          <span class="meta-accounts">
            绑定账号：
            <template v-if="rule.account_nicknames && rule.account_nicknames.length">
              <span class="acc-tag" v-for="(nick, i) in rule.account_nicknames" :key="i">{{ nick }}</span>
            </template>
            <strong v-else>全局兜底（全部账号）</strong>
          </span>
          <span>优先级：<strong>{{ rule.priority }}</strong></span>
          <span>冷却：<strong>{{ rule.cooldown_seconds ? rule.cooldown_seconds + ' 秒' : '不限' }}</strong></span>
          <span v-if="rule.weekday_mask && rule.weekday_mask !== '1111111'">
            生效日：<strong>{{ weekdayLabel(rule.weekday_mask) }}</strong>
          </span>
          <span v-if="rule.time_window_start && rule.time_window_end">
            时段：<strong>{{ rule.time_window_start }}–{{ rule.time_window_end }}</strong>
          </span>
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
        
        <div class="form-field">
          <span class="field-label">应用范围（可多选；不选=全局兜底，对所有未绑定账号生效）</span>
          <div class="account-multi">
            <button
              v-for="acc in accounts"
              :key="acc.id"
              type="button"
              class="account-chip"
              :class="{ active: form.account_ids.includes(acc.id) }"
              @click="toggleFormAccount(acc.id)"
            >
              <span class="chip-check">{{ form.account_ids.includes(acc.id) ? '✓' : '' }}</span>
              {{ acc.nickname }}
            </button>
            <span v-if="accounts.length === 0" class="account-empty">暂无托管账号</span>
          </div>
          <p class="field-hint">
            {{ form.account_ids.length
              ? `已绑定 ${form.account_ids.length} 个账号（命中优先级最高）`
              : '未绑定账号 = 全局规则，对所有未被其他规则绑定的账号兜底生效' }}
          </p>
        </div>
        
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
        
        <div class="priority-cooldown-row">
          <label class="form-field priority-field">
            <span class="field-label" title="数字越大越优先命中">优先级</span>
            <input class="input-glass" v-model.number="form.priority" type="number" min="0" />
          </label>
          <div class="form-field cooldown-field">
            <span class="field-label">防刷冷却时间</span>
            <div class="cooldown-presets">
              <button
                v-for="p in COOLDOWN_PRESETS"
                :key="p.value"
                type="button"
                class="preset-btn"
                :class="{ active: form.cooldown_seconds === p.value }"
                @click="form.cooldown_seconds = p.value"
              >{{ p.label }}</button>
              <input
                class="input-glass cooldown-custom"
                v-model.number="form.cooldown_seconds"
                type="number"
                min="0"
                placeholder="自定义秒数"
              />
            </div>
          </div>
        </div>

        <div class="form-field">
          <span class="field-label">生效星期</span>
          <div class="weekday-row">
            <button
              v-for="(day, idx) in WEEKDAYS"
              :key="idx"
              type="button"
              class="weekday-btn"
              :class="{ active: weekdayOn(idx) }"
              @click="toggleWeekday(idx)"
            >{{ day }}</button>
            <button type="button" class="preset-btn ml-8" @click="form.weekday_mask = '1111111'">全选</button>
            <button type="button" class="preset-btn" @click="form.weekday_mask = '1111100'">工作日</button>
          </div>
        </div>

        <div class="row-fields">
          <label class="form-field">
            <span class="field-label">生效起始时间（留空=全天）</span>
            <input class="input-glass" v-model="form.time_window_start" type="time" />
          </label>
          <label class="form-field">
            <span class="field-label">生效截止时间</span>
            <input class="input-glass" v-model="form.time_window_end" type="time" />
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

    <!-- Account Conflict Confirmation Modal -->
    <AppModal
      :open="pendingConflicts.length > 0"
      title="账号已被其他规则绑定"
      dialog-role="alertdialog"
      @close="cancelMoveAccounts"
    >
      <div class="delete-modal-content">
        <p class="confirm-text">以下账号当前已绑定到其他规则。一个账号只能属于一条规则，确认后将把它们从原规则移除并加入本规则：</p>
        <ul class="conflict-list">
          <li v-for="c in pendingConflicts" :key="c.account_id">
            <strong>{{ c.account_nickname }}</strong>
            <span class="conflict-arrow">当前在</span>
            <span class="conflict-rule">规则「{{ c.rule_name }}」</span>
          </li>
        </ul>
        <div class="actions-footer">
          <button type="button" class="btn-glass" :disabled="movingAccounts" @click="cancelMoveAccounts">取消</button>
          <button type="button" class="btn-glass btn-primary-glass" :disabled="movingAccounts" @click="confirmMoveAccounts">
            {{ movingAccounts ? '正在移动...' : '确认移动并保存' }}
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

/* ── Toggle 开关（浅色主题，高对比）──────── */
.toggle-btn {
  position: relative;
  width: 46px;
  height: 26px;
  border-radius: 999px;
  background: #cbd5e1;
  border: 1px solid #94a3b8;
  cursor: pointer;
  transition: background 0.22s ease, border-color 0.22s ease;
  padding: 0;
  flex-shrink: 0;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.12);
}
.toggle-btn:hover {
  border-color: #64748b;
}
.toggle-btn.toggle-on {
  background: linear-gradient(135deg, #4f46e5, #6366f1);
  border-color: #4338ca;
  box-shadow: inset 0 1px 2px rgba(67, 56, 202, 0.3), 0 1px 4px rgba(79, 70, 229, 0.35);
}
.toggle-btn:focus-visible {
  outline: 2px solid var(--accent-indigo);
  outline-offset: 2px;
}
.toggle-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.35);
  transition: transform 0.22s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.toggle-on .toggle-knob {
  transform: translateX(20px);
}

/* ── Toolbar right ───────────────────── */
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.btn-dryrun {
  font-size: 0.8rem;
  padding: 7px 16px;
  border-radius: 9px;
  font-weight: 600;
  color: var(--accent-indigo);
  border: 1px solid rgba(79, 70, 229, 0.3);
  background: rgba(79, 70, 229, 0.06);
}
.btn-dryrun:hover {
  background: rgba(79, 70, 229, 0.12);
  border-color: rgba(79, 70, 229, 0.5);
}

/* ── Dry-run Panel ───────────────────── */
.dryrun-panel {
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  border-left: 3px solid var(--accent-indigo);
}
.dryrun-title {
  font-size: 0.98rem;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}
.dryrun-desc {
  font-size: 0.8rem;
  color: var(--text-muted);
  margin: 0;
}
.dryrun-controls {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.dryrun-account {
  width: 180px;
  flex-shrink: 0;
  font-size: 0.82rem;
  padding: 8px 10px;
}
.dryrun-input {
  flex: 1;
  min-width: 200px;
  font-size: 0.86rem;
}
.dryrun-result {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  font-size: 0.85rem;
}
.dryrun-result.hit {
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.3);
}
.dryrun-result.miss {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.25);
}
.result-icon { font-size: 1.15rem; margin-top: 1px; line-height: 1.2; }
.result-body { display: flex; flex-direction: column; gap: 5px; }
.result-rule { color: var(--text-secondary); }
.result-rule strong { color: var(--text-primary); font-weight: 700; }
.result-preview { color: var(--text-secondary); }
.preview-text {
  background: rgba(15, 23, 42, 0.06);
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 0.82rem;
  color: #0369a1;
  font-weight: 500;
}
.result-notes { display: flex; flex-direction: column; gap: 3px; margin-top: 5px; }
.note-item { font-size: 0.78rem; color: var(--text-muted); }

/* ── 优先级 + 冷却 一行（优先级窄，冷却占主） ── */
.priority-cooldown-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.priority-field {
  width: 88px;
  flex-shrink: 0;
}
.priority-field .input-glass {
  font-size: 0.8rem;
  padding: 6px 10px;
}
.cooldown-field {
  flex: 1;
  min-width: 0;
}

/* ── 冷却快选 ────────────────────────── */
.cooldown-presets {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  margin-top: 4px;
}
.preset-btn {
  padding: 6px 12px;
  font-size: 0.78rem;
  font-weight: 500;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: rgba(255, 255, 255, 0.6);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  white-space: nowrap;
}
.preset-btn:hover {
  background: #fff;
  border-color: #94a3b8;
  color: var(--text-primary);
}
.preset-btn.active {
  background: rgba(79, 70, 229, 0.12);
  border-color: var(--accent-indigo);
  color: var(--accent-indigo);
  font-weight: 600;
}
.cooldown-custom {
  flex: 1;
  min-width: 64px;
  width: auto;
  font-size: 0.8rem;
  padding: 6px 10px;
}
.ml-8 { margin-left: 8px; }

/* ── 账号多选 ────────────────────────── */
.account-multi {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}
.account-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  font-size: 0.82rem;
  font-weight: 500;
  border-radius: 999px;
  border: 1px solid #cbd5e1;
  background: rgba(255, 255, 255, 0.6);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.account-chip:hover {
  background: #fff;
  border-color: #94a3b8;
  color: var(--text-primary);
}
.account-chip.active {
  background: rgba(79, 70, 229, 0.12);
  border-color: var(--accent-indigo);
  color: var(--accent-indigo);
  font-weight: 600;
}
.chip-check {
  display: inline-block;
  width: 12px;
  font-weight: 700;
  color: var(--accent-indigo);
}
.account-empty {
  font-size: 0.8rem;
  color: var(--text-muted);
}

/* ── 卡片账号标签 ────────────────────── */
.meta-accounts {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.acc-tag {
  display: inline-block;
  padding: 2px 10px;
  font-size: 0.74rem;
  font-weight: 600;
  border-radius: 999px;
  background: rgba(79, 70, 229, 0.1);
  border: 1px solid rgba(79, 70, 229, 0.25);
  color: var(--accent-indigo);
}

/* ── 冲突确认列表 ────────────────────── */
.conflict-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conflict-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.18);
  font-size: 0.84rem;
}
.conflict-list strong { color: var(--text-primary); }
.conflict-arrow { color: var(--text-muted); font-size: 0.78rem; }
.conflict-rule { color: #b45309; font-weight: 600; }

/* ── 星期选择 ────────────────────────── */
.weekday-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
}
.weekday-btn {
  min-width: 46px;
  height: 34px;
  padding: 0 8px;
  font-size: 0.8rem;
  font-weight: 500;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: rgba(255, 255, 255, 0.6);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.weekday-btn:hover {
  background: #fff;
  border-color: #94a3b8;
  color: var(--text-primary);
}
.weekday-btn.active {
  background: rgba(79, 70, 229, 0.12);
  border-color: var(--accent-indigo);
  color: var(--accent-indigo);
  font-weight: 600;
}
</style>
