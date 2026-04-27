<script lang="ts" setup>
import type {
  DouyinAccount,
  DouyinAccountCreateInput,
} from '#/api/core/douyin';

import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  createDouyinWebSocket,
  type WebSocketManager,
} from '#/api/core/websocket';

import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElPagination,
  ElSelect,
  ElSpace,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTimePicker,
  ElTooltip,
} from 'element-plus';

import {
  batchDeleteDouyinAccountApi,
  createDouyinAccountApi,
  deleteDouyinAccountApi,
  focusDouyinAccountApi,
  getDouyinAccountListApi,
  patchDouyinAccountApi,
  triggerDouyinLoginApi,
  triggerDouyinLogoutApi,
  cancelDouyinLoginApi,
  updateDouyinAccountApi,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinAccount' });

const STATUS_OPTIONS = [
  { label: '未登录', value: 0 },
  { label: '在线', value: 1 },
  { label: '登录失效', value: 2 },
  { label: '已禁用', value: 3 },
];
const router = useRouter();

const STATUS_TAG_TYPE: Record<number, string> = {
  0: 'info',
  1: 'success',
  2: 'warning',
  3: 'danger',
};

const loading = ref(false);
const tableData = ref<DouyinAccount[]>([]);
const selected = ref<DouyinAccount[]>([]);
const pagination = reactive({
  page: 1,
  pageSize: 10,
  total: 0,
});
const searchForm = reactive<{ nickname?: string; status?: number | null }>({
  nickname: '',
  status: null,
});

const dialogVisible = ref(false);
const dialogMode = ref<'create' | 'edit'>('create');
const currentId = ref<string | null>(null);
const formRef = ref();
const form = reactive<DouyinAccountCreateInput>(createEmptyForm());
const autoReplyLoading = reactive<Record<string, boolean>>({});

function createEmptyForm(): DouyinAccountCreateInput {
  return {
    nickname: '',
    status: 0,
    auto_reply_enabled: true,
    daily_reply_quota: 200,
    min_interval_seconds: 8,
    max_interval_seconds: 25,
    silent_start: '22:00:00',
    silent_end: '08:00:00',
    remark: '',
  };
}

const dialogTitle = computed(() =>
  dialogMode.value === 'create' ? '新增抖音账号' : '编辑抖音账号',
);

async function loadData() {
  loading.value = true;
  try {
    const params = {
      page: pagination.page,
      pageSize: pagination.pageSize,
      nickname: searchForm.nickname || undefined,
      status:
        searchForm.status === null || searchForm.status === undefined
          ? undefined
          : searchForm.status,
    };
    const res = await getDouyinAccountListApi(params);
    tableData.value = res.items || [];
    pagination.total = res.total || 0;
  } catch (error) {
    console.error('加载抖音账号列表失败', error);
    ElMessage.error('加载抖音账号列表失败');
  } finally {
    loading.value = false;
  }
}

function onSearch() {
  pagination.page = 1;
  loadData();
}

function onReset() {
  searchForm.nickname = '';
  searchForm.status = null;
  onSearch();
}

function openCreate() {
  dialogMode.value = 'create';
  currentId.value = null;
  Object.assign(form, createEmptyForm());
  dialogVisible.value = true;
}

function openEdit(row: DouyinAccount) {
  dialogMode.value = 'edit';
  currentId.value = row.id;
  Object.assign(form, {
    nickname: row.nickname,
    sec_uid: row.sec_uid ?? '',
    avatar: row.avatar ?? '',
    status: row.status,
    auto_reply_enabled: row.auto_reply_enabled,
    daily_reply_quota: row.daily_reply_quota,
    min_interval_seconds: row.min_interval_seconds,
    max_interval_seconds: row.max_interval_seconds,
    silent_start: row.silent_start,
    silent_end: row.silent_end,
    remark: row.remark ?? '',
  });
  dialogVisible.value = true;
}

async function onSubmit() {
  try {
    await formRef.value?.validate?.();
  } catch {
    return;
  }
  try {
    if (dialogMode.value === 'create') {
      await createDouyinAccountApi(form);
      ElMessage.success('创建成功');
    } else if (currentId.value) {
      await updateDouyinAccountApi(currentId.value, form);
      ElMessage.success('更新成功');
    }
    dialogVisible.value = false;
    loadData();
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error?.response?.data?.detail || '保存失败');
  }
}

