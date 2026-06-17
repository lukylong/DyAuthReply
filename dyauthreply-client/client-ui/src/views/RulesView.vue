<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
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

const loading = ref(true);
const error = ref('');
const accounts = ref<DouyinAccount[]>([]);
const rules = ref<DouyinRule[]>([]);
const filterAccountId = ref('');
const showForm = ref(false);
const editing = ref<DouyinRule | null>(null);
const submitting = ref(false);
const formError = ref('');

const form = ref({
  name: '',
  match_type: 'contains',
  keywordsText: '',
  reply_text: '',
  links: [] as RuleLink[],
  send_mode: 'merged' as 'merged' | 'multi_message',
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
      return { title: (item.title || '').trim(), url: (item.url || '').trim() };
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
  return rows.map((l) => l.url).join(' · ');
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
    send_mode: 'merged',
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
    send_mode: (rule.send_mode === 'multi_message' ? 'multi_message' : 'merged'),
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

function parseKeywords(text: string) {
  return text
    .split(/[\n,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function submitForm() {
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
          title: (l.title || '').trim() || l.url.trim(),
          url: l.url.trim(),
        })),
      send_mode: form.value.send_mode,
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

async function removeRule(rule: DouyinRule) {
  if (!confirm(`删除规则「${rule.name}」？`)) return;
  try {
    await deleteRule(rule.id);
    await loadRules();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

watch(filterAccountId, loadRules);

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
  <div>
    <div class="head">
      <div>
        <h2>自动回复规则</h2>
        <p class="sub">按关键词 / 兜底匹配私信，优先级高的先命中</p>
      </div>
      <button type="button" class="btn primary" @click="openCreate">+ 新建规则</button>
    </div>

    <div class="toolbar card">
      <label>
        账号筛选
        <select v-model="filterAccountId">
          <option value="">全部</option>
          <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
        </select>
      </label>
      <span class="hint">当前：{{ filterLabel }} · 共 {{ rules.length }} 条</span>
    </div>

    <section v-if="loading" class="card">加载中…</section>
    <section v-else-if="error" class="card error">{{ error }}</section>
    <section v-else-if="rules.length === 0" class="card empty">
      <p>还没有规则</p>
      <button type="button" class="btn primary" @click="openCreate">创建第一条规则</button>
    </section>
    <section v-else class="list">
      <article v-for="rule in rules" :key="rule.id" class="card rule">
        <div class="rule-top">
          <div>
            <strong>{{ rule.name }}</strong>
            <span class="tag">{{ matchTypeLabel(rule.match_type) }}</span>
            <span class="tag" :class="rule.status ? 'on' : 'off'">{{ rule.status ? '启用' : '停用' }}</span>
          </div>
          <div class="actions">
            <button type="button" class="btn ghost sm" @click="toggleStatus(rule)">
              {{ rule.status ? '停用' : '启用' }}
            </button>
            <button type="button" class="btn ghost sm" @click="openEdit(rule)">编辑</button>
            <button type="button" class="btn ghost sm danger" @click="removeRule(rule)">删除</button>
          </div>
        </div>
        <p class="meta">
          {{ rule.account_nickname || '全部账号' }}
          · 优先级 {{ rule.priority }}
          · 冷却 {{ rule.cooldown_seconds ?? 0 }}s
        </p>
        <p v-if="rule.keywords?.length" class="kw">关键词：{{ rule.keywords.join('、') }}</p>
        <p class="reply">{{ rule.reply_text || '（无文案）' }}</p>
        <p v-if="formatLinksPreview(rule.links)" class="kw">链接：{{ formatLinksPreview(rule.links) }}</p>
      </article>
    </section>

    <div v-if="showForm" class="overlay" @click.self="closeForm">
      <div class="modal">
        <h3>{{ editing ? '编辑规则' : '新建规则' }}</h3>
        <label class="field">
          规则名称
          <input v-model="form.name" type="text" placeholder="例如：询价自动回复" />
        </label>
        <label class="field">
          所属账号
          <select v-model="form.account_id">
            <option value="">全部账号（全局）</option>
            <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.nickname }}</option>
          </select>
        </label>
        <label class="field">
          匹配方式
          <select v-model="form.match_type">
            <option value="contains">包含关键词</option>
            <option value="default">兜底（无命中时）</option>
            <option value="regex">正则</option>
          </select>
        </label>
        <label v-if="form.match_type === 'contains'" class="field">
          关键词（每行一个）
          <textarea v-model="form.keywordsText" rows="3" placeholder="价格&#10;怎么买" />
        </label>
        <label v-if="form.match_type === 'regex'" class="field">
          正则表达式
          <input v-model="form.keywordsText" type="text" placeholder="例如：.*咨询.*" />
        </label>
        <label class="field">
          回复内容
          <textarea v-model="form.reply_text" rows="4" placeholder="您好，感谢私信… 可用 {{nickname}} {{link_1}}" />
        </label>
        <div class="field">
          <span>附带链接（可选）</span>
          <div v-if="form.links.length === 0" class="link-empty">暂无链接，点击下方添加名片/商品链接</div>
          <div v-for="(_, idx) in form.links" :key="idx" class="link-row">
            <input v-model="form.links[idx].title" type="text" placeholder="链接标题（可选）" />
            <input v-model="form.links[idx].url" type="url" placeholder="https://..." />
            <button type="button" class="btn ghost sm" @click="removeLinkRow(idx)">删</button>
          </div>
          <button type="button" class="btn ghost sm link-add" @click="addLinkRow">+ 添加链接</button>
        </div>
        <label class="field">
          发送方式
          <select v-model="form.send_mode">
            <option value="merged">合并一条（文案 + 链接）</option>
            <option value="multi_message">分条发送（先文案后链接）</option>
          </select>
        </label>
        <div class="row2">
          <label class="field">
            优先级
            <input v-model.number="form.priority" type="number" min="0" />
          </label>
          <label class="field">
            冷却（秒）
            <input v-model.number="form.cooldown_seconds" type="number" min="0" />
          </label>
        </div>
        <label class="check">
          <input v-model="form.status" type="checkbox" />
          创建后立即启用
        </label>
        <p v-if="formError" class="msg error">{{ formError }}</p>
        <div class="actions-bottom">
          <button type="button" class="btn ghost" :disabled="submitting" @click="closeForm">取消</button>
          <button type="button" class="btn primary" :disabled="submitting" @click="submitForm">
            {{ submitting ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
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

.card.error {
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

.card.empty {
  text-align: center;
  padding: 36px 20px;
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

select,
input,
textarea {
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  background: #0f172a;
  color: #e2e8f0;
  padding: 8px 10px;
  font: inherit;
}

.hint {
  color: #64748b;
  font-size: 0.85rem;
}

.list {
  display: grid;
  gap: 12px;
}

.rule-top {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.tag {
  margin-left: 8px;
  font-size: 0.72rem;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.15);
  color: #cbd5e1;
}

.tag.on {
  background: rgba(34, 197, 94, 0.15);
  color: #86efac;
}

.tag.off {
  background: rgba(239, 68, 68, 0.12);
  color: #fca5a5;
}

.meta,
.kw,
.reply {
  margin: 8px 0 0;
  color: #94a3b8;
  font-size: 0.85rem;
}

.reply {
  color: #e2e8f0;
}

.actions {
  display: flex;
  gap: 6px;
}

.btn {
  border: none;
  border-radius: 10px;
  padding: 10px 16px;
  cursor: pointer;
  font-size: 0.92rem;
}

.btn.sm {
  padding: 6px 10px;
  font-size: 0.82rem;
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

.btn.ghost.danger {
  color: #fca5a5;
}

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: grid;
  place-items: center;
  padding: 20px;
  z-index: 100;
}

.modal {
  width: min(560px, 100%);
  max-height: 90vh;
  overflow-y: auto;
  background: #1e293b;
  border-radius: 16px;
  padding: 22px;
  border: 1px solid rgba(148, 163, 184, 0.2);
}

.modal h3 {
  margin: 0 0 16px;
}

.field {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 0.88rem;
  color: #94a3b8;
}

.row2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.check {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
  font-size: 0.88rem;
}

.actions-bottom {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.msg.error {
  color: #fca5a5;
  font-size: 0.88rem;
}

.link-row {
  display: grid;
  grid-template-columns: 1fr 1.4fr auto;
  gap: 8px;
  margin-bottom: 8px;
}

.link-empty {
  color: #64748b;
  font-size: 0.85rem;
  margin-bottom: 8px;
}

.link-add {
  margin-top: 4px;
}
</style>
