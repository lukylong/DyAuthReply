<script setup lang="ts">
import { ref, watch } from 'vue';
import { Heart } from 'lucide-vue-next';
import {
  getAccountWorks,
  getProfileStats,
  openExternalUrl,
  patchAccount,
  statusLabel,
  type DouyinAccount,
  type ProfileStats,
  type WorkItem,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const props = defineProps<{
  open: boolean;
  account: DouyinAccount | null;
}>();

const emit = defineEmits<{ close: [] }>();

const { licenseStatus: license } = useClientLicense();

const stats = ref<ProfileStats | null>(null);
const statsLoading = ref(false);
const statsError = ref('');

const savingQuota = ref(false);
const quotaError = ref('');

async function saveQuota(event: Event) {
  const acc = props.account;
  const input = event.target as HTMLInputElement;
  if (!acc) return;
  const quota = Number(input.value);
  if (!Number.isFinite(quota) || quota < 0) return;
  quotaError.value = '';
  if (license.value && !license.value.can_use_business) {
    quotaError.value = `当前授权状态为「${license.value.state_label}」，无法修改配额`;
    return;
  }
  savingQuota.value = true;
  try {
    const updated = await patchAccount(acc.id, { daily_reply_quota: quota });
    acc.daily_reply_quota = updated.daily_reply_quota;
  } catch (e) {
    quotaError.value = e instanceof Error ? e.message : String(e);
  } finally {
    savingQuota.value = false;
  }
}

const works = ref<WorkItem[]>([]);
const worksLoading = ref(false);
const worksError = ref('');
const worksCursor = ref('0');
const worksHasMore = ref(false);
let loadToken = 0;

function formatCount(n?: number): string {
  const v = Number(n ?? 0);
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}亿`;
  if (v >= 10_000) return `${(v / 10_000).toFixed(1)}万`;
  return String(v);
}

function formatTime(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatSyncTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function avatarInitial(name?: string): string {
  const t = (name ?? '').trim();
  return t ? t.slice(0, 1).toUpperCase() : '?';
}

function openWork(w: WorkItem) {
  const url = w.share_url || (w.aweme_id ? `https://www.douyin.com/video/${w.aweme_id}` : '');
  if (url) openExternalUrl(url);
}

async function loadStats(refresh = false) {
  const acc = props.account;
  if (!acc) return;
  const token = loadToken;
  statsLoading.value = true;
  statsError.value = '';
  try {
    const res = await getProfileStats(acc.id, refresh);
    if (token !== loadToken) return;
    stats.value = res;
    if (!res.ok) statsError.value = res.error || '获取主页信息失败';
  } catch (e) {
    if (token !== loadToken) return;
    statsError.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (token === loadToken) statsLoading.value = false;
  }
}

async function loadWorks(reset = false) {
  const acc = props.account;
  if (!acc) return;
  const token = loadToken;
  if (reset) {
    works.value = [];
    worksCursor.value = '0';
    worksHasMore.value = false;
  }
  worksLoading.value = true;
  worksError.value = '';
  try {
    const res = await getAccountWorks(acc.id, { cursor: worksCursor.value });
    if (token !== loadToken) return;
    if (!res.ok) {
      worksError.value = res.error || '获取作品列表失败';
      return;
    }
    works.value = [...works.value, ...res.items];
    worksCursor.value = res.max_cursor;
    worksHasMore.value = res.has_more;
  } catch (e) {
    if (token !== loadToken) return;
    worksError.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (token === loadToken) worksLoading.value = false;
  }
}

function refreshAll() {
  loadStats(true);
  loadWorks(true);
}

watch(
  () => props.open,
  (open) => {
    if (open && props.account) {
      loadToken += 1;
      stats.value = null;
      statsError.value = '';
      works.value = [];
      worksError.value = '';
      worksCursor.value = '0';
      worksHasMore.value = false;
      loadStats(false);
      loadWorks(true);
    }
  },
);
</script>

