<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import {
  Bell,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Zap,
  Gauge,
  Download,
} from "lucide-vue-next";
import {
  useClientSettings,
  DEFAULT_UPDATE_MIRRORS,
} from "../composables/useClientSettings";
import { useVersionUpdate } from "../composables/useVersionUpdate";
import CapacityPanel from "../components/CapacityPanel.vue";
import { openExternalUrl } from "../api/client";

const { settings, resetSettings } = useClientSettings();
const { checkUpdate, isChecking, updateInfo, checkError } = useVersionUpdate();

// 更新镜像编辑（每行一个；保存时清洗为字符串数组）
const mirrorsText = ref(
  (settings.value.version_update.update_mirrors ?? DEFAULT_UPDATE_MIRRORS).join(
    "\n",
  ),
);

function saveMirrors() {
  const list = mirrorsText.value
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  settings.value.version_update.update_mirrors = list.length
    ? list
    : [...DEFAULT_UPDATE_MIRRORS];
}

function resetMirrors() {
  settings.value.version_update.update_mirrors = [...DEFAULT_UPDATE_MIRRORS];
  mirrorsText.value = DEFAULT_UPDATE_MIRRORS.join("\n");
}

const activeTab = ref<"capacity" | "version" | "notification" | "runtime">(
  "capacity",
);

// 运行设置：开机自启状态反馈
const autoStartBusy = ref(false);
const autoStartMessage = ref("");
const autoStartMessageType = ref<"info" | "error" | "success">("info");

function isTauriEnv(): boolean {
  return (
    typeof window !== "undefined" &&
    ("__TAURI__" in window || "__TAURI_INTERNALS__" in window)
  );
}

const checkFrequencyOptions = [
  { value: "startup", label: "仅启动时" },
  { value: "6h", label: "每 6 小时" },
  { value: "daily", label: "每天" },
  { value: "weekly", label: "每周" },
  { value: "manual", label: "手动检查" },
];

async function handleCheckUpdate() {
  await checkUpdate(true);
}

async function handleOpenDownload() {
  const url = updateInfo.value?.download_url || updateInfo.value?.release_page;
  if (url) {
    await openExternalUrl(url);
  }
}

// 进入页面时，把开关同步为系统真实的自启状态，避免与本地缓存不一致
onMounted(async () => {
  if (!isTauriEnv()) return;
  try {
    const { isEnabled } = await import("@tauri-apps/plugin-autostart");
    const enabled = await isEnabled();
    settings.value.runtime.auto_start = enabled;
  } catch (error) {
    console.warn("Failed to read auto-start state:", error);
  }
});

function setAutoStartMessage(type: "info" | "error" | "success", text: string) {
  autoStartMessageType.value = type;
  autoStartMessage.value = text;
}

async function onAutoStartChange(event: Event) {
  const target = event.target as HTMLInputElement;
  await handleToggleAutoStart(target.checked);
  // 强制把 DOM 同步为最终状态，避免回滚到原值时 Vue 因 diff 无变化而不更新复选框
  target.checked = settings.value.runtime.auto_start;
}

