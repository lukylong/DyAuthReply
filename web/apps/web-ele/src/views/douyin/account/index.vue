<script lang="ts" setup>
import type {
  DouyinAccount,
  DouyinAccountCreateInput,
} from '#/api/core/douyin';

import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';

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
  getDouyinAccountListApi,
  triggerDouyinLoginApi,
  triggerDouyinLogoutApi,
  updateDouyinAccountApi,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinAccount' });

const STATUS_OPTIONS = [
  { label: '未登录', value: 0 },
  { label: '在线', value: 1 },
  { label: '登录失效', value: 2 },
  { label: '已禁用', value: 3 },
];

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

function createEmptyForm(): DouyinAccountCreateInput {
  return {
    nickname: '',
    status: 0,
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

// ---------------- 扫码登录弹窗 ----------------
const qrDialogVisible = ref(false);
const qrImage = ref('');
const qrHint = ref('正在生成二维码，请稍候…');
const qrAccountName = ref('');
const qrLoading = ref(false);
let douyinWs: null | WebSocketManager = null;

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
      if (payload.image_base64) {
        qrImage.value = `data:image/png;base64,${payload.image_base64}`;
        qrHint.value = payload.hint || '请使用抖音 APP 扫码登录';
        qrLoading.value = false;
      }
      break;
    }
    case 'login_success': {
      ElMessage.success(`账号 ${payload.nickname || ''} 登录成功`);
      qrDialogVisible.value = false;
      loadData();
      break;
    }
    case 'login_failed': {
      ElMessage.error(`登录失败：${payload.reason || '未知错误'}`);
      qrHint.value = `登录失败：${payload.reason || '未知错误'}`;
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
  qrAccountName.value = row.nickname;
  qrImage.value = '';
  qrHint.value = '正在生成二维码，请稍候…';
  qrLoading.value = true;
  qrDialogVisible.value = true;
  try {
    await ensureWsConnected();
    const res = await triggerDouyinLoginApi(row.id);
    ElMessage.success(res.message || '已下发扫码登录指令');
    loadData();
  } catch (error: any) {
    qrDialogVisible.value = false;
    ElMessage.error(error?.response?.data?.detail || '登录指令下发失败');
  }
}

function onCloseQrDialog() {
  qrDialogVisible.value = false;
  qrImage.value = '';
}

onBeforeUnmount(() => {
  if (douyinWs) {
    douyinWs.close();
    douyinWs = null;
  }
});

async function onLogout(row: DouyinAccount) {
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
            content="触发扫码登录后，worker 进程（M2 里程碑）将打开有头浏览器窗口完成登录"
            placement="top"
          >
            <ElTag type="info">提示：登录/登出真正动作由 douyin worker 进程执行</ElTag>
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
          <ElTableColumn label="操作" width="280" fixed="right">
            <template #default="{ row }">
              <ElButton
                link
                type="primary"
                size="small"
                :disabled="row.status === 1"
                @click="onLogin(row)"
              >
                扫码登录
              </ElButton>
              <ElButton
                link
                type="warning"
                size="small"
                :disabled="row.status !== 1"
                @click="onLogout(row)"
              >
                登出
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

      <!-- 扫码登录弹窗：订阅 WebSocket 实时显示二维码与登录结果 -->
      <ElDialog
        v-model="qrDialogVisible"
        :title="`扫码登录 · ${qrAccountName}`"
        width="360px"
        :close-on-click-modal="false"
        destroy-on-close
        @close="onCloseQrDialog"
      >
        <div class="flex flex-col items-center gap-3 py-2">
          <div
            v-loading="qrLoading"
            class="flex size-56 items-center justify-center rounded border bg-white"
          >
            <img
              v-if="qrImage"
              :src="qrImage"
              alt="扫码登录二维码"
              class="size-56 object-contain"
            />
            <span v-else class="text-sm text-gray-400">二维码生成中…</span>
          </div>
          <div class="text-center text-sm text-gray-500">
            {{ qrHint }}
          </div>
          <div class="text-xs text-gray-400">
            二维码每 45 秒自动刷新；扫码后保持页面打开等待登录结果
          </div>
        </div>
        <template #footer>
          <ElButton @click="onCloseQrDialog">关闭</ElButton>
        </template>
      </ElDialog>
    </div>
  </Page>
</template>
