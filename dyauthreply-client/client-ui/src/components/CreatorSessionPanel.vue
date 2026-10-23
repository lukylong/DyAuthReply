<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { ExternalLink, LoaderCircle } from 'lucide-vue-next';
import { finishCreatorSession, getCreatorSession, startCreatorSession, type CreatorSession, type DouyinAccount } from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const props = defineProps<{ account: DouyinAccount }>();
const { licenseStatus } = useClientLicense();
const session = ref<CreatorSession | null>(null);
const busy = ref(false);
const error = ref('');
const confirmation = ref(false);
const ready = ref(false);
let alive = true;
let sequence = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const active = computed(() => !!session.value && !['completed', 'needs_attention'].includes(session.value.state));
const mine = computed(() => session.value?.account_id === props.account.id);
const ending = computed(() => session.value && ['syncing', 'closing'].includes(session.value.state));
const available = computed(() => ready.value && !busy.value && !!licenseStatus.value?.can_use_business);

async function refresh() {
  if (!alive || busy.value) return;
  const ticket = ++sequence;
  try {
    const value = await getCreatorSession();
    if (!alive || ticket !== sequence) return;
    const previous = session.value;
    session.value = value;
    ready.value = true;
    error.value = '';
    if (value?.account_id === props.account.id) {
      if (!['completed', 'needs_attention'].includes(value.state)) props.account.auto_reply_enabled = false;
      else if (value.state === 'completed' && previous?.session_id === value.session_id && previous.state !== 'completed') props.account.auto_reply_enabled = false;
    }
  } catch (e) {
    if (!alive || ticket !== sequence) return;
    ready.value = false;
    error.value = e instanceof Error ? e.message : String(e);
  }
}
async function poll() {
  await refresh();
  if (alive) timer = setTimeout(poll, active.value ? 1200 : 5000);
}
async function open() {
  if (!available.value || active.value) return;
  busy.value = true;
  sequence += 1;
  error.value = '';
  const account = props.account;
  try {
    const value = await startCreatorSession(account.id, true);
    if (!alive) return;
    session.value = value;
    account.auto_reply_enabled = false;
    confirmation.value = false;
  } catch (e) {
    if (alive) error.value = e instanceof Error ? e.message : String(e);
  } finally { busy.value = false; }
}
async function finish() {
  const value = session.value;
  if (!value || !mine.value || busy.value) return;
  busy.value = true;
  sequence += 1;
  try {
    const next = await finishCreatorSession(value.session_id);
    if (alive) session.value = { ...next, state: 'closing' };
  } catch (e) {
    if (alive) error.value = e instanceof Error ? e.message : String(e);
  } finally { busy.value = false; }
}
onMounted(() => void poll());
onUnmounted(() => { alive = false; sequence += 1; clearTimeout(timer); });
</script>

<template>
  <section class="creator-panel" aria-labelledby="creator-title">
    <div class="creator-heading">
      <div><h4 id="creator-title">创作者平台</h4><p>复用当前账号登录态，免扫码进入私信</p></div>
      <ExternalLink :size="20" aria-hidden="true" />
    </div>
    <template v-if="confirmation && !active">
      <div class="creator-confirm" role="alertdialog" aria-label="确认进入创作者平台">
        <strong>{{ account.auto_reply_enabled ? '进入前暂停此账号自动回复？' : '进入此账号的创作者平台？' }}</strong>
        <p>人工操作期间暂停此账号自动回复，其他账号继续运行。关闭浏览器或点击结束后，自动回复仍保持关闭；需要继续时，请在账号列表手动开启。</p>
        <div class="creator-actions">
          <button class="btn-secondary" type="button" :disabled="busy" @click="confirmation = false">取消</button>
          <button class="btn-primary" type="button" :disabled="!available" @click="open">{{ busy ? '正在打开…' : '确认并免扫码进入' }}</button>
        </div>
      </div>
    </template>
    <button v-else-if="!active" class="btn-primary creator-open" type="button" :disabled="!available" @click="confirmation = true">
      <ExternalLink :size="15" />免扫码进入平台
    </button>
    <div v-else-if="mine" class="creator-active">
      <p aria-live="polite"><LoaderCircle v-if="session?.state !== 'open'" :size="15" class="spin" />{{ session?.message }}</p>
      <button class="btn-primary" type="button" :disabled="busy || !!ending" @click="finish">{{ ending ? '正在关闭并校验…' : '结束窗口并校验凭证' }}</button>
    </div>
    <p v-else class="creator-note">另一账号正在使用创作者窗口，请先结束该窗口。</p>
    <p v-if="mine && !active" class="creator-result" :class="{ warning: session?.state === 'needs_attention' }" role="status">{{ session?.message }}</p>
    <p v-if="error" class="creator-error" role="alert">{{ error }}</p>
    <p class="creator-note">可直接关闭浏览器；系统只校验凭证，不会自动开启自动回复。</p>
  </section>
</template>

<style scoped>
.creator-panel { border: 1px solid var(--border-color, #dbe4ef); border-radius: 14px; padding: 16px; margin-bottom: 18px; background: var(--bg-card); }
.creator-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
h4 { margin: 0 0 5px; font-size: 15px; }
p { margin: 6px 0; font-size: 12px; line-height: 1.65; }
.creator-heading p, .creator-note { color: var(--text-secondary, #64748b); }
.creator-open, .creator-active p, .creator-actions { display: flex; align-items: center; gap: 8px; }
.creator-actions { justify-content: flex-end; margin-top: 12px; }
.creator-confirm { padding: 12px; border-radius: 10px; background: var(--bg-secondary, #f1f5f9); }
.creator-result { color: #15803d; }
.creator-error, .warning { color: #b45309; }
.spin { animation: spin 1s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