async function onDelete(row: DouyinAccount) {
  try {
    await ElMessageBox.confirm(
      `确定要删除账号「${row.nickname}」吗？`,
      '删除',
      { type: 'warning' },
    );
  } catch {
    return;
  }
  try {
    await deleteDouyinAccountApi(row.id);
    ElMessage.success('删除成功');
    loadData();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '删除失败');
  }
}

async function onBatchDelete() {
  if (selected.value.length === 0) {
    ElMessage.warning('请先选择要删除的账号');
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selected.value.length} 个账号吗？`,
      '批量删除',
      { type: 'warning' },
    );
  } catch {
    return;
  }
  try {
    const ids = selected.value.map((r) => r.id);
    const res = await batchDeleteDouyinAccountApi(ids);
    ElMessage.success(
      `成功删除 ${res.count} 个，失败 ${res.failed_ids.length} 个`,
    );
    selected.value = [];
    loadData();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '批量删除失败');
  }
}

async function onToggleAutoReply(row: DouyinAccount, enabled: boolean) {
  const prev = row.auto_reply_enabled;
  row.auto_reply_enabled = enabled;
  autoReplyLoading[row.id] = true;
  try {
    await patchDouyinAccountApi(row.id, { auto_reply_enabled: enabled });
    ElMessage.success(enabled ? '已开启自动回复' : '已关闭自动回复');
  } catch (error: any) {
    row.auto_reply_enabled = prev;
    ElMessage.error(error?.response?.data?.detail || '更新自动回复开关失败');
  } finally {
    autoReplyLoading[row.id] = false;
  }
}

function openReplyHistory(row: DouyinAccount) {
  router.push({
    path: '/douyin/reply-log',
    query: {
      account_id: row.id,
    },
  });
}

// ---------------- 扫码登录弹窗 ----------------
const qrDialogVisible = ref(false);
const qrImage = ref('');
const qrHint = ref('正在打开监管页面，请稍候…');
const qrStage = ref<'supervise' | 'verification'>('supervise');
const qrAccountName = ref('');
const qrLoading = ref(false);
const loginPendingAccountId = ref<string>('');
let douyinWs: null | WebSocketManager = null;
let supervisionWindow: null | Window = null;
const isLoginFlowActive = computed(() => !!loginPendingAccountId.value);
const supervisionUrl = computed(() => {
  const url = new URL(window.location.href);
  url.protocol = 'http:';
  url.port = '6080';
  url.pathname = '/vnc.html';
  url.search = 'autoconnect=1&resize=scale&path=websockify';
  url.hash = '';
  return url.toString();
});
const qrFootnote = computed(() =>
  qrStage.value === 'verification'
    ? '检测到安全校验，请在浏览器窗口中手动完成验证；完成后保持页面打开等待系统自动放行'
    : '系统将打开监管页面并自动连线到 Docker 内浏览器；请在该页面完成扫码和验证码操作',
);

function openSupervisionPage(accountId?: string) {
  const targetAccountId = accountId || loginPendingAccountId.value || '';
  supervisionWindow = window.open(
    supervisionUrl.value,
    `douyin-supervision-${targetAccountId || 'pending'}`,
  );
  if (targetAccountId) {
    void focusAccountPage(targetAccountId);
  }
}

async function focusAccountPage(accountId: string) {
  try {
    await focusDouyinAccountApi(accountId);
  } catch (error) {
    console.error('聚焦账号监管页失败', error);
  }
}

function openSupervisionPageDirect(row: DouyinAccount) {
  openSupervisionPage(row.id);
}

async function ensureWsConnected() {
  if (douyinWs && douyinWs.isConnected) return;
  if (douyinWs) douyinWs.close();
  douyinWs = createDouyinWebSocket({
    onMessage: handleDouyinEvent,
    onClose: () => {
      // 重连由管理器自动处理
    },
  });
  try {
    await douyinWs.connect();
    douyinWs.send({ type: 'subscribe' });
  } catch (error) {
    console.error('连接抖音 WS 失败', error);
    ElMessage.error('连接实时推送通道失败，请刷新页面重试');
  }
}

function handleDouyinEvent(message: any) {
  // 后端 DouyinConsumer 会把事件以 send_message(event, ...) 包装，
  // 结构形如 { type: 'qr_image', data: { event, payload, ... } }
  const eventType = message?.type || message?.data?.event;
  const payload = message?.data?.payload || message?.data || {};
  switch (eventType) {
    case 'qr_image': {
      qrLoading.value = false;
      qrImage.value = '';
      qrHint.value =
        '监管页中的 Docker 浏览器已打开登录页，请直接在监管页完成扫码，不再在后台弹窗内展示二维码。';
      break;
    }
    case 'verification_required': {
      qrDialogVisible.value = true;
      qrLoading.value = false;
      qrStage.value = 'verification';
      if (payload.image_base64) {
        qrImage.value = `data:image/jpeg;base64,${payload.image_base64}`;
      }
      const title = payload.title ? `页面：${payload.title}` : '';
      const text = payload.text_excerpt ? `线索：${payload.text_excerpt}` : '';
      qrHint.value = [payload.hint, title, text].filter(Boolean).join(' ');
      ElMessage.warning(
        `检测到${payload.kind_label || '安全验证'}，请在浏览器窗口中手动完成后等待自动登录`,
      );
      break;
    }
    case 'login_success': {
      ElMessage.success(`账号 ${payload.nickname || ''} 登录成功`);
      qrDialogVisible.value = false;
      loginPendingAccountId.value = '';
      supervisionWindow?.close();
      supervisionWindow = null;
      loadData();
      break;
    }
    case 'login_failed': {
      const reason = payload.reason || '未知错误';
      ElMessage.error(`登录失败：${reason}`);
      if (reason.includes('弱登录态') || reason.includes('风控')) {
        qrHint.value =
          '登录失败：当前被抖音风控拦截（仅弱登录态）。请等待 10-30 分钟后重试，避免频繁点登录。';
      } else {
        qrHint.value = `登录失败：${reason}`;
      }
      loginPendingAccountId.value = '';
      supervisionWindow?.close();
      supervisionWindow = null;
      break;
    }
    case 'login_progress': {
      const elapsed = Number(payload.elapsed || 0);
      const remain = Number(payload.remain || 0);
      const cookies = Array.isArray(payload.session_cookies)
        ? payload.session_cookies
        : [];
      const cookiesText = cookies.length > 0 ? cookies.join(', ') : '无';
      qrHint.value =
        payload.status_hint ||
        `等待扫码确认中… 已等待 ${elapsed}s，剩余 ${remain}s，cookie=${cookiesText}`;
      break;
    }
    case 'reply_sent': {
      ElMessage.info(
        `已回复 ${payload.peer_nickname || ''}（规则：${payload.rule || '-'}）`,
      );
      break;
    }
  }
}

async function onLogin(row: DouyinAccount) {
  if (isLoginFlowActive.value) {
    ElMessage.warning('已有扫码流程进行中，请先完成当前流程');
    return;
  }
  loginPendingAccountId.value = row.id;
  qrAccountName.value = row.nickname;
  qrImage.value = '';
  qrHint.value = '正在打开监管页面，请稍候…';
  qrStage.value = 'supervise';
  qrLoading.value = true;
  qrDialogVisible.value = true;
  try {
    openSupervisionPage(row.id);
    await ensureWsConnected();
    const res = await triggerDouyinLoginApi(row.id);
    ElMessage.success(res.message || '已下发扫码登录指令');
    qrLoading.value = false;
    qrHint.value =
      '请在新打开的监管页面中操作 Docker 浏览器完成扫码/验证码；本页将实时显示登录状态。';
    loadData();
  } catch (error: any) {
    qrDialogVisible.value = false;
    loginPendingAccountId.value = '';
    supervisionWindow?.close();
    supervisionWindow = null;
    ElMessage.error(error?.response?.data?.detail || '登录指令下发失败');
  }
}

async function onCloseQrDialog() {
  const accountId = loginPendingAccountId.value;
  qrDialogVisible.value = false;
  qrImage.value = '';
  qrStage.value = 'supervise';
  loginPendingAccountId.value = '';
  supervisionWindow?.close();
  supervisionWindow = null;
  if (accountId) {
    try {
      await cancelDouyinLoginApi(accountId);
    } catch (error) {
      console.error('取消扫码登录失败', error);
    }
  }
}

onBeforeUnmount(() => {
  supervisionWindow?.close();
  supervisionWindow = null;
  if (douyinWs) {
    douyinWs.close();
    douyinWs = null;
  }
});

async function onLogout(row: DouyinAccount) {
  if (isLoginFlowActive.value) {
    ElMessage.warning('扫码登录进行中，暂不可登出');
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确定要登出账号「${row.nickname}」吗？`,
      '登出',
      { type: 'warning' },
    );
  } catch {
    return;
  }
  try {
    const res = await triggerDouyinLogoutApi(row.id);
    ElMessage.success(res.message || '已下发登出指令');
    loadData();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '登出失败');
  }
}

