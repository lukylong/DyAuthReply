<script lang="ts" setup>
import type {
  DiagnosticDevice,
  DiagnosticDeviceDetail,
  DiagnosticEvent,
} from '#/api/core/license-diagnostics';
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { Page } from '@vben/common-ui';
import {
  ElAlert,
  ElButton,
  ElCard,
  ElDrawer,
  ElInput,
  ElOption,
  ElPagination,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';
import {
  getDiagnosticDeviceApi,
  getDiagnosticDevicesApi,
  getDiagnosticEventsApi,
} from '#/api/core/license-diagnostics';
import {
  accountIssue,
  automaticView,
  eventFields,
  eventAdvice,
  eventCategory,
  traceRef,
  eventMessage,
  formatReportedAt,
  metric,
  stateView,
  summarizeDevice,
} from './presentation';

defineOptions({ name: 'LicenseDiagnostics' });
const loading = ref(false),
  loadError = ref(''),
  now = ref(Date.now());
const rows = ref<DiagnosticDevice[]>([]);
const filters = reactive({ app_version: '', os: '' });
const pagination = reactive({ page: 1, pageSize: 10, total: 0 });
const drawerOpen = ref(false),
  detailLoading = ref(false),
  detailError = ref('');
const detail = ref<DiagnosticDeviceDetail | null>(null);
const events = ref<DiagnosticEvent[]>([]),
  eventsLoading = ref(false),
  eventError = ref('');
const eventFilter = reactive({
  level: '',
  account: '',
  trace_id: '',
  trigger_type: '',
});
const eventPage = reactive({ page: 1, pageSize: 20, total: 0 });
let listRequest = 0,
  detailRequest = 0,
  eventRequest = 0;
let refreshTimer: ReturnType<typeof setInterval> | undefined;
const presented = computed(() =>
  rows.value.map((row) => ({
    ...row,
    health: summarizeDevice(row, now.value),
  })),
);
const currentHealth = computed(() =>
  detail.value ? summarizeDevice(detail.value, now.value) : null,
);
const totals = computed(() => ({
  normal: presented.value.filter((r) => r.health.tone === 'success').length,
  attention: presented.value.filter((r) =>
    ['warning', 'danger'].includes(r.health.tone),
  ).length,
  receiving: presented.value
    .filter((r) => !r.health.freshness.stale)
    .reduce((n, r) => n + r.health.receiving, 0),
}));
async function loadData() {
  const request = ++listRequest;
  loading.value = true;
  try {
    const res = await getDiagnosticDevicesApi({
      ...filters,
      page: pagination.page,
      pageSize: pagination.pageSize,
    });
    if (request !== listRequest) return;
    rows.value = res.items || [];
    pagination.total = res.total || 0;
    loadError.value = '';
    now.value = Date.now();
  } catch {
    if (request === listRequest)
      loadError.value = '读取诊断失败，当前显示的是上次加载的数据，请重试。';
  } finally {
    if (request === listRequest) loading.value = false;
  }
}
async function loadEvents() {
  if (!detail.value) return;
  const id = detail.value.device_id,
    request = ++eventRequest;
  const account = eventFilter.account.trim(),
    trace = eventFilter.trace_id.trim();
  if (
    (account && !/^[0-9a-f]{12}$/.test(account)) ||
    (trace && !/^[0-9a-f]{16}$/.test(trace))
  ) {
    eventError.value =
      '请输入 12 位匿名账号或 16 位回复关联号（小写十六进制）。';
    eventsLoading.value = false;
    events.value = [];
    eventPage.total = 0;
    return;
  }
  eventsLoading.value = true;
  try {
    const res = await getDiagnosticEventsApi(id, {
      ...eventFilter,
      account: eventFilter.account.trim(),
      trace_id: eventFilter.trace_id.trim(),
      page: eventPage.page,
      pageSize: eventPage.pageSize,
    });
    if (request !== eventRequest || detail.value?.device_id !== id) return;
    events.value = res.items || [];
    eventPage.total = res.total || 0;
    eventError.value = '';
  } catch {
    if (request === eventRequest)
      eventError.value = '事件读取失败，请重新刷新设备详情。';
  } finally {
    if (request === eventRequest) eventsLoading.value = false;
  }
}
async function openDevice(row: DiagnosticDevice) {
  if (detail.value?.device_id !== row.device_id)
    Object.assign(eventFilter, {
      level: '',
      account: '',
      trace_id: '',
      trigger_type: '',
    });
  const request = ++detailRequest;
  ++eventRequest;
  drawerOpen.value = true;
  detailLoading.value = true;
  detailError.value = '';
  detail.value = null;
  events.value = [];
  eventError.value = '';
  eventsLoading.value = false;
  eventPage.total = 0;
  try {
    const res = await getDiagnosticDeviceApi(row.device_id);
    if (request !== detailRequest) return;
    detail.value = res;
    now.value = Date.now();
    eventPage.page = 1;
    await loadEvents();
  } catch {
    if (request === detailRequest)
      detailError.value = '设备详情读取失败，请关闭后重试。';
  } finally {
    if (request === detailRequest) detailLoading.value = false;
  }
}
function searchEvents() {
  eventPage.page = 1;
  void loadEvents();
}
function showTrace(row: DiagnosticEvent) {
  Object.assign(eventFilter, {
    level: '',
    trigger_type: '',
    account: String(row.fields?.account || ''),
    trace_id: traceRef(row),
  });
  searchEvents();
}
function clearEventFilters() {
  Object.assign(eventFilter, {
    level: '',
    trigger_type: '',
    account: '',
    trace_id: '',
  });
  searchEvents();
}
function search() {
  pagination.page = 1;
  void loadData();
}
function levelType(level: string) {
  return level === 'ERROR' ? 'danger' : level === 'WARN' ? 'warning' : 'info';
}
function levelLabel(level: string) {
  return level === 'ERROR' ? '错误' : level === 'WARN' ? '告警' : '运行事件';
}
function shortId(value: string) {
  return value?.length > 12 ? `${value.slice(0, 12)}…` : value || '未标识';
}
onMounted(() => {
  void loadData();
  refreshTimer = setInterval(() => {
    now.value = Date.now();
    if (!loading.value && document.visibilityState === 'visible')
      void loadData();
  }, 30_000);
});
onUnmounted(() => {
  clearInterval(refreshTimer);
  ++listRequest;
  ++detailRequest;
  ++eventRequest;
});
</script>

<template>
  <Page auto-content-height>
    <ElCard shadow="never">
      <div class="heading">
        <div>
          <h2>客户端运行诊断</h2>
          <p class="muted">
            先看是否仍在上报，再看账号接收、发送和自动回复状态。
          </p>
        </div>
        <ElButton :loading="loading" @click="loadData">刷新状态</ElButton>
      </div>
      <ElAlert
        v-if="loadError"
        :title="loadError"
        type="error"
        :closable="false"
        class="mb-3"
      />
      <div class="summary-grid">
        <div>
          <span>诊断设备</span><b>{{ pagination.total }}</b
          ><small>有过上报记录的设备</small>
        </div>
        <div>
          <span>本页运行正常</span><b>{{ totals.normal }}</b
          ><small>最近快照正常，不代表每条消息均送达</small>
        </div>
        <div>
          <span>本页需要关注</span><b>{{ totals.attention }}</b
          ><small>含状态异常或上报长时间未更新</small>
        </div>
        <div>
          <span>本页正常接收账号</span><b>{{ totals.receiving }}</b
          ><small>仅统计上报未过期的账号</small>
        </div>
      </div>
      <p class="notice">
        新版客户端默认开启，可在「设置 →
        运行诊断」关闭；已关闭的选择会保留。约每15分钟上报，列表每30秒刷新，详情可手动刷新；超过35分钟未上报显示“状态待更新”，不直接判定账号掉线。数据脱敏，不含私信正文、昵称、抖音账号标识或凭证；保留30天。
      </p>
      <div class="filters">
        <ElInput
          v-model="filters.app_version"
          placeholder="客户端版本"
          clearable
          style="width: 180px"
          @keyup.enter="search"
        />
        <ElSelect
          v-model="filters.os"
          placeholder="操作系统"
          clearable
          style="width: 140px"
          ><ElOption label="Windows" value="windows" /><ElOption
            label="macOS"
            value="macos"
        /></ElSelect>
        <ElButton type="primary" @click="search">查询</ElButton>
      </div>
      <ElTable
        :data="presented"
        v-loading="loading"
        border
        @row-click="openDevice"
        empty-text="暂无诊断上报，请检查客户端版本、授权和诊断开关"
      >
        <ElTableColumn label="设备 / 版本" min-width="190"
          ><template #default="{ row }"
            ><b>{{ row.device_name || '未命名设备' }}</b>
            <p class="muted">
              {{ row.app_version || '未知版本' }} ·
              {{
                row.os === 'macos'
                  ? 'macOS'
                  : row.os === 'windows'
                    ? 'Windows'
                    : row.os || '未知系统'
              }}
              / {{ row.arch || '—' }}
            </p>
            <small class="muted">{{
              shortId(row.device_fingerprint)
            }}</small></template
          ></ElTableColumn
        >
        <ElTableColumn label="运行状况" min-width="230"
          ><template #default="{ row }"
            ><ElTag :type="row.health.tone">{{ row.health.label }}</ElTag>
            <p class="muted">{{ row.health.advice }}</p></template
          ></ElTableColumn
        >
        <ElTableColumn label="账号概况（最近快照）" min-width="230"
          ><template #default="{ row }"
            ><div>
              托管 {{ row.health.total }} · 正常接收 {{ row.health.receiving }}
            </div>
            <div>
              发送已验证 {{ row.health.sending }} · 待验证/受限
              {{ row.health.pending }}
            </div>
            <div>已启用且收发正常 {{ row.health.automatic }}</div></template
          ></ElTableColumn
        >
        <ElTableColumn label="24小时历史事件" min-width="155"
          ><template #default="{ row }"
            ><ElTag :type="row.error_24h ? 'danger' : 'info'" size="small"
              >错误 {{ row.error_24h }}</ElTag
            >
            <ElTag :type="row.warn_24h ? 'warning' : 'info'" size="small"
              >告警 {{ row.warn_24h }}</ElTag
            >
            <p class="muted">历史次数，不等于当前故障</p></template
          ></ElTableColumn
        >
        <ElTableColumn label="最近上报" min-width="180"
          ><template #default="{ row }"
            ><span>{{ formatReportedAt(row.last_report_at) }}</span>
            <p :class="row.health.freshness.stale ? 'stale' : 'muted'">
              {{ row.health.freshness.text }}
            </p></template
          ></ElTableColumn
        >
        <ElTableColumn label="详情" width="100"
          ><template #default="{ row }"
            ><ElButton link type="primary" @click.stop="openDevice(row)"
              >查看诊断</ElButton
            ></template
          ></ElTableColumn
        >
      </ElTable>
      <div class="pager">
        <ElPagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          :total="pagination.total"
          @current-change="loadData"
          @size-change="loadData"
        />
      </div>
    </ElCard>
    <ElDrawer
      v-model="drawerOpen"
      :title="detail?.device_name || '设备诊断'"
      size="75%"
    >
      <div v-loading="detailLoading">
        <ElAlert
          v-if="detailError"
          :title="detailError"
          type="error"
          :closable="false"
        />
        <template v-if="detail && currentHealth">
          <div class="heading">
            <ElTag :type="currentHealth.tone">{{ currentHealth.label }}</ElTag
            ><ElButton size="small" @click="openDevice(detail)"
              >刷新详情</ElButton
            >
          </div>
          <p>{{ currentHealth.advice }}</p>
          <p class="muted">
            版本 {{ detail.app_version }} · {{ detail.os }}/{{ detail.arch }} ·
            累计上报 {{ detail.report_count }} 次 ·
            {{ formatReportedAt(detail.last_report_at) }}（{{
              currentHealth.freshness.text
            }}）
          </p>
          <div class="summary-grid metrics">
            <div>
              <span>待处理队列</span
              ><b>{{ metric(detail.runtime?.queue_depth) }}</b
              ><small>当前等待处理的工作项</small>
            </div>
            <div>
              <span>依赖保护中</span
              ><b>{{ metric(detail.runtime?.open_circuit_count) }}</b
              ><small>临时暂停失败依赖，等待恢复</small>
            </div>
            <div>
              <span>存储状态</span
              ><b class="word">{{ stateView(detail.storage_pressure).label }}</b
              ><small>不包含数据库或私信正文</small>
            </div>
            <div>
              <span>协议自检</span
              ><b class="word">{{
                detail.protocol_parity == null
                  ? '尚无数据'
                  : detail.protocol_parity
                    ? '通过'
                    : '未通过'
              }}</b
              ><small>协议回归通过不等于实际送达</small>
            </div>
          </div>
          <h3>账号状态</h3>
          <ElTable
            :data="detail.accounts"
            border
            size="small"
            empty-text="此快照没有托管账号"
          >
            <ElTableColumn label="匿名账号" min-width="155"
              ><template #default="{ row }"
                ><code>{{ row.account }}</code></template
              ></ElTableColumn
            >
            <ElTableColumn label="身份 / 授权" min-width="150"
              ><template #default="{ row }"
                ><ElTag
                  size="small"
                  :type="row.identity_verified === true ? 'success' : 'warning'"
                  >{{
                    row.identity_verified === true ? '身份已验证' : '身份待验证'
                  }}</ElTag
                >
                <p>{{ stateView(row.ownership).label }}</p></template
              ></ElTableColumn
            >
            <ElTableColumn label="消息接收" min-width="145"
              ><template #default="{ row }"
                ><ElTag size="small" :type="stateView(row.inbound).tone">{{
                  stateView(row.inbound).label
                }}</ElTag></template
              ></ElTableColumn
            >
            <ElTableColumn label="消息发送" min-width="145"
              ><template #default="{ row }"
                ><ElTag size="small" :type="stateView(row.send).tone">{{
                  stateView(row.send).label
                }}</ElTag></template
              ></ElTableColumn
            >
            <ElTableColumn label="自动回复" min-width="155"
              ><template #default="{ row }">{{
                automaticView(row).label
              }}</template></ElTableColumn
            >
            <ElTableColumn label="说明 / 建议" min-width="235"
              ><template #default="{ row }">{{
                accountIssue(row)
              }}</template></ElTableColumn
            >
          </ElTable>
          <p class="muted">
            累计队列拒绝 {{ metric(detail.runtime?.queue_rejected) }} 次 ·
            累计签名失败
            {{ metric(detail.runtime?.signer_failed) }}
            次；累计计数不表示此刻仍在失败。
          </p>
          <h3>运行事件</h3>
          <div class="filters">
            <ElSelect
              v-model="eventFilter.level"
              placeholder="全部级别"
              clearable
              style="width: 140px"
              @change="
                eventPage.page = 1;
                loadEvents();
              "
              ><ElOption label="错误" value="ERROR" /><ElOption
                label="告警"
                value="WARN" /><ElOption label="运行事件" value="INFO"
            /></ElSelect>
            <ElSelect
              v-model="eventFilter.trigger_type"
              placeholder="触发类型"
              clearable
              style="width: 170px"
              @change="searchEvents"
            >
              <ElOption label="回关/互关触发" value="mutual_follow" />
              <ElOption label="普通消息触发" value="message" />
            </ElSelect>
            <ElInput
              v-model="eventFilter.account"
              placeholder="匿名账号（12位）"
              clearable
              style="width: 180px"
              @keyup.enter="searchEvents"
            />
            <ElInput
              v-model="eventFilter.trace_id"
              placeholder="回复关联号（16位）"
              clearable
              style="width: 220px"
              @keyup.enter="searchEvents"
            />
            <ElButton @click="searchEvents">筛选事件</ElButton>
            <ElButton @click="clearEventFilters">清空筛选</ElButton>
          </div>
          <p class="muted">
            触发类型筛选只显示触发事件；点击关联号查看同批发送与停止原因。旧日志未记录的来源或关联号不作推断，事件条数不等于回复成功率。
          </p>
          <ElAlert
            v-if="eventError"
            :title="eventError"
            type="error"
            :closable="false"
          />
          <ElTable
            :data="events"
            v-loading="eventsLoading"
            border
            size="small"
            empty-text="当前筛选范围没有诊断事件"
          >
            <ElTableColumn label="发生时间" min-width="175"
              ><template #default="{ row }"
                >{{ formatReportedAt(row.occurred_at) }}
                <p class="muted">
                  收到：{{ formatReportedAt(row.received_at) }}
                </p>
              </template></ElTableColumn
            >
            <ElTableColumn label="级别 / 模块" width="130"
              ><template #default="{ row }"
                ><ElTag size="small" :type="levelType(row.level)">{{
                  levelLabel(row.level)
                }}</ElTag>
                <p>{{ eventCategory(row) }}</p></template
              ></ElTableColumn
            >
            <ElTableColumn label="事件说明" min-width="260"
              ><template #default="{ row }"
                ><b>{{ eventMessage(row) }}</b>
                <p class="muted">{{ eventFields(row) }}</p>
                <p v-if="eventAdvice(row)" class="event-advice">
                  {{ eventAdvice(row) }}
                </p>
                <ElButton
                  v-if="traceRef(row)"
                  link
                  type="primary"
                  @click="showTrace(row)"
                  >查看整批 · {{ traceRef(row) }}</ElButton
                >
                <span v-else class="muted">未提供回复关联号</span>
                <details>
                  <summary>技术原文</summary>
                  <pre class="payload">{{
                    [
                      row.message,
                      row.target,
                      JSON.stringify(row.fields, null, 2),
                    ].join('\n')
                  }}</pre>
                </details></template
              ></ElTableColumn
            >
          </ElTable>
          <div class="pager">
            <ElPagination
              v-model:current-page="eventPage.page"
              v-model:page-size="eventPage.pageSize"
              layout="total, prev, pager, next"
              :total="eventPage.total"
              @current-change="loadEvents"
            />
          </div>
          <details class="technical">
            <summary>查看完整脱敏快照（技术排查）</summary>
            <pre class="payload">{{
              JSON.stringify(detail.snapshot, null, 2)
            }}</pre>
          </details>
        </template>
      </div>
    </ElDrawer>
  </Page>
</template>
<style scoped>
.heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
h2 {
  font-size: 20px;
  font-weight: 600;
}
h3 {
  margin: 22px 0 12px;
  font-size: 16px;
  font-weight: 600;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}
.summary-grid > div {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 15px;
  background: var(--el-fill-color-light);
}
.summary-grid span,
.summary-grid small {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.summary-grid b {
  display: block;
  font-size: 25px;
  margin: 5px 0;
}
.summary-grid .word {
  font-size: 18px;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.notice,
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.notice {
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.stale {
  color: var(--el-color-warning);
  font-size: 12px;
}
.payload {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  margin: 8px 0;
}
.technical {
  margin-top: 24px;
}
summary {
  cursor: pointer;
  color: var(--el-color-primary);
  font-size: 12px;
}
@media (max-width: 900px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
