<script setup lang="ts">
import { onMounted, ref } from 'vue';
import {
  createTemplate,
  deleteTemplate,
  listTemplates,
  patchTemplate,
  type DouyinTemplate,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const loading = ref(true);
const error = ref('');
const { licenseStatus: license, ensureStatus } = useClientLicense();
const templates = ref<DouyinTemplate[]>([]);
const showForm = ref(false);
const editing = ref<DouyinTemplate | null>(null);
const submitting = ref(false);
const formError = ref('');

const form = ref({
  name: '',
  content: '',
  status: true,
  remark: '',
});

async function load() {
  loading.value = true;
  error.value = '';
  try {
    await ensureStatus();
    const page = await listTemplates({ page: 1, pageSize: 200 });
    templates.value = page.items ?? [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    templates.value = [];
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  form.value = { name: '', content: '', status: true, remark: '' };
  formError.value = '';
  showForm.value = true;
}

function openEdit(tpl: DouyinTemplate) {
  editing.value = tpl;
  form.value = {
    name: tpl.name,
    content: tpl.content,
    status: tpl.status,
    remark: tpl.remark || '',
  };
  formError.value = '';
  showForm.value = true;
}

function closeForm() {
  showForm.value = false;
  editing.value = null;
}

async function submitForm() {
  if (!license.value?.can_use_business) {
    formError.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法保存模板`;
    return;
  }
  if (!form.value.name.trim()) {
    formError.value = '请填写模板名称';
    return;
  }
  if (!form.value.content.trim()) {
    formError.value = '请填写模板内容';
    return;
  }
  submitting.value = true;
  formError.value = '';
  try {
    const payload = {
      name: form.value.name.trim(),
      content: form.value.content,
      status: form.value.status,
      remark: form.value.remark.trim() || null,
    };
    if (editing.value) {
      await patchTemplate(editing.value.id, payload);
    } else {
      await createTemplate(payload);
    }
    closeForm();
    await load();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}

async function toggleStatus(tpl: DouyinTemplate) {
  if (!license.value?.can_use_business) {
    error.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法修改模板`;
    return;
  }
  try {
    await patchTemplate(tpl.id, { status: !tpl.status });
    tpl.status = !tpl.status;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function removeTemplate(tpl: DouyinTemplate) {
  if (!license.value?.can_use_business) {
    error.value = `当前授权状态为「${license.value?.state_label || '未激活'}」，无法删除模板`;
    return;
  }
  if (!confirm(`删除模板「${tpl.name}」？`)) return;
  try {
    await deleteTemplate(tpl.id);
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <div class="head">
      <div>
        <h2>回复模板</h2>
        <p class="sub">可复用文案，规则里可引用模板；支持变量如 nickname</p>
        <p v-if="license && !license.can_use_business" class="license-tip">
          当前授权状态为「{{ license.state_label }}」，模板编辑已限制。
        </p>
      </div>
      <button type="button" class="btn primary" :disabled="license ? !license.can_use_business : false" @click="openCreate">+ 新建模板</button>
    </div>

    <section v-if="loading" class="card">加载中…</section>
    <section v-else-if="error" class="card error">{{ error }}</section>
    <section v-else-if="templates.length === 0" class="card empty">
      <p>还没有模板</p>
      <button type="button" class="btn primary" @click="openCreate">创建第一个模板</button>
    </section>
    <section v-else class="grid">
      <article v-for="tpl in templates" :key="tpl.id" class="card tpl">
        <div class="tpl-head">
          <strong>{{ tpl.name }}</strong>
          <span class="tag" :class="tpl.status ? 'on' : 'off'">{{ tpl.status ? '启用' : '停用' }}</span>
        </div>
        <p class="content">{{ tpl.content }}</p>
        <p class="meta">已用 {{ tpl.use_count ?? 0 }} 次</p>
        <div class="actions">
          <button type="button" class="btn ghost sm" @click="toggleStatus(tpl)">
            {{ tpl.status ? '停用' : '启用' }}
          </button>
          <button type="button" class="btn ghost sm" @click="openEdit(tpl)">编辑</button>
          <button type="button" class="btn ghost sm danger" @click="removeTemplate(tpl)">删除</button>
        </div>
      </article>
    </section>

    <div v-if="showForm" class="overlay" @click.self="closeForm">
      <div class="modal">
        <h3>{{ editing ? '编辑模板' : '新建模板' }}</h3>
        <label class="field">
          名称
          <input v-model="form.name" type="text" placeholder="例如：欢迎语" />
        </label>
        <label class="field">
          内容
          <textarea v-model="form.content" rows="6" placeholder="您好，感谢关注…" />
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

.license-tip {
  margin: 8px 0 0;
  color: #f59e0b;
  font-size: 0.88rem;
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

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.tpl-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.tag {
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

.content {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.9rem;
  line-height: 1.5;
  color: #e2e8f0;
}

.meta {
  margin: 10px 0;
  color: #64748b;
  font-size: 0.82rem;
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

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
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
  width: min(520px, 100%);
  background: #1e293b;
  border-radius: 16px;
  padding: 22px;
  border: 1px solid rgba(148, 163, 184, 0.2);
}

.field {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 0.88rem;
  color: #94a3b8;
}

input,
textarea {
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  background: #0f172a;
  color: #e2e8f0;
  padding: 8px 10px;
  font: inherit;
}

.check {
  display: flex;
  align-items: center;
  gap: 8px;
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
}
</style>