function onSelectionChange(rows: DouyinAccount[]) {
  selected.value = rows;
}

function onPageChange(page: number) {
  pagination.page = page;
  loadData();
}

function onSizeChange(size: number) {
  pagination.pageSize = size;
  pagination.page = 1;
  loadData();
}

onMounted(loadData);
</script>

<template>
  <Page auto-content-height>
    <div class="p-4">
      <!-- 搜索 -->
      <div class="mb-4 rounded bg-white p-3 shadow-sm dark:bg-gray-800">
        <ElForm :inline="true" :model="searchForm">
          <ElFormItem label="昵称">
            <ElInput
              v-model="searchForm.nickname"
              placeholder="昵称模糊匹配"
              clearable
              style="width: 200px"
              @keyup.enter="onSearch"
            />
          </ElFormItem>
          <ElFormItem label="状态">
            <ElSelect
              v-model="searchForm.status"
              placeholder="全部"
              clearable
              style="width: 160px"
            >
              <ElOption
                v-for="item in STATUS_OPTIONS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </ElSelect>
          </ElFormItem>
          <ElFormItem>
            <ElButton type="primary" @click="onSearch">查询</ElButton>
            <ElButton @click="onReset">重置</ElButton>
          </ElFormItem>
        </ElForm>
      </div>

      <!-- 操作区 + 表格 -->
      <div class="rounded bg-white p-3 shadow-sm dark:bg-gray-800">
      <div class="mb-3 flex items-center justify-between">
        <ElSpace>
          <ElButton type="primary" @click="openCreate">新增账号</ElButton>
            <ElButton
              type="danger"
              plain
              :disabled="selected.length === 0"
              @click="onBatchDelete"
            >
              批量删除{{ selected.length > 0 ? `(${selected.length})` : '' }}
            </ElButton>
          </ElSpace>
          <ElTooltip
            content="扫码登录走 docker 内 worker；监管页会打开当前账号对应的 Docker 浏览器窗口"
            placement="top"
          >
            <ElTag type="info">提示：请在对应账号的监管页中完成扫码和验证</ElTag>
          </ElTooltip>
        </div>

        <ElTable
          v-loading="loading"
          :data="tableData"
          border
          stripe
          height="calc(100vh - 320px)"
          @selection-change="onSelectionChange"
        >
          <ElTableColumn type="selection" width="44" />
          <ElTableColumn prop="nickname" label="昵称" min-width="140" />
          <ElTableColumn label="状态" width="110">
            <template #default="{ row }">
              <ElTag :type="STATUS_TAG_TYPE[row.status] as any">
                {{ row.status_display }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="自动回复" width="110" align="center">
            <template #default="{ row }">
              <ElSwitch
                :model-value="row.auto_reply_enabled"
                :loading="!!autoReplyLoading[row.id]"
                inline-prompt
                active-text="开"
                inactive-text="关"
                @change="(value) => onToggleAutoReply(row, Boolean(value))"
              />
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="owner_name"
            label="所属用户"
            width="140"
            show-overflow-tooltip
          />
          <ElTableColumn
            prop="daily_reply_quota"
            label="日回复上限"
            width="110"
          />
          <ElTableColumn label="回复间隔（秒）" width="130">
            <template #default="{ row }">
              {{ row.min_interval_seconds }} ~ {{ row.max_interval_seconds }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="静默时段" width="140">
            <template #default="{ row }">
              {{ row.silent_start }} ~ {{ row.silent_end }}
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="last_heartbeat"
            label="最近心跳"
            width="170"
            show-overflow-tooltip
          />
          <ElTableColumn
            prop="remark"
            label="备注"
            min-width="160"
            show-overflow-tooltip
          />
          <ElTableColumn label="操作" width="430" fixed="right">
            <template #default="{ row }">
              <ElButton
                link
                type="info"
                size="small"
                @click="openSupervisionPageDirect(row)"
              >
                监管页
              </ElButton>
              <ElButton
                link
                type="primary"
                size="small"
                :disabled="isLoginFlowActive"
                @click="onLogin(row)"
              >
                {{ isLoginFlowActive ? '登录中…' : '扫码登录' }}
              </ElButton>
              <ElButton
                link
                type="warning"
                size="small"
                :disabled="row.status !== 1 || isLoginFlowActive"
                @click="onLogout(row)"
              >
                登出
              </ElButton>
              <ElButton
                link
                type="success"
                size="small"
                @click="openReplyHistory(row)"
              >
                回复历史
              </ElButton>
              <ElButton
                link
                type="primary"
                size="small"
                @click="openEdit(row)"
              >
                编辑
              </ElButton>
              <ElButton
                link
                type="danger"
                size="small"
                :disabled="row.status === 1"
                @click="onDelete(row)"
              >
                删除
              </ElButton>
            </template>
          </ElTableColumn>
        </ElTable>

        <div class="mt-3 flex justify-end">
          <ElPagination
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.pageSize"
            :total="pagination.total"
            :page-sizes="[10, 20, 50, 100]"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="onPageChange"
            @size-change="onSizeChange"
          />
        </div>
      </div>

      <!-- 新增 / 编辑 弹窗（自定义遮罩 Drawer 风格，简化版用内置 ElDialog 替代） -->
      <ElDialog
        v-model="dialogVisible"
        :title="dialogTitle"
        width="560px"
        destroy-on-close
      >
        <ElForm
          ref="formRef"
          :model="form"
          label-width="110px"
          :rules="{
            nickname: [
              { required: true, message: '请输入抖音昵称', trigger: 'blur' },
            ],
          }"
        >
          <ElFormItem label="昵称" prop="nickname">
            <ElInput v-model="form.nickname" placeholder="抖音昵称" />
          </ElFormItem>
          <ElFormItem label="状态">
            <ElSelect v-model="form.status" placeholder="状态">
              <ElOption
                v-for="item in STATUS_OPTIONS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </ElSelect>
          </ElFormItem>
          <ElFormItem label="自动回复">
            <ElSwitch v-model="form.auto_reply_enabled" />
          </ElFormItem>
          <ElFormItem label="日回复上限">
            <ElInputNumber
              v-model="form.daily_reply_quota"
              :min="0"
              :max="10000"
            />
          </ElFormItem>
          <ElFormItem label="回复间隔（秒）">
            <div class="flex items-center gap-2">
              <ElInputNumber
                v-model="form.min_interval_seconds"
                :min="1"
                :max="600"
              />
              <span>~</span>
              <ElInputNumber
                v-model="form.max_interval_seconds"
                :min="1"
                :max="600"
              />
            </div>
          </ElFormItem>
          <ElFormItem label="静默时段">
            <div class="flex items-center gap-2">
              <ElTimePicker
                v-model="form.silent_start"
                value-format="HH:mm:ss"
                format="HH:mm"
                placeholder="开始"
              />
              <span>~</span>
              <ElTimePicker
                v-model="form.silent_end"
                value-format="HH:mm:ss"
                format="HH:mm"
                placeholder="结束"
              />
            </div>
          </ElFormItem>
          <ElFormItem label="备注">
            <ElInput
              v-model="form.remark"
              type="textarea"
              :rows="3"
              placeholder="可选"
            />
          </ElFormItem>
        </ElForm>
        <template #footer>
          <ElButton @click="dialogVisible = false">取消</ElButton>
          <ElButton type="primary" @click="onSubmit">确定</ElButton>
        </template>
      </ElDialog>

      <!-- 监管式扫码登录弹窗：引导用户前往 noVNC 监管页完成登录 -->
      <ElDialog
        v-model="qrDialogVisible"
        :title="`监管登录 · ${qrAccountName}`"
        width="420px"
        :close-on-click-modal="false"
        destroy-on-close
        @close="onCloseQrDialog"
      >
        <div class="flex flex-col items-center gap-3 py-2">
          <div
            v-loading="qrLoading"
            class="flex min-h-40 w-full items-center justify-center rounded border bg-white px-4 py-6"
          >
            <div class="space-y-3 text-center">
              <div class="text-sm text-gray-500">
                {{ qrHint }}
              </div>
              <div class="text-xs text-gray-400 break-all">
                {{ supervisionUrl }}
              </div>
              <ElButton type="primary" @click="() => openSupervisionPage()">
                打开监管页面
              </ElButton>
            </div>
          </div>
          <div class="text-xs text-gray-400">
            {{ qrFootnote }}
          </div>
        </div>
        <template #footer>
          <ElButton @click="onCloseQrDialog">关闭</ElButton>
        </template>
      </ElDialog>
    </div>
  </Page>
</template>
