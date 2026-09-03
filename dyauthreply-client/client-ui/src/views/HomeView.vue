<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import {
  Check,
  CircleCheck,
  Inbox,
  Puzzle,
  TriangleAlert,
} from 'lucide-vue-next';
import {
  getHealth,
  getReplyLogStat,
  listAccounts,
  type DouyinAccount,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const loading = ref(true);
const error = ref('');
const accounts = ref<DouyinAccount[]>([]);
const { licenseStatus: license, ensureStatus } = useClientLicense();
const currentStep = ref(1);
const wizardRef = ref<HTMLElement | null>(null);

// 今日回复统计：从 /douyin/reply-log/stat/summary?scope=today 拉取，请求失败时保留占位符，
// 不影响概览页其余区块渲染。
const todayReply = ref<number | null>(null);
const todaySuccess = ref<number | null>(null);
const todayFailed = ref<number | null>(null);
const todayStatError = ref('');

const todayReplyDisplay = computed(() => (todayReply.value === null ? '--' : todayReply.value));
const todaySuccessDisplay = computed(() => (todaySuccess.value === null ? '--' : todaySuccess.value));
const todayFailedDisplay = computed(() => (todayFailed.value === null ? '--' : todayFailed.value));

async function loadTodayStat() {
  try {
    const stat = await getReplyLogStat(undefined, 'today');
    todayReply.value = stat.total;
    todaySuccess.value = stat.success;
    todayFailed.value = stat.failed;
    todayStatError.value = '';
  } catch (e) {
    todayStatError.value = e instanceof Error ? e.message : String(e);
    console.error('加载今日回复统计失败:', e);
  }
}

const onlineCount = computed(() => accounts.value.filter((a) => a.status === 1).length);
const offlineCount = computed(() => accounts.value.length - onlineCount.value);

const heroOk = computed(
  () => accounts.value.length > 0 && (!license.value || license.value.can_use_business),
);

const heroIcon = computed(() => {
  if (heroOk.value) return CircleCheck;
  return accounts.value.length === 0 ? Inbox : TriangleAlert;
});

const heroTitle = computed(() => {
  if (heroOk.value) return '自动回复正在运行';
  if (accounts.value.length === 0) return '尚未托管任何抖音号';
  return '自动回复已暂停';
});

async function initDashboard() {
  loading.value = true;
  error.value = '';
  try {
    await getHealth();
    await ensureStatus();
    accounts.value = await listAccounts();

    // Automatically set stepper progress based on system status
    if (accounts.value.length > 0) {
      currentStep.value = 3;
    } else {
      currentStep.value = 2; // Connected to backend but no accounts imported yet
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }

  // 今日回复统计单独拉取、单独兜底：即使这一个请求失败，也不影响上面主区域已渲染的内容。
  await loadTodayStat();
}

function goInstallExtension() {
  // 暂无独立的插件下载/打包产物，先引导到下方“快速接入向导”第 1 步的安装说明。
  currentStep.value = 1;
  wizardRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

onMounted(() => {
  initDashboard();
});
</script>

<template>
  <div class="home-container">
    <p class="eyebrow">概览</p>

    <div v-if="loading" class="loading-state glass-panel">
      <div class="spinner"></div>
      <p>正在同步本端核心服务状态，请稍候...</p>
    </div>

    <div v-else-if="error" class="error-state glass-panel">
      <TriangleAlert class="error-icon" :size="32" />
      <h2>后台核心服务未连接</h2>
      <p class="error-msg">{{ error }}</p>
      <p class="hint">请先启动桌面主程序，或稍候重试。</p>
      <button type="button" class="btn-glass btn-primary-glass" @click="initDashboard">重新连接服务</button>
    </div>

    <template v-else>
      <!-- 状态主区 -->
      <section class="hero glass-panel">
        <div class="hero-left">
          <component :is="heroIcon" class="hero-icon" :class="{ ok: heroOk, warn: !heroOk && accounts.length > 0 }" :size="26" />
          <div class="hero-text">
            <h2>{{ heroTitle }}</h2>
            <p>已托管 {{ accounts.length }} 个抖音账号 · 授权{{ license?.state_label || '未配置' }}</p>
          </div>
        </div>
        <RouterLink to="/accounts" class="btn-glass btn-primary-glass">查看账号</RouterLink>
      </section>

      <!-- 数据统计条 -->
      <section class="stats-bar glass-panel">
        <div class="stat-item">
          <span class="num">{{ todayReplyDisplay }}</span>
          <span class="lbl">今日回复</span>
        </div>
        <span class="stat-divider"></span>
        <div class="stat-item">
          <span class="num success">{{ todaySuccessDisplay }}</span>
          <span class="lbl">成功</span>
        </div>
        <span class="stat-divider"></span>
        <div class="stat-item">
          <span class="num danger">{{ todayFailedDisplay }}</span>
          <span class="lbl">失败</span>
        </div>
        <span class="stat-divider"></span>
        <div class="stat-item">
          <span class="num">{{ onlineCount }}</span>
          <span class="lbl">在线账号</span>
        </div>
        <span class="stat-divider"></span>
        <div class="stat-item">
          <span class="num">{{ offlineCount }}</span>
          <span class="lbl">掉线账号</span>
        </div>
      </section>

      <!-- 浏览器插件下载入口 -->
      <section class="plugin-row glass-panel">
        <div class="plugin-info">
          <div class="plugin-icon"><Puzzle :size="20" /></div>
          <div class="plugin-text">
            <h5>浏览器插件</h5>
            <p>用于一键提取抖音登录态，配合第 1 步使用</p>
          </div>
        </div>
        <button type="button" class="btn-glass btn-primary-glass" @click="goInstallExtension">下载安装</button>
      </section>

      <!-- 快速接入向导（去卡片化，仅顶部分隔线） -->
      <section ref="wizardRef" class="stepper-section">
        <div class="stepper-header">
          <h3>快速接入向导</h3>
          <p>只需简单三步，即可将抖音号托管至本终端，开启自动回复服务</p>
        </div>

        <!-- Beautiful horizontal wizard timeline stepper -->
        <div class="stepper-container">
          <!-- Background connector line -->
          <div class="stepper-line">
            <div class="stepper-line-progress" :style="{ width: currentStep === 1 ? '0%' : currentStep === 2 ? '50%' : '100%' }"></div>
          </div>

          <div class="step-node" :class="{ active: currentStep === 1, completed: currentStep > 1 }" @click="currentStep = 1">
            <div class="step-circle">
              <Check v-if="currentStep > 1" class="check-icon" :size="16" />
              <span v-else>1</span>
            </div>
            <span class="step-title">安装提取插件</span>
          </div>

          <div class="step-node" :class="{ active: currentStep === 2, completed: currentStep > 2 }" @click="currentStep = 2">
            <div class="step-circle">
              <Check v-if="currentStep > 2" class="check-icon" :size="16" />
              <span v-else>2</span>
            </div>
            <span class="step-title">获取登录凭证</span>
          </div>

          <div class="step-node" :class="{ active: currentStep === 3 }" @click="currentStep = 3">
            <div class="step-circle">3</div>
            <span class="step-title">导入托管运行</span>
          </div>
        </div>

        <!-- Instructions box -->
        <div class="step-content-box">
          <div v-if="currentStep === 1" class="step-slide">
            <h4>第一步：安装浏览器 Credential 提取扩展</h4>
            <p class="step-intro">由于抖音平台的登录安全校验，本客户端需配合官方浏览器扩展使用，提取用于协议直连的加密凭证，安全无侵入。</p>
            <div class="instruction-list">
              <div class="instruction-item">
                <span class="item-num">1</span>
                <div class="item-text">
                  定位到项目根目录下的 <code class="code-path">browser-extension/douyin-cred-extractor</code> 文件夹。
                </div>
              </div>
              <div class="instruction-item">
                <span class="item-num">2</span>
                <div class="item-text">
                  在 Chrome 或 Edge 浏览器打开 <code class="code-path">chrome://extensions/</code>，开启右上方 <strong>“开发者模式”</strong>。
                </div>
              </div>
              <div class="instruction-item">
                <span class="item-num">3</span>
                <div class="item-text">
                  点击左上角 <strong>“加载已解压的扩展程序”</strong>，选中上述的扩展文件夹导入。
                </div>
              </div>
            </div>
            <div class="action-row">
              <button type="button" class="btn-glass btn-primary-glass" @click="currentStep = 2">我已安装，进入下一步 →</button>
            </div>
          </div>

          <div v-if="currentStep === 2" class="step-slide">
            <h4>第二步：在抖音后台一键复制凭证</h4>
            <p class="step-intro">使用已登录抖音账号的浏览器访问后台，利用插件快捷提取必要的登录态凭证串。</p>
            <div class="instruction-list">
              <div class="instruction-item">
                <span class="item-num">1</span>
                <div class="item-text">
                  使用已安装插件的浏览器，登录 <a href="https://creator.douyin.com" target="_blank" class="link-styled">抖音创作者服务平台</a> 并进入私信页面。
                </div>
              </div>
              <div class="instruction-item">
                <span class="item-num">2</span>
                <div class="item-text">
                  点击浏览器右上角的扩展图标，点击 <strong>“复制一键导入串”</strong>（内容将以 <code class="code-highlight">DYCRED1.</code> 开头）。
                </div>
              </div>
            </div>
            <div class="action-row">
              <button type="button" class="btn-glass" @click="currentStep = 1">← 上一步</button>
              <button type="button" class="btn-glass btn-primary-glass" @click="currentStep = 3">复制完成，去导入 →</button>
            </div>
          </div>

          <div v-if="currentStep === 3" class="step-slide">
            <h4>第三步：粘贴凭证并激活自动回复</h4>
            <p class="step-intro">粘贴刚才复制的一键导入串，系统将自动校验其时效性并绑定账号资料。</p>
            <div class="instruction-list">
              <div class="instruction-item">
                <span class="item-num">1</span>
                <div class="item-text">
                  点击下方 <strong>“立即去导入账号”</strong> 按钮前往账号管理页面。
                </div>
              </div>
              <div class="instruction-item">
                <span class="item-num">2</span>
                <div class="item-text">
                  点击 <strong>“导入账号”</strong> 并粘贴复制的 <code class="code-highlight">DYCRED1.xxxx</code> 一键导入串。
                </div>
              </div>
              <div class="instruction-item">
                <span class="item-num">3</span>
                <div class="item-text">
                  导入成功后，开启 <strong>“自动回复”</strong> 开关并配置关键词规则。
                </div>
              </div>
            </div>
            <div class="action-row">
              <button type="button" class="btn-glass" @click="currentStep = 2">← 上一步</button>
              <RouterLink to="/accounts" class="btn-glass btn-primary-glass">立即去导入账号</RouterLink>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.home-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}

.eyebrow {
  margin: 0;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.loading-state, .error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 40px;
  text-align: center;
  gap: 16px;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--border-subtle);
  border-radius: 50%;
  border-top-color: var(--text-muted);
  animation: spin 1s infinite linear;
}

.error-icon {
  color: var(--warning);
}

.error-msg {
  color: var(--warning);
  font-family: monospace;
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 0.88rem;
}

.hint {
  color: var(--text-secondary);
  max-width: 500px;
  font-size: 0.85rem;
}

/* 状态主区 */
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 26px;
}