<template>
  <transition name="drawer-fade">
    <div v-if="open" class="drawer-overlay">
      <div class="drawer-backdrop" @click="emit('close')" />
      <aside class="drawer-panel glass-panel" role="dialog" aria-modal="true" @click.stop>
        <header class="drawer-head">
          <div class="id-group">
            <div class="avatar">
              <img v-if="account?.avatar" :src="account.avatar" alt="" />
              <span v-else>{{ avatarInitial(account?.nickname) }}</span>
            </div>
            <div class="id-text">
              <strong>{{ account?.nickname || '—' }}</strong>
              <span v-if="account?.unique_id || stats?.unique_id" class="uid">
                抖音号：{{ account?.unique_id || stats?.unique_id }}
              </span>
              <span class="badge" :class="{ success: account?.status === 1 }">
                {{ statusLabel(account?.status ?? 0) }}
              </span>
            </div>
          </div>
          <button type="button" class="drawer-close" aria-label="关闭" @click="emit('close')">×</button>
        </header>

        <div class="drawer-body">
          <!-- 统计 -->
          <section class="stats-card">
            <div class="stats-grid">
              <div class="stat">
                <span class="num">{{ formatCount(stats?.follower_count) }}</span>
                <span class="lbl">粉丝</span>
              </div>
              <div class="stat">
                <span class="num">{{ formatCount(stats?.following_count) }}</span>
                <span class="lbl">关注</span>
              </div>
              <div class="stat">
                <span class="num">{{ formatCount(stats?.aweme_count) }}</span>
                <span class="lbl">作品</span>
              </div>
              <div class="stat">
                <span class="num">{{ formatCount(stats?.total_favorited) }}</span>
                <span class="lbl">获赞</span>
              </div>
            </div>
            <div class="stats-foot">
              <span v-if="statsLoading" class="hint">同步中…</span>
              <span v-else-if="statsError" class="hint err">{{ statsError }}</span>
              <span v-else-if="stats?.last_profile_sync_at" class="hint">
                更新于 {{ formatSyncTime(stats.last_profile_sync_at) }}
                <em v-if="stats.cached">（缓存）</em>
              </span>
              <button type="button" class="btn-refresh" :disabled="statsLoading" @click="refreshAll">
                刷新
              </button>
            </div>
          </section>

          <!-- 配额编辑 -->
          <section class="quota-card">
            <div class="quota-row">
              <div class="quota-label">
                <span class="lbl">日回复限额</span>
                <span class="hint">今日已回复 {{ account?.reply_today ?? 0 }} 次</span>
              </div>
              <input
                class="input-glass quota-input"
                type="number"
                min="0"
                :value="account?.daily_reply_quota ?? 200"
                :disabled="savingQuota || !!(license && !license.can_use_business)"
                @change="saveQuota"
              />
            </div>
            <p v-if="quotaError" class="quota-error">{{ quotaError }}</p>
          </section>

          <!-- 作品 -->
          <section class="works-section">
            <h4 class="section-title">作品列表</h4>

            <div v-if="worksError && works.length === 0" class="works-msg err">{{ worksError }}</div>
            <div v-else-if="!worksLoading && works.length === 0" class="works-msg">暂无作品</div>

            <div v-else class="works-grid">
              <button
                v-for="w in works"
                :key="w.aweme_id"
                type="button"
                class="work-card"
                title="点击在浏览器中查看作品"
                @click="openWork(w)"
              >
                <div class="cover">
                  <img v-if="w.cover" :src="w.cover" alt="" loading="lazy" referrerpolicy="no-referrer" />
                  <span v-else class="cover-ph">无封面</span>
                  <span v-if="w.work_type === 'image'" class="type-tag">图集</span>
                  <span class="play-overlay">▶</span>
                </div>
                <p class="desc">{{ w.desc || '（无标题）' }}</p>
                <div class="meta">
                  <span class="likes"><Heart :size="12" /> {{ formatCount(w.like_count) }}</span>
                  <span class="date">{{ formatTime(w.create_time) }}</span>
                </div>
              </button>
            </div>

            <div class="works-foot">
              <span v-if="worksLoading" class="hint">加载中…</span>
              <button
                v-else-if="worksHasMore"
                type="button"
                class="btn-more"
                @click="loadWorks(false)"
              >
                加载更多
              </button>
              <span v-else-if="works.length > 0" class="hint">已全部加载</span>
            </div>
          </section>
        </div>
      </aside>
    </div>
  </transition>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  z-index: 999;
  display: flex;
  justify-content: flex-end;
}

.drawer-backdrop {
  position: absolute;
  inset: 0;
  background-color: rgba(17, 24, 39, 0.35);
}

.drawer-panel {
  position: relative;
  z-index: 1000;
  width: min(560px, 100%);
  height: 100%;
  background: var(--bg-card);
  border-left: 1px solid var(--border-subtle);
  box-shadow: -12px 0 32px rgba(16, 24, 40, 0.08);
  display: flex;
  flex-direction: column;
  border-radius: 0;
}

.drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 24px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.id-group {
  display: flex;
  gap: 14px;
  align-items: center;
  min-width: 0;
}

.avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  font-weight: 700;
  font-size: 1.2rem;
  color: var(--text-primary);
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.id-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.id-text strong {
  font-size: 1.15rem;
  font-weight: 800;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.id-text .uid {
  font-size: 0.78rem;
  color: var(--text-muted);
}

.badge {
  align-self: flex-start;
  font-size: 0.68rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 99px;
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
}
.badge.success {
  background: var(--success-soft);
  border-color: rgba(5, 150, 105, 0.2);
  color: var(--success);
}

.drawer-close {
  border: 1px solid var(--border-subtle);
  background: var(--bg-app);
  color: var(--text-secondary);
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  transition: var(--transition-quick);
}
.drawer-close:hover {
  background: var(--border-subtle);
  color: var(--text-primary);
}

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* 统计卡 */
.stats-card {
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  padding: 18px 16px 12px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.stat .num {
  font-size: 1.25rem;
  font-weight: 800;
  color: var(--text-primary);
}
.stat .lbl {
  font-size: 0.74rem;
  color: var(--text-muted);
}
.stats-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--border-subtle);
}
.hint {
  font-size: 0.74rem;
  color: var(--text-muted);
}
.hint.err {
  color: var(--danger);
}
.hint em {
  font-style: normal;
  opacity: 0.7;
}
.btn-refresh {
  font-size: 0.78rem;
  font-weight: 600;
  padding: 5px 14px;
  border-radius: 8px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  transition: var(--transition-quick);
}
.btn-refresh:hover:not(:disabled) {
  background: var(--bg-app);
}

/* 配额编辑 */
.quota-card {
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  padding: 14px 16px;
}
.quota-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.quota-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.quota-label .lbl {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-primary);
}
.quota-label .hint {
  font-size: 0.74rem;
  color: var(--text-muted);
}
.quota-input {
  width: 96px;
  padding: 6px 10px;
  text-align: right;
  font-weight: 600;
}
.quota-error {
  margin: 8px 0 0;
  font-size: 0.78rem;
  color: var(--danger);
}
.btn-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 作品 */
.section-title {
  margin: 0 0 12px;
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-primary);
}
.works-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.work-card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  width: 100%;
  text-decoration: none;
  color: inherit;
  text-align: left;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  font: inherit;
}
.cover {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(0, 0, 0, 0.05);
  display: grid;
  place-items: center;
  transition: var(--transition-quick);
}
.cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.25s ease;
}
.play-overlay {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 1.4rem;
  color: #fff;
  background: rgba(0, 0, 0, 0.28);
  opacity: 0;
  transition: opacity 0.2s ease;
}
.work-card:hover .cover {
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.18);
}
.work-card:hover .cover img {
  transform: scale(1.05);
}
.work-card:hover .play-overlay {
  opacity: 1;
}
.cover-ph {
  font-size: 0.72rem;
  color: var(--text-muted);
}
.type-tag {
  position: absolute;
  top: 6px;
  right: 6px;
  font-size: 0.62rem;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
}
.desc {
  margin: 0;
  font-size: 0.78rem;
  line-height: 1.35;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  font-size: 0.7rem;
  color: var(--text-muted);
}
.meta .likes {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
  white-space: nowrap;
}
.meta .date {
  flex-shrink: 0;
  margin-left: auto;
  white-space: nowrap;
}

.works-foot {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
.btn-more {
  font-size: 0.82rem;
  font-weight: 600;
  padding: 8px 24px;
  border-radius: 10px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  transition: var(--transition-quick);
}
.btn-more:hover {
  background: var(--bg-app);
}
.works-msg {
  padding: 24px;
  text-align: center;
  font-size: 0.85rem;
  color: var(--text-muted);
}
.works-msg.err {
  color: #ef4444;
}

/* 动画 */
.drawer-fade-enter-active .drawer-backdrop,
.drawer-fade-leave-active .drawer-backdrop {
  transition: background-color 0.25s ease;
}
.drawer-fade-enter-active .drawer-panel,
.drawer-fade-leave-active .drawer-panel {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.drawer-fade-enter-from .drawer-backdrop,
.drawer-fade-leave-to .drawer-backdrop {
  background-color: rgba(241, 243, 247, 0) !important;
}
.drawer-fade-enter-from .drawer-panel,
.drawer-fade-leave-to .drawer-panel {
  transform: translateX(100%);
}
</style>
