<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { Download, RefreshCw, RotateCcw, Trash2, Globe, AlertTriangle } from 'lucide-vue-next';
import AppModal from './AppModal.vue';
import {
  getQuickAuthComponent, getCurrentQuickAuthSession, installQuickAuthComponent,
  repairQuickAuthComponent, rollbackQuickAuthComponent, uninstallQuickAuthComponent,
} from '../api/client';
import { browserStateLabel, createBrowserComponentManager } from '../composables/browserComponentManager';

const manager = createBrowserComponentManager({
  status: getQuickAuthComponent, current: getCurrentQuickAuthSession,
  install: installQuickAuthComponent, repair: repairQuickAuthComponent,
  rollback: rollbackQuickAuthComponent, uninstall: uninstallQuickAuthComponent,
});
const { state } = manager;
const confirmUninstall = ref(false);
const progress = computed(() => Math.min(100, Math.max(0, state.component?.progress_percent ?? 0)));
const recentFailure = computed(() => state.session && ['failed', 'browser_exited', 'timed_out'].includes(state.session.state) ? state.session.message : '');
function bytes(value = 0) { return `${(value / 1024 / 1024).toFixed(1)} MB`; }
async function uninstall() {
  if (!manager.can('uninstall')) return;
  confirmUninstall.value = false;
  await manager.act('uninstall');
}
onMounted(() => void manager.refresh());
onBeforeUnmount(manager.dispose);
</script>

<template>
  <section class="browser-panel" aria-labelledby="browser-title">
    <header class="browser-header">
      <div><h2 id="browser-title"><Globe :size="22" /> 内置浏览器</h2><p>管理快捷登录使用的独立浏览器组件。</p></div>
      <button class="btn-glass" :disabled="state.loading || !!state.busy" @click="manager.refresh()"><RefreshCw :size="15" /> {{ state.loading ? '检查中…' : '刷新状态' }}</button>
    </header>
    <dl class="browser-meta">
      <div><dt>组件状态</dt><dd>{{ state.component ? browserStateLabel[state.component.state] : '读取中…' }}</dd></div>
      <div><dt>浏览器版本</dt><dd>{{ state.component?.version || '—' }}</dd></div>
      <div><dt>组件来源</dt><dd>{{ state.component?.source === 'development_browser' ? '系统浏览器（仅开发环境）' : 'D 助手内置组件' }}</dd></div>
    </dl>
    <p v-if="state.component" class="component-message">{{ state.component.message }}</p>
    <p v-if="state.component?.selection_reason" class="component-message">
      {{ state.component.selection_reason }}<template v-if="state.component.recommended_version">：{{ state.component.recommended_version }}</template>。
      <template v-if="state.component.source === 'managed' && state.component.recommended_version && state.component.version && state.component.version !== state.component.recommended_version">
        当前安装版本不同，点击“修复重装”会自动换成适配版本，保留账号和登录资料。
      </template>
    </p>
    <p class="browser-note">“文件校验正常”表示程序文件完整，不代表浏览器一定能成功启动。打开后立即退出时，请先尝试修复重装。</p>
    <div v-if="state.component?.state === 'installing'" class="browser-progress" aria-live="polite">
      <progress :value="state.component.progress_percent == null ? undefined : progress" max="100" aria-label="浏览器下载进度" />
      <span>{{ state.component.progress_percent == null ? '正在连接下载源 / 安装中' : `${progress}%` }} · {{ bytes(state.component.downloaded_bytes) }}<template v-if="state.component.total_bytes"> / {{ bytes(state.component.total_bytes) }}</template></span>
    </div>
    <p v-if="manager.activeSession()" class="browser-warning">快捷登录窗口正在使用中，请先结束快捷登录，再安装、修复或卸载。</p>
    <div v-if="recentFailure" class="browser-warning"><b><AlertTriangle :size="16" /> 最近一次快捷登录未完成</b><p>{{ recentFailure }}</p><small>这是最近一次记录。修复后请重新快捷登录验证；如仍退出，请反馈包含退出码的新日志。</small></div>
    <div class="browser-actions">
      <button class="btn-glass btn-primary-glass" :disabled="!manager.can('install')" @click="manager.act('install')"><Download :size="16" /> {{ state.busy === 'install' ? '准备安装…' : '安装内置浏览器' }}</button>
      <button class="btn-glass" :disabled="!manager.can('repair')" @click="manager.act('repair')"><RefreshCw :size="16" /> {{ state.busy === 'repair' ? '准备修复…' : '修复重装' }}</button>
      <button class="btn-glass" :disabled="!manager.can('rollback')" @click="manager.act('rollback')"><RotateCcw :size="16" /> 回退上一份组件</button>
      <button class="btn-glass danger" :disabled="!manager.can('uninstall')" @click="confirmUninstall = true"><Trash2 :size="16" /> {{ state.busy === 'uninstall' ? '卸载中…' : '卸载内置浏览器' }}</button>
    </div>
    <p v-if="state.error" role="alert" class="browser-warning">{{ state.error }}</p>
    <p v-if="state.notice" role="status" class="browser-success">{{ state.notice }}</p>
    <footer>修复会重新下载并校验完整安装包，即使版本相同也会替换。卸载只删除内置浏览器文件，不删除登录资料、已导入账号、回复规则和聊天数据，也不卸载系统 Chrome / Edge。</footer>
    <AppModal :open="confirmUninstall" title="卸载内置浏览器？" dialog-role="alertdialog" @close="confirmUninstall = false">
      <p>只删除 D 助手的内置浏览器组件，保留登录资料、账号、规则和聊天数据。</p>
      <p>卸载后，快捷登录需要重新安装组件才能使用；现有账号的协议托管不依赖这个浏览器。</p>
      <div class="browser-actions"><button class="btn-glass" @click="confirmUninstall = false">取消</button><button class="btn-glass danger" :disabled="!manager.can('uninstall')" @click="uninstall">确认卸载</button></div>
    </AppModal>
  </section>