.hero-left {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.hero-icon {
  flex-shrink: 0;
  color: var(--text-muted);
}

.hero-icon.ok {
  color: var(--success);
}

.hero-icon.warn {
  color: var(--warning);
}

.hero-text h2 {
  margin: 0;
  font-size: 1.2rem;
  font-weight: 800;
  color: var(--text-primary);
}

.hero-text p {
  margin: 4px 0 0;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

/* 数据统计条 */
.stats-bar {
  display: flex;
  align-items: center;
  padding: 18px 26px;
  gap: 20px;
}

.stat-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}

.stat-item .num {
  font-size: 1.3rem;
  font-weight: 800;
  color: var(--text-primary);
}

.stat-item .num.success {
  color: var(--success);
}

.stat-item .num.danger {
  color: var(--danger);
}

.stat-item .lbl {
  font-size: 0.74rem;
  color: var(--text-muted);
  font-weight: 600;
}

.stat-divider {
  width: 1px;
  align-self: stretch;
  background: var(--border-subtle);
}

/* 浏览器插件下载入口 */
.plugin-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 24px;
}

.plugin-info {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.plugin-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: var(--violet-soft);
  color: var(--violet);
  flex-shrink: 0;
}

.plugin-text h5 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-primary);
}

.plugin-text p {
  margin: 4px 0 0;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

/* Stepper Section Layout (去卡片化，仅顶部分隔线) */
.stepper-section {
  padding-top: 24px;
  border-top: 1px solid var(--border-subtle);
}

.stepper-header h3 {
  margin: 0;
  font-size: 1.05rem;
  color: var(--text-primary);
  font-weight: 800;
}

.stepper-header p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 0.85rem;
}