async function handleToggleAutoStart(enabled: boolean) {
  autoStartBusy.value = true;
  autoStartMessage.value = "";

  if (!isTauriEnv()) {
    setAutoStartMessage("error", "开机自启动仅在桌面客户端中可用");
    settings.value.runtime.auto_start = false; // 回滚
    autoStartBusy.value = false;
    return;
  }

  try {
    const { enable, disable, isEnabled } =
      await import("@tauri-apps/plugin-autostart");
    if (enabled) {
      await enable();
    } else {
      await disable();
    }
    // 以系统真实状态为准回写，确保设置生效
    const actual = await isEnabled();
    settings.value.runtime.auto_start = actual;
    setAutoStartMessage(
      "success",
      actual ? "已开启开机自启动" : "已关闭开机自启动",
    );
  } catch (error) {
    console.error("Failed to toggle auto-start:", error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    setAutoStartMessage("error", `设置失败：${errorMessage}`);
    settings.value.runtime.auto_start = !enabled; // 回滚
  } finally {
    autoStartBusy.value = false;
  }
}

function handleReset() {
  if (confirm("确定要恢复默认设置吗？")) {
    const autoStart = settings.value.runtime.auto_start;
    resetSettings();
    settings.value.runtime.auto_start = autoStart;
    mirrorsText.value = DEFAULT_UPDATE_MIRRORS.join("\n");
  }
}

const updateStatusText = computed(() => {
  if (isChecking.value) return "检查中...";
  if (checkError.value) return `检查失败：${checkError.value}`;
  if (!updateInfo.value) return "未检查";
  if (updateInfo.value.has_update) {
    return `发现新版本 ${updateInfo.value.latest_version}（当前 ${updateInfo.value.current_version}）`;
  }
  return `已是最新版本（当前 ${updateInfo.value.current_version}）`;
});

const updateStatusType = computed<"info" | "error" | "success" | "update">(
  () => {
    if (isChecking.value) return "info";
    if (checkError.value) return "error";
    if (!updateInfo.value) return "info";
    if (updateInfo.value.has_update) return "update";
    return "success";
  },
);
</script>

<template>
  <div class="settings-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">偏好与资源</p>
        <h1>客户端设置</h1>
        <p>把资源、提醒和更新安排得井井有条。</p>
      </div>
      <span class="local-tag"><SlidersHorizontal :size="14" /> 本机设置</span>
    </header>
    <div class="settings-layout">
      <nav class="settings-nav" aria-label="设置分类">
        <button
          :class="{ active: activeTab === 'capacity' }"
          @click="activeTab = 'capacity'"
        >
          <Gauge :size="18" /><span
            >承载与性能<small>按需测试与资源策略</small></span
          >
        </button>
        <button
          :class="{ active: activeTab === 'runtime' }"
          @click="activeTab = 'runtime'"
        >
          <Zap :size="18" /><span>启动与运行<small>系统启动行为</small></span>
        </button>
        <button
          :class="{ active: activeTab === 'notification' }"
          @click="activeTab = 'notification'"
        >
          <Bell :size="18" /><span>通知与公告<small>管理信息提醒</small></span>
        </button>
        <button
          :class="{ active: activeTab === 'version' }"
          @click="activeTab = 'version'"
        >
          <RefreshCw :size="18" /><span
            >版本更新<small>检查更新与下载</small></span
          >
        </button>
        <p class="nav-note">
          承载设置点击保存后生效；其他偏好自动保存。账号、授权和聊天数据不会随偏好重置而删除。
        </p>
      </nav>
      <div class="settings-main">
        <CapacityPanel v-if="activeTab === 'capacity'" editable />
        <section v-if="activeTab === 'runtime'" class="settings-section">
          <header>
            <h2>启动与运行</h2>
            <p>保持托管稳定，同时尊重你的桌面使用习惯。</p>
          </header>
          <label class="setting-row"
            ><span
              ><b>开机自启动</b><small>登录系统后自动启动客户端</small></span
            ><input
              type="checkbox"
              role="switch"
              :disabled="autoStartBusy || !isTauriEnv()"
              :checked="settings.runtime.auto_start"
              @change="onAutoStartChange"
          /></label>
          <p v-if="!isTauriEnv()" class="note">
            浏览器预览只展示状态，开机自启动请在桌面客户端中设置。
          </p>
          <p
            v-if="autoStartMessage"
            role="status"
            :class="['feedback', autoStartMessageType]"
          >
            {{ autoStartMessage }}
          </p>
          <div class="setting-row">
            <span
              ><b>关闭窗口</b
              ><small>关闭后保持后台托管；完全退出请使用托盘菜单。</small></span
            ><span class="value-tag">保留后台运行</span>
          </div>
          <div class="setting-row">
            <span
              ><b>升级与退出保护</b
              ><small>退出前等待已接收任务结束，防止重复回复。</small></span
            ><span class="value-tag success">已启用</span>
          </div>
        </section>
        <section v-if="activeTab === 'notification'" class="settings-section">
          <header>
            <h2>通知与公告</h2>
            <p>选择需要看到的信息。</p>
          </header>
          <label class="setting-row"
            ><span
              ><b>公告提醒</b
              ><small>接收服务维护、功能变更与版本公告</small></span
            ><input
              type="checkbox"
              role="switch"
              v-model="settings.notification.announcement_enabled"
          /></label>
          <p class="note">公告展示仍受系统通知总开关控制。</p>
          <label class="setting-row"
            ><span
              ><b>通知总开关</b
              ><small
                >管理客户端通知偏好；系统通知还需要操作系统允许。</small
              ></span
            ><input
              type="checkbox"
              role="switch"
              v-model="settings.notification.system_enabled"
          /></label>
        </section>
        <section v-if="activeTab === 'version'" class="settings-section">
          <header>
            <h2>版本更新</h2>
            <p>更新前会等待任务结束，保留本机账号与授权。</p>
          </header>
          <div class="update-box">
            <div>
              <b>{{ updateStatusText }}</b
              ><small v-if="updateInfo?.notes">{{ updateInfo.notes }}</small>
            </div>
            <button
              class="btn-glass"
              :disabled="isChecking"
              @click="handleCheckUpdate"
            >
              <Search :size="15" />{{ isChecking ? "检查中…" : "检查更新" }}
            </button>
          </div>
          <label class="setting-row"
            ><span
              ><b>自动检查新版本</b
              ><small>按照设定频率检查可用更新</small></span
            ><input
              type="checkbox"
              role="switch"
              v-model="settings.version_update.enabled"
          /></label>
          <div class="setting-row">
            <label for="check-frequency"
              ><b>检查频率</b><small>仅在客户端运行期间检查</small></label
            ><select
              id="check-frequency"
              v-model="settings.version_update.check_frequency"
              :disabled="!settings.version_update.enabled"
            >
              <option
                v-for="opt in checkFrequencyOptions"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </div>
          <label class="setting-row"
            ><span
              ><b>自动下载安装</b
              ><small>检测到更新后自动安装并重启，建议在空闲时使用</small></span
            ><input
              type="checkbox"
              role="switch"
              :disabled="!settings.version_update.enabled"
              v-model="settings.version_update.auto_download"
          /></label>
          <button
            v-if="
              updateStatusType === 'update' &&
              (updateInfo?.download_url || updateInfo?.release_page)
            "
            class="btn-glass btn-primary-glass"
            @click="handleOpenDownload"
          >
            <Download :size="15" />打开下载页
          </button>
          <details class="advanced">
            <summary>高级下载设置</summary>
            <label for="update-mirrors">下载镜像（每行一个）</label
            ><textarea
              id="update-mirrors"
              v-model="mirrorsText"
              rows="4"
              spellcheck="false"
              @change="saveMirrors"
            /><button class="btn-glass" @click="resetMirrors">
              恢复默认镜像
            </button>
          </details>
        </section>
        <footer class="reset-section">
          <div>
            <b>恢复偏好设置</b>
            <p>只重置通知与更新偏好，保留系统自启状态和承载设置。</p>
          </div>
          <button class="btn-glass" @click="handleReset">恢复默认</button>
        </footer>
      </div>
    </div>
  </div>
</template>
<style scoped>
.settings-page {
  display: grid;
  gap: 26px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.eyebrow {
  font-size: 12px;
  letter-spacing: 0.08em;
  color: var(--text-muted);
  margin: 0 0 8px;
}
h1 {
  font-size: 26px;
  letter-spacing: -0.7px;
  margin: 0;
}
p {
  color: var(--text-secondary);
  line-height: 1.6;
  font-size: 13px;
  margin: 6px 0 0;
}
.local-tag {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.settings-layout {
  display: grid;
  grid-template-columns: 215px minmax(0, 1fr);
  gap: 28px;
  align-items: start;
}
.settings-nav {
  display: grid;
  gap: 6px;
  position: sticky;
  top: 0;
}
.settings-nav button {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  text-align: left;
  padding: 14px 12px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
}
.settings-nav button.active {
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
}
.settings-nav button:hover {
  background: var(--bg-card);
}
.settings-nav small {
  display: block;
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 5px;
}
.nav-note {
  font-size: 11px;
  padding: 16px 12px;
  line-height: 1.8;
}
.settings-main {
  min-width: 0;
  display: grid;
  gap: 20px;
}
.settings-section {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  padding: 26px;
}
.settings-section header {
  margin-bottom: 14px;
}
h2 {
  font-size: 18px;
  margin: 0;
}
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  padding: 22px 0;
  border-bottom: 1px solid var(--border-subtle);
}
.setting-row:last-child {
  border: 0;
}
.setting-row b,
.setting-row small {
  display: block;
}
.setting-row b {
  font-size: 14px;
  font-weight: 600;
}
.setting-row small {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin-top: 5px;
}
.setting-row input[type="checkbox"] {
  appearance: none;
  width: 38px;
  height: 22px;
  flex: 0 0 38px;
  border-radius: 20px;
  background: var(--border-subtle);
  position: relative;
  cursor: pointer;
}
.setting-row input:checked {
  background: var(--brand-primary);
}
.setting-row input:after {
  content: "";
  position: absolute;
  left: 3px;
  top: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--bg-card);
  transition: transform 0.15s;
}
.setting-row input:checked:after {
  transform: translateX(16px);
}
input:disabled {
  opacity: 0.5;
  cursor: default;
}
.value-tag {
  font-size: 12px;
  white-space: nowrap;
  color: var(--text-secondary);
  background: var(--bg-app);
  padding: 5px 9px;
  border-radius: 6px;
}
.success {
  color: var(--success);
}
select,
textarea {
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--bg-card);
  color: var(--text-primary);
  padding: 10px;
  font: inherit;
  font-size: 13px;
}
.setting-row select {
  max-width: 180px;
}
.update-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px;
  background: var(--bg-app);
  border-radius: 10px;
  margin: 20px 0 4px;
}
.update-box b {
  font-size: 13px;
}
.update-box small {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  margin-top: 8px;
}
.btn-glass {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}
.advanced {
  margin-top: 24px;
  font-size: 13px;
  color: var(--text-secondary);
}
summary {
  cursor: pointer;
}
.advanced label {
  display: block;
  margin: 16px 0 8px;
}
.advanced textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  margin-bottom: 10px;
}
.reset-section {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 0;
  border-top: 1px solid var(--border-subtle);
}
.reset-section b {
  font-size: 13px;
}
.reset-section p,
.note {
  font-size: 12px;
}
.feedback {
  font-size: 12px;
  color: var(--success);
}
.feedback.error {
  color: var(--danger);
}
button:focus-visible,
input:focus-visible,
select:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 3px;
}
@media (max-width: 1100px) {
  .settings-layout {
    grid-template-columns: 1fr;
  }
  .settings-nav {
    position: static;
    grid-template-columns: repeat(2, 1fr);
  }
  .nav-note {
    display: none;
  }
  .settings-section {
    padding: 20px;
  }
  .local-tag {
    display: none;
  }
}
</style>
