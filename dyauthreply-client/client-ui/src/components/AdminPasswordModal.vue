<script setup lang="ts">
import { ref } from 'vue';
import AppModal from './AppModal.vue';
import { adminLogin, setAdminToken } from '../api/client';

defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  close: [];
  success: [];
}>();

const password = ref('');
const loading = ref(false);
const error = ref('');

async function onSubmit() {
  if (!password.value.trim()) {
    error.value = '请输入管理员密码';
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    const res = await adminLogin(password.value.trim());
    setAdminToken(res.token);
    password.value = '';
    emit('success');
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

function onClose() {
  password.value = '';
  error.value = '';
  emit('close');
}
</script>

<template>
  <AppModal :open="open" title="管理员验证" dialog-role="alertdialog" @close="onClose">
    <p class="hint">隐藏管理入口，请输入管理员密码。</p>
    <form class="form" @submit.prevent="onSubmit">
      <label class="field">
        <span>管理员密码</span>
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          placeholder="请输入密码"
          :disabled="loading"
        />
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <div class="actions">
        <button type="button" class="btn ghost" :disabled="loading" @click="onClose">取消</button>
        <button type="submit" class="btn primary" :disabled="loading">
          {{ loading ? '验证中...' : '进入管理台' }}
        </button>
      </div>
    </form>
  </AppModal>
</template>

<style scoped>
.hint {
  margin: 0 0 16px;
  color: var(--text-muted);
  font-size: 0.85rem;
  line-height: 1.5;
}

code {
  font-family: ui-monospace, monospace;
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.field input {
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 0.95rem;
}

.error {
  margin: 0;
  color: #dc2626;
  font-size: 0.85rem;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

.btn {
  border-radius: 10px;
  padding: 10px 16px;
  border: none;
  cursor: pointer;
  font-weight: 600;
}

.btn.ghost {
  background: rgba(0, 0, 0, 0.04);
  color: var(--text-secondary);
}

.btn.primary {
  background: #2563eb;
  color: #fff;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