/* Timeline Stepper Container */
.stepper-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: relative;
  margin: 28px 0 36px;
  padding: 0 48px;
}

.stepper-line {
  position: absolute;
  top: 17px;
  left: 90px;
  right: 90px;
  height: 2px;
  background: var(--border-subtle);
  z-index: 0;
  border-radius: 1px;
  overflow: hidden;
}

.stepper-line-progress {
  height: 100%;
  background: var(--brand-primary);
  transition: width 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.step-node {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  cursor: pointer;
  flex: 1;
}

.step-circle {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 0.85rem;
  color: var(--text-muted);
  transition: var(--transition-smooth);
}

.step-node:hover .step-circle {
  border-color: var(--border-strong);
  color: var(--text-primary);
}

.step-node.active .step-circle {
  background: var(--brand-primary);
  border-color: var(--brand-primary);
  color: #fff;
}

.step-node.completed .step-circle {
  background: var(--success);
  border-color: var(--success);
  color: #fff;
}

.check-icon {
  display: block;
}

.step-title {
  margin-top: 10px;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-muted);
  transition: var(--transition-smooth);
}

.step-node.active .step-title {
  color: var(--text-primary);
  font-weight: 700;
}

.step-node.completed .step-title {
  color: #15803d;
}

/* Stepper Slides panel */
.step-content-box {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  padding: 24px;
}

