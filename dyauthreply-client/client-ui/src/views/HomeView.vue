<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import {
  getBootstrap,
  getHealth,
  listAccounts,
  type BootstrapInfo,
  type DouyinAccount,
} from '../api/client';
import { useClientLicense } from '../composables/useClientLicense';

const loading = ref(true);
const error = ref('');
const health = ref<{ ok: boolean; env: string } | null>(null);
const bootstrap = ref<BootstrapInfo | null>(null);
const accounts = ref<DouyinAccount[]>([]);
const { licenseStatus: license, ensureStatus } = useClientLicense();
const currentStep = ref(1);

async function initDashboard() {
  loading.value = true;
  error.value = '';
  try {
    health.value = await getHealth();
    bootstrap.value = await getBootstrap();
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
}

onMounted(() => {
  initDashboard();
});
</script>

<template>
  <div class="home-container">
    <header class="dashboard-header">
      <div class="logo-area">
        <span class="logo-emoji">⚡</span>
        <div class="logo-text">
          <h1 class="gradient-text">自动回复控制台</h1>
          <p class="subtitle">运行在本机的多账号抖音私信托管运营中心</p>
        </div>
      </div>
      <div v-if="!loading && !error" class="header-status">
        <span class="status-dot green pulsing"></span>
        <span class="status-label">核心服务已连接</span>
      </div>
      <div v-else-if="!loading && error" class="header-status">
        <span class="status-dot red"></span>
        <span class="status-label text-error">核心服务连接断开</span>
      </div>
    </header>

    <div v-if="loading" class="loading-state glass-panel">
      <div class="spinner"></div>
      <p>正在同步本端核心服务状态，请稍候...</p>
    </div>

    <div v-else-if="error" class="error-state glass-panel">
      <div class="error-icon">⚠️</div>
      <h2>后台核心服务未连接</h2>
      <p class="error-msg">{{ error }}</p>
      <p class="hint">请先启动桌面主程序。如果是开发者模式，请确认在终端运行了 <code>npm run launcher</code> 且 Django 服务端口 8765 已就绪。</p>
      <button type="button" class="btn-glass btn-primary-glass" @click="initDashboard">重新连接服务</button>
    </div>

    <template v-else>
      <!-- Dashboard Status Stats Grid -->
      <section class="stats-grid">
        <div class="stat-card glass-panel">
          <div class="stat-icon api-icon">⚡</div>
          <div class="stat-content">
            <span class="label">服务状态</span>
            <span class="value success-text">运行中</span>
            <span class="subtext">环境: {{ health?.env }} · 端口: {{ bootstrap?.http_port }}</span>
          </div>
        </div>

        <div class="stat-card glass-panel">
          <div class="stat-icon accounts-icon">👤</div>
          <div class="stat-content">
            <span class="label">托管账号</span>
            <span class="value">{{ accounts.length }} 个</span>
            <span class="subtext">已启用自动回复: {{ accounts.filter(a => a.auto_reply_enabled).length }} 个</span>
          </div>
        </div>

        <div class="stat-card glass-panel">
          <div class="stat-icon">🔐</div>
          <div class="stat-content">
            <span class="label">客户端授权</span>
            <span class="value" :class="{ 'success-text': license?.can_use_business, 'danger-text': license && !license.can_use_business }">
              {{ license?.state_label || '未配置' }}
            </span>
            <span class="subtext">{{ license?.masked_code || '未绑定卡密' }}</span>
          </div>
        </div>

        <div class="stat-card glass-panel">
          <div class="stat-icon storage-icon">💾</div>
          <div class="stat-content">
            <span class="label">存储方式</span>
            <span class="value">SQLite3</span>
            <span class="subtext" :title="bootstrap?.data_dir">数据隔离 · 本地沙箱隔离</span>
          </div>
        </div>
      </section>

      <!-- Visual Interactive Setup Stepper -->
      <section class="stepper-section glass-panel">
        <div class="stepper-header">
          <h3>🚀 快速接入向导</h3>
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
              <span v-if="currentStep > 1" class="check-icon">✓</span>
              <span v-else>1</span>
            </div>
            <span class="step-title">安装提取插件</span>
          </div>

          <div class="step-node" :class="{ active: currentStep === 2, completed: currentStep > 2 }" @click="currentStep = 2">
            <div class="step-circle">
              <span v-if="currentStep > 2" class="check-icon">✓</span>
              <span v-else>2</span>
            </div>
            <span class="step-title">获取登录凭证</span>
          </div>

          <div class="step-node" :class="{ active: currentStep === 3 }" @click="currentStep = 3">
            <div class="step-circle">3</div>
            <span class="step-title">导入托管运行</span>
          </div>
        </div>

        <!-- Translucent light theme instructions box -->
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

      <!-- CTA Shortcuts -->
      <section class="cta-shortcuts">
        <RouterLink to="/license" class="shortcut-btn glass-panel">
          <div class="btn-inner">
            <span class="icon">🔐</span>
            <div class="text">
              <h5>查看授权状态</h5>
              <p>检查激活情况、离线宽限时间与当前设备绑定信息</p>
            </div>
          </div>
          <span class="arrow">→</span>
        </RouterLink>

        <RouterLink to="/chat" class="shortcut-btn glass-panel">
          <div class="btn-inner">
            <span class="icon">💬</span>
            <div class="text">
              <h5>进入私信工作台</h5>
              <p>实时监控、手动收发抖音私信，免去频繁登录抖音后台的琐碎</p>
            </div>
          </div>
          <span class="arrow">→</span>
        </RouterLink>

        <RouterLink to="/rules" class="shortcut-btn glass-panel">
          <div class="btn-inner">
            <span class="icon">🎯</span>
            <div class="text">
              <h5>回复规则定制</h5>
              <p>自由配置包含关键词、正则表达式匹配或全局兜底的匹配逻辑</p>
            </div>
          </div>
          <span class="arrow">→</span>
        </RouterLink>
      </section>
    </template>
  </div>
</template>

<style scoped>
.home-container {
  display: flex;
  flex-direction: column;
  gap: 24px;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 16px;
}

.logo-emoji {
  font-size: 1.8rem;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid var(--glass-border);
  border-radius: 12px;
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.logo-text {
  display: flex;
  flex-direction: column;
}

.gradient-text {
  font-size: 1.6rem;
  font-weight: 800;
  margin: 0;
  color: var(--text-primary);
  letter-spacing: -0.5px;
}

.subtitle {
  color: var(--text-secondary);
  font-size: 0.88rem;
  margin: 4px 0 0;
}

.header-status {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.45);
  padding: 6px 14px;
  border-radius: 20px;
  border: 1px solid var(--glass-border);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.02);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.green {
  background: #10b981;
}

.status-dot.red {
  background: #ef4444;
}

.status-dot.pulsing {
  animation: pulse-green 2s infinite;
}

@keyframes pulse-green {
  0% {
    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.6);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
  }
}

