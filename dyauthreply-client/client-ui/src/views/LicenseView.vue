<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';

import { useClientLicense } from '../composables/useClientLicense';
import { formatDateTime } from '../utils/formatDateTime';

const submitting = ref(false);
const error = ref('');
const success = ref('');
const { licenseStatus: status, ensureStatus, activate, refreshNow: refreshLicense, deactivate, loading } =
  useClientLicense();
const form = ref({
  license_code: '',
});

const stateClass = computed(() => {
  switch (status.value?.state) {
    case 'active':
      return 'success';
    case 'grace':
      return 'warn';
    case 'expired':
    case 'revoked':
    case 'invalid':
      return 'danger';
    default:
      return 'muted';
  }
});

async function loadStatus(force = false) {
  error.value = '';
  try {
    await (force ? refreshLicense() : ensureStatus());
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function submitActivation() {
  if (!form.value.license_code.trim()) {
    error.value = '请输入卡密';
    return;
  }
  submitting.value = true;
  error.value = '';
  success.value = '';
  try {
    await activate({
      license_code: form.value.license_code.trim(),
    });
    form.value.license_code = '';
    success.value = '设备激活成功';
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}

async function refreshNow() {
  submitting.value = true;
  success.value = '';
  error.value = '';
  try {
    await loadStatus(true);
    success.value = '授权状态已刷新';
  } finally {
    submitting.value = false;
  }
}

async function unbindLicense() {
  submitting.value = true;
  success.value = '';
  error.value = '';
  try {
    await deactivate();
    success.value = '当前设备已解绑';
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  loadStatus(false);
});
</script>

<template>
  <div class="license-page">
    <div class="page-head">
      <div>
        <h2>客户端授权</h2>
        <p class="sub">授权异常时仍可查看状态与诊断信息，业务操作会被限制。</p>
      </div>
    </div>

    <div v-if="loading" class="glass-panel state-card">
      <div class="dot-spinner"></div>
      <p>正在加载授权状态...</p>
    </div>

    <template v-else>
      <section class="license-grid">
        <article class="glass-panel state-card">
          <div class="row between">
            <div>
              <span class="label">当前状态</span>
              <h3 :class="stateClass">{{ status?.state_label || '未知' }}</h3>
            </div>
            <span class="pill" :class="stateClass">{{ status?.state || 'unknown' }}</span>
          </div>
          <div class="meta-list">
            <div class="meta-item">
              <span>业务可用</span>
              <strong>{{ status?.can_use_business ? '是' : '否' }}</strong>
            </div>
            <div class="meta-item">
              <span>卡密</span>
              <strong>{{ status?.masked_code || '未绑定' }}</strong>
            </div>
            <div class="meta-item">
              <span>套餐</span>
              <strong>{{ status?.plan?.name || '未分配' }}</strong>
            </div>
            <div class="meta-item">
              <span>设备指纹</span>
              <strong class="mono">{{ status?.device_fingerprint || '-' }}</strong>
            </div>
            <div class="meta-item">
              <span>离线截止</span>
              <strong>{{ formatDateTime(status?.last_valid_until) }}</strong>
            </div>
          </div>
          <div class="action-row">
            <button type="button" class="btn-glass" :disabled="submitting" @click="refreshNow">
              刷新授权
            </button>
            <button
              type="button"
              class="btn-glass"
              :disabled="submitting || !status?.license_key_id"
              @click="unbindLicense"
            >
              解绑设备
            </button>
          </div>
        </article>

        <article class="glass-panel form-card">
          <h3>激活设备</h3>
          <p class="sub">首次部署时输入卡密即可完成授权绑定。</p>

          <label class="field">
            <span>卡密</span>
            <textarea
              v-model="form.license_code"
              class="input-glass textarea"
              rows="3"
              placeholder="输入发放的卡密"
              spellcheck="false"
            />
          </label>

          <div v-if="error" class="msg error-msg">{{ error }}</div>
          <div v-if="success" class="msg success-msg">{{ success }}</div>

          <div class="action-row">
            <button type="button" class="btn-glass btn-primary-glass" :disabled="submitting" @click="submitActivation">
              {{ submitting ? '提交中...' : '立即激活' }}
            </button>
          </div>
        </article>
      </section>

      <section class="glass-panel details-card">
        <h3>授权详情</h3>
        <div class="detail-grid">
          <div class="detail-item">
            <span>激活时间</span>
            <strong>{{ formatDateTime(status?.activated_at) }}</strong>
          </div>
          <div class="detail-item">
            <span>最后校验</span>
            <strong>{{ formatDateTime(status?.last_check_in_at) }}</strong>
          </div>
          <div class="detail-item">
            <span>下次校验</span>
            <strong>{{ formatDateTime(status?.next_check_in_at) }}</strong>
          </div>
          <div class="detail-item">
            <span>到期时间</span>
            <strong>{{ formatDateTime(status?.expires_at) }}</strong>
          </div>
          <div class="detail-item">
            <span>心跳间隔</span>
            <strong>{{ status?.heartbeat_interval_minutes || 0 }} 分钟</strong>
          </div>
          <div class="detail-item">
            <span>离线宽限</span>
            <strong>{{ status?.grace_period_minutes || 0 }} 分钟</strong>
          </div>
        </div>

        <div v-if="status?.last_error" class="msg warn-msg">
          {{ status.last_error }}
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.license-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 1180px;
  margin: 0 auto;
}

.page-head h2,
.state-card h3,
.form-card h3,
.details-card h3 {
  margin: 0;
}

.sub {
  margin: 6px 0 0;
  color: var(--text-muted);
}

.license-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(360px, 0.9fr);
  gap: 20px;
}

.state-card,
.form-card,
.details-card {
  padding: 24px;
}

.row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.between {
  justify-content: space-between;
}

.label {
  display: block;
  color: var(--text-muted);
  font-size: 0.85rem;
}

.pill {
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 700;
  background: rgba(0, 0, 0, 0.05);
}

.meta-list,
.detail-grid {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}

.meta-item,
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.38);
}

.meta-item span,
.detail-item span {
  color: var(--text-muted);
  font-size: 0.84rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

.textarea {
  resize: vertical;
  min-height: 96px;
}

.action-row {
  display: flex;
  gap: 12px;
  margin-top: 18px;
  flex-wrap: wrap;
}

.msg {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 0.9rem;
}

.success,
.success-msg {
  color: #15803d;
}

.warn,
.warn-msg {
  color: #b45309;
}

.danger,
.error-msg {
  color: #b91c1c;
}

.muted {
  color: var(--text-muted);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.82rem;
}

@media (max-width: 980px) {
  .license-grid {
    grid-template-columns: 1fr;
  }
}
</style>
