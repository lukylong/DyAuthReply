<script setup lang="ts">
import { onMounted, ref } from 'vue';
import {
  createCard,
  deleteCard,
  listCards,
  updateCard,
  uploadCardCover,
  type DouyinCard,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const loading = ref(true);
const error = ref('');
const { licenseStatus: license, ensureStatus } = useClientLicense();
const cards = ref<DouyinCard[]>([]);
const showForm = ref(false);
const editing = ref<DouyinCard | null>(null);
const submitting = ref(false);
const uploading = ref(false);
const formError = ref('');

const form = ref({
  title: '',
  description: '',
  cover_file_id: '' as string,
  cover_url: '' as string,
  target_url: '',
  remark: '',
  status: true,
});

async function load() {
  loading.value = true;
  error.value = '';
  try {
    await ensureStatus();
    const page = await listCards({ page: 1, pageSize: 200 });
    cards.value = page.items ?? [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    cards.value = [];
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  form.value = { title: '', description: '', cover_file_id: '', cover_url: '', target_url: '', remark: '', status: true };
  formError.value = '';
  showForm.value = true;
}

function openEdit(card: DouyinCard) {
  editing.value = card;
  form.value = {
    title: card.title,
    description: card.description || '',
    cover_file_id: card.cover_file_id || '',
    cover_url: card.cover_url || '',
    target_url: card.target_url,
    remark: card.remark || '',
    status: card.status,
  };
  formError.value = '';
  showForm.value = true;
}

function closeForm() {
  showForm.value = false;
  editing.value = null;
}

async function onPickCover(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    formError.value = '请选择图片文件';
    return;
  }
  if (file.size / 1024 / 1024 > 5) {
    formError.value = '图片需小于 5MB';
    return;
  }
  uploading.value = true;
  formError.value = '';
  try {
    const res = await uploadCardCover(file);
    form.value.cover_file_id = res.cover_file_id;
    form.value.cover_url = res.cover_url;
  } catch (e) {
    formError.value = `封面上传失败：${e instanceof Error ? e.message : String(e)}`;
  } finally {
    uploading.value = false;
    input.value = '';
  }
}

function isValidUrl(u: string) {
  return /^https?:\/\//i.test(u.trim());
}

async function submitForm() {
  if (!license.value?.can_use_business) {
    formError.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法保存卡片`;
    return;
  }
  if (!form.value.title.trim()) {
    formError.value = '请填写卡片标题';
    return;
  }
  if (!isValidUrl(form.value.target_url)) {
    formError.value = '请填写有效的目标链接（http/https）';
    return;
  }
  submitting.value = true;
  formError.value = '';
  try {
    const payload = {
      title: form.value.title.trim(),
      description: form.value.description.trim(),
      cover_file_id: form.value.cover_file_id || null,
      target_url: form.value.target_url.trim(),
      remark: form.value.remark.trim() || null,
      status: form.value.status,
    };
    if (editing.value) {
      await updateCard(editing.value.id, payload);
    } else {
      await createCard(payload);
    }
    closeForm();
    await load();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}

async function removeCard(card: DouyinCard) {
  if (!license.value?.can_use_business) {
    error.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法删除卡片`;
    return;
  }
  if (!confirm(`删除卡片「${card.title}」？`)) return;
  try {
    await deleteCard(card.id);
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function syncLabel(state?: string) {
  return { synced: '已同步', pending: '待同步', failed: '同步失败', local: '本地' }[state || 'local'] || state;
}

onMounted(load);
</script>

<template>
  <div>
    <div class="head">
      <div>
        <h2>卡片管理</h2>
        <p class="sub">卡片用于伪装链接卡片自动回复；保存后自动同步到公网，抖音抓取落地页渲染卡片</p>
        <p v-if="license && !license.can_use_business" class="license-tip">
          当前授权状态为「{{ license.state_label }}」，卡片编辑已限制。
        </p>
      </div>
      <button type="button" class="btn primary" :disabled="license ? !license.can_use_business : false" @click="openCreate">+ 新建卡片</button>
    </div>

    <section v-if="loading" class="card">加载中…</section>
    <section v-else-if="error" class="card error">{{ error }}</section>
    <section v-else-if="cards.length === 0" class="card empty">
      <p>还没有卡片</p>
      <button type="button" class="btn primary" @click="openCreate">创建第一个卡片</button>
    </section>
    <section v-else class="grid">
      <article v-for="card in cards" :key="card.id" class="card tpl">
        <div class="tpl-head">
          <img v-if="card.cover_url" :src="card.cover_url" class="cover" alt="" />
          <div class="tpl-title">
            <strong>{{ card.title }}</strong>
            <span class="tag" :class="card.status ? 'on' : 'off'">{{ card.status ? '启用' : '停用' }}</span>
            <span class="tag sync" :class="card.sync_state">{{ syncLabel(card.sync_state) }}</span>
          </div>
        </div>
        <p v-if="card.description" class="content">{{ card.description }}</p>
        <p class="meta">{{ card.target_url }}</p>
        <div class="actions">
          <button type="button" class="btn ghost sm" @click="openEdit(card)">编辑</button>
          <button type="button" class="btn ghost sm danger" @click="removeCard(card)">删除</button>
        </div>
      </article>
    </section>

    <div v-if="showForm" class="overlay" @click.self="closeForm">
      <div class="modal">
        <h3>{{ editing ? '编辑卡片' : '新建卡片' }}</h3>
        <label class="field">
          卡片标题
          <input v-model="form.title" type="text" placeholder="例如：点击咨询" />
        </label>
        <label class="field">
          卡片描述
          <textarea v-model="form.description" rows="2" placeholder="卡片副标题/描述" />
        </label>
        <div class="field">
          封面图
          <div class="cover-row">
            <img v-if="form.cover_url" :src="form.cover_url" class="cover-preview" alt="" />
            <label class="btn ghost sm upload-btn">
              {{ uploading ? '上传中…' : (form.cover_url ? '更换封面' : '上传封面') }}
              <input type="file" accept="image/*" hidden :disabled="uploading" @change="onPickCover" />
            </label>
          </div>
        </div>
        <label class="field">
          目标链接
          <input v-model="form.target_url" type="text" placeholder="https://… 用户点击后跳转" />
        </label>
        <label class="field">
          备注
          <input v-model="form.remark" type="text" />
        </label>
        <label class="check">
          <input v-model="form.status" type="checkbox" />
          启用
        </label>
        <p v-if="formError" class="msg error">{{ formError }}</p>
        <div class="actions-bottom">
          <button type="button" class="btn ghost" :disabled="submitting" @click="closeForm">取消</button>
          <button type="button" class="btn primary" :disabled="submitting || uploading" @click="submitForm">
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
  margin-bottom: 20px;
}

.head h2 {
  margin: 0 0 6px;
  color: var(--text-primary);
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

.card {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  border-radius: 16px;
  padding: 18px 20px;
  color: var(--text-primary);
  backdrop-filter: blur(12px);
}

.card.error {
  border-color: rgba(239, 68, 68, 0.35);
  color: #dc2626;
}

.card.empty {
  text-align: center;
  padding: 36px 20px;
  color: var(--text-secondary);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.tpl-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.cover {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
  background: rgba(0, 0, 0, 0.04);
}

.tpl-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tpl-title strong {
  color: var(--text-primary);
}

.tag {
  font-size: 0.72rem;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(100, 116, 139, 0.12);
  color: var(--text-secondary);
}

.tag.on {
  background: rgba(16, 185, 129, 0.14);
  color: #047857;
}

.tag.off {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
}

.tag.sync.failed {
  background: rgba(239, 68, 68, 0.14);
  color: #dc2626;
}

.tag.sync.pending {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.tag.sync.synced {
  background: rgba(2, 132, 199, 0.14);
  color: #0284c7;
}

.content {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.9rem;
  line-height: 1.5;
  color: var(--text-secondary);
}

.meta {
  margin: 10px 0;
  color: var(--text-muted);
  font-size: 0.82rem;
  word-break: break-all;
}

.actions {
  display: flex;
  gap: 6px;
}

.btn {
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  padding: 10px 16px;
  cursor: pointer;
  font-size: 0.92rem;
  background: var(--glass-bg);
  color: var(--text-primary);
  transition: var(--transition-quick);
}

.btn:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border-active);
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn.sm {
  padding: 6px 10px;
  font-size: 0.82rem;
}

.btn.primary {
  background: linear-gradient(135deg, #0284c7, #4f46e5);
  color: #fff;
  font-weight: 600;
  border: none;
}

.btn.primary:hover {
  filter: brightness(1.05);
}

.btn.ghost {
  background: rgba(0, 0, 0, 0.03);
  color: var(--text-secondary);
}

.btn.ghost.danger {
  color: #dc2626;
}

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(4px);
  display: grid;
  place-items: center;
  padding: 20px;
  z-index: 100;
}

.modal {
  width: min(520px, 100%);
  background: rgba(255, 255, 255, 0.92);
  border-radius: 16px;
  padding: 22px;
  border: 1px solid var(--glass-border-active);
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.18);
  max-height: 90vh;
  overflow-y: auto;
  color: var(--text-primary);
}

.modal h3 {
  margin: 0 0 16px;
  color: var(--text-primary);
}

.field {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

.cover-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cover-preview {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  object-fit: cover;
  background: rgba(0, 0, 0, 0.04);
}

.upload-btn {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

input,
textarea {
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  background: rgba(255, 255, 255, 0.85);
  color: var(--text-primary);
  padding: 8px 10px;
  font: inherit;
}

input:focus,
textarea:focus {
  outline: none;
  border-color: var(--accent-blue);
  background: #fff;
}

.check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.88rem;
  color: var(--text-secondary);
}

.check input[type='checkbox'] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent-blue);
}

.actions-bottom {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.msg.error {
  color: #dc2626;
}
</style>