.status-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-secondary);
}

.text-error {
  color: #ef4444;
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
  border: 3px solid rgba(0, 0, 0, 0.05);
  border-radius: 50%;
  border-top-color: var(--text-muted);
  animation: spin 1s infinite linear;
}

.error-icon {
  font-size: 2.5rem;
}

.error-msg {
  color: #c2410c;
  font-family: monospace;
  background: rgba(0, 0, 0, 0.03);
  border: 1px solid rgba(0, 0, 0, 0.05);
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 0.88rem;
}

.hint {
  color: var(--text-secondary);
  max-width: 500px;
  font-size: 0.85rem;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 18px 22px;
}

.stat-icon {
  font-size: 1.35rem;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  background: rgba(0, 0, 0, 0.03);
  border: 1px solid rgba(0, 0, 0, 0.03);
  color: var(--text-secondary);
  flex-shrink: 0;
}

.api-icon {
  color: #0284c7;
  background: rgba(14, 165, 233, 0.08);
}

.accounts-icon {
  color: #10b981;
  background: rgba(16, 185, 129, 0.08);
}

.storage-icon {
  color: #6366f1;
  background: rgba(99, 102, 241, 0.08);
}

.stat-content {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.stat-content .label {
  font-size: 0.72rem;
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-content .value {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text-primary);
  margin: 2px 0;
}

.success-text {
  color: #16803d;
}

.stat-content .subtext {
  font-size: 0.72rem;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Stepper Section Layout */
.stepper-section {
  padding: 28px;
}

.stepper-header h3 {
  margin: 0;
  font-size: 1.15rem;
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
  background: rgba(0, 0, 0, 0.06);
  z-index: 0;
  border-radius: 1px;
  overflow: hidden;
}

.stepper-line-progress {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #10b981);
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
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(0, 0, 0, 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 0.85rem;
  color: var(--text-muted);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.01);
  transition: var(--transition-smooth);
}

.step-node:hover .step-circle {
  border-color: rgba(0, 0, 0, 0.25);
  color: var(--text-primary);
}

.step-node.active .step-circle {
  background: var(--text-primary);
  border-color: var(--text-primary);
  color: #fff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.step-node.completed .step-circle {
  background: #10b981;
  border-color: #10b981;
  color: #fff;
  box-shadow: 0 4px 10px rgba(16, 185, 129, 0.2);
}

.check-icon {
  font-weight: bold;
  font-size: 0.95rem;
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
  background: rgba(255, 255, 255, 0.25);
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 14px;
  padding: 24px;
  box-shadow: inset 0 1px 2px rgba(255, 255, 255, 0.2);
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

/* Premium Checklist Styles */
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
  background: rgba(255, 255, 255, 0.35);
  border: 1px solid rgba(0, 0, 0, 0.01);
  padding: 12px 16px;
  border-radius: 10px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.01);
  transition: var(--transition-quick);
}

.instruction-item:hover {
  background: rgba(255, 255, 255, 0.55);
  border-color: rgba(255, 255, 255, 0.55);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.01);
}