</template>

<style scoped>
.browser-panel{padding:28px;background:rgba(255,255,255,.78);border:1px solid rgba(15,23,42,.08);border-radius:22px;color:#17243c}
.browser-header{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}.browser-header h2{display:flex;align-items:center;gap:10px;margin:0;font-size:21px}.browser-header p{color:#64748b;font-size:14px;margin:10px 0 0}
.browser-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin:26px 0;padding:20px;background:#f6f8fc;border-radius:14px}.browser-meta dt{font-size:12px;color:#64748b}.browser-meta dd{margin:8px 0 0;font-weight:600;overflow-wrap:anywhere}
.component-message,.browser-note{font-size:13px;line-height:1.7;overflow-wrap:anywhere}.browser-note,footer{color:#64748b}.browser-actions{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0}.browser-actions button,.browser-header button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:40px}.danger{color:#b42318}.browser-warning,.browser-success{padding:14px 16px;border-radius:12px;font-size:13px;line-height:1.7;overflow-wrap:anywhere}.browser-warning{background:#fff6e8;color:#854d0e}.browser-warning b{display:flex;align-items:center;gap:7px}.browser-warning p{margin:8px 0}.browser-success{background:#ecfdf3;color:#166534}.browser-progress{display:grid;gap:8px;margin:18px 0;font-size:12px;color:#475569}.browser-progress progress{width:100%;height:10px;accent-color:#4169d8}footer{font-size:12px;line-height:1.8;border-top:1px solid #e8ecf3;padding-top:18px}button:disabled{opacity:.45;cursor:not-allowed}
@media(max-width:800px){.browser-panel{padding:20px}.browser-header{flex-direction:column}.browser-meta{grid-template-columns:1fr}.browser-actions button{flex:1 1 180px}}
</style>