.step-slide h4 {
  margin: 0 0 10px;
  font-size: 0.95rem;
  color: var(--text-primary);
  font-weight: 700;
}

.step-intro {
  margin: 0 0 18px;
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.5;
}

/* Checklist Styles */
.instruction-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
}

.instruction-item {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  background: var(--bg-app);
  border: 1px solid var(--border-subtle);
  padding: 12px 16px;
  border-radius: 10px;
  transition: var(--transition-quick);
}

.instruction-item:hover {
  background: var(--brand-primary-soft);
  border-color: var(--brand-primary);
}

.item-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--text-secondary);
  flex-shrink: 0;
  margin-top: 1px;
}

.item-text {
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.5;
  flex: 1;
}

.code-path {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  padding: 2px 6px;
  border-radius: 5px;
  font-family: monospace;
  font-size: 0.85em;
  color: var(--brand-primary);
  font-weight: 600;
}

.code-highlight {
  background: var(--success-soft);
  border: 1px solid rgba(5, 150, 105, 0.2);
  padding: 2px 6px;
  border-radius: 5px;
  font-family: monospace;
  font-size: 0.85em;
  color: var(--success);
  font-weight: 600;
}

.link-styled {
  color: var(--brand-primary);
  text-decoration: none;
  font-weight: 600;
  border-bottom: 1px dashed rgba(37, 99, 235, 0.4);
  padding-bottom: 1px;
  transition: var(--transition-quick);
}

.link-styled:hover {
  color: var(--brand-primary-hover);
  border-bottom-style: solid;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.btn-glass {
  text-decoration: none;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .stats-bar {
    flex-wrap: wrap;
  }
  .stat-divider {
    display: none;
  }
  .stepper-container {
    padding: 0;
    margin: 20px 0 28px;
  }
  .stepper-line {
    left: 45px;
    right: 45px;
  }
  .hero {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