.item-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.04);
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
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid rgba(0, 0, 0, 0.02);
  padding: 2px 6px;
  border-radius: 5px;
  font-family: monospace;
  font-size: 0.85em;
  color: #0284c7;
  font-weight: 600;
}

.code-highlight {
  background: rgba(16, 185, 129, 0.04);
  border: 1px solid rgba(16, 185, 129, 0.08);
  padding: 2px 6px;
  border-radius: 5px;
  font-family: monospace;
  font-size: 0.85em;
  color: #059669;
  font-weight: 600;
}

.link-styled {
  color: #0284c7;
  text-decoration: none;
  font-weight: 600;
  border-bottom: 1px dashed rgba(2, 132, 199, 0.4);
  padding-bottom: 1px;
  transition: var(--transition-quick);
}

.link-styled:hover {
  color: #0369a1;
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

/* CTA Shortcuts */
.cta-shortcuts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.shortcut-btn {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  text-decoration: none;
  color: inherit;
  border-radius: 18px;
}

.shortcut-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05);
  border-color: rgba(255, 255, 255, 0.55);
}

.btn-inner {
  display: flex;
  gap: 16px;
  align-items: center;
}

.btn-inner .icon {
  font-size: 1.5rem;
  background: rgba(255, 255, 255, 0.45);
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.01);
}

.btn-inner .text h5 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-primary);
}

.btn-inner .text p {
  margin: 4px 0 0;
  font-size: 0.78rem;
  color: var(--text-secondary);
  line-height: 1.45;
  max-width: 320px;
}

.shortcut-btn .arrow {
  font-size: 1.1rem;
  color: var(--text-muted);
  transition: var(--transition-quick);
}

.shortcut-btn:hover .arrow {
  color: var(--text-primary);
  transform: translateX(4px);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
  .stepper-container {
    padding: 0;
    margin: 20px 0 28px;
  }
  .stepper-line {
    left: 45px;
    right: 45px;
  }
  .cta-shortcuts {
    grid-template-columns: 1fr;
  }
  .dashboard-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
}
</style>
