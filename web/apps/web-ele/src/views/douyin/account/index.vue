<script lang="ts" setup>
import type {
  DouyinAccount,
  DouyinAccountCreateInput,
} from '#/api/core/douyin';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElDialog,
  ElDivider,
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
  getDouyinAccountListApi,
  importDouyinCredentialApi,
  patchDouyinAccountApi,
  quickCreateDouyinAccountApi,
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
const router = useRouter();

const STATUS_TAG_TYPE: Record<number, string> = {
  0: 'info',
  1: 'success',
  2: 'danger',
  3: 'info',
};

// 凭证三态徽标：可发送 / 仅接收 / 已失效 / 未知
const CREDENTIAL_TAG: Record<string, { label: string; type: string }> = {
  sendable: { label: '可发送', type: 'success' },
  receive_only: { label: '仅接收', type: 'warning' },
  invalid: { label: '已失效', type: 'danger' },
  unknown: { label: '未探测', type: 'info' },
};

function credentialTag(row: DouyinAccount) {
  return CREDENTIAL_TAG[row.credential_state || 'unknown'] || CREDENTIAL_TAG.unknown;
}

// 失效账号整行标红（登录失效 status=2 或凭证 invalid），让运维一眼看出谁掉线了
function rowClassName({ row }: { row: DouyinAccount }) {
  if (row.status === 2 || row.credential_state === 'invalid') {
    return 'dy-row-invalid';
  }
  return '';
}

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
const dialogMode = ref<'create' | 'edit' | 'import'>('create');
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
  dialogMode.value = 'import';
  importAccountId.value = null;
  Object.assign(importForm, {
    bundle: '',
    cookie: '',
    web_protect: '',
    keys: '',
    user_agent: '',
  });
  resetImportSettings();
  importAdvanced.value = false;
  importDialogVisible.value = true;
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

// ---------------- 导入 Cookie 登录态（替代扫码登录，无浏览器）----------------
const importDialogVisible = ref(false);
const importAccountId = ref<string | null>(null);
const importAccountName = ref<string>('');
const importSubmitting = ref(false);
const importAdvanced = ref(false);
const importForm = reactive({
  bundle: '',
  cookie: '',
  web_protect: '',
  keys: '',
  user_agent: '',
});
const importSettings = reactive({
  auto_reply_enabled: true,
  daily_reply_quota: 200,
  min_interval_seconds: 8,
  max_interval_seconds: 25,
  silent_start: '22:00:00',
  silent_end: '08:00:00',
  remark: '',
});

function resetImportSettings() {
  Object.assign(importSettings, {
    auto_reply_enabled: true,
    daily_reply_quota: 200,
    min_interval_seconds: 8,
    max_interval_seconds: 25,
    silent_start: '22:00:00',
    silent_end: '08:00:00',
    remark: '',
  });
}

function openImport(row: DouyinAccount) {
  importAccountId.value = row.id;
  importAccountName.value = row.nickname;
  importForm.bundle = '';
  importForm.cookie = '';
  importForm.web_protect = '';
  importForm.keys = '';
  importForm.user_agent = '';
  importAdvanced.value = false;
  importDialogVisible.value = true;
}

async function onImportSubmit() {
  const bundle = importForm.bundle.trim();
  const cookie = importForm.cookie.trim();
  const webProtect = importForm.web_protect.trim();
  const keys = importForm.keys.trim();
  if (!bundle && !cookie && !webProtect && !keys) {
    ElMessage.warning('请粘贴扩展生成的「一键导入串」，或展开手动填写 Cookie');
    return;
  }
  importSubmitting.value = true;
  try {
    // 判断是新建还是更新
    if (importAccountId.value === null) {
      await quickCreateDouyinAccountApi({
        bundle: bundle || undefined,
        cookie: cookie || undefined,
        web_protect: webProtect || undefined,
        keys: keys || undefined,
        user_agent: importForm.user_agent.trim() || undefined,
        auto_reply_enabled: importSettings.auto_reply_enabled,
        daily_reply_quota: importSettings.daily_reply_quota,
        min_interval_seconds: importSettings.min_interval_seconds,
        max_interval_seconds: importSettings.max_interval_seconds,
        silent_start: importSettings.silent_start,
        silent_end: importSettings.silent_end,
        remark: importSettings.remark.trim() || undefined,
      });
      ElMessage.success(
        '账号创建成功；若昵称仍为「临时_xxx」，说明自动拉取超时，可在列表中手动修改',
      );
    } else {
      // 更新模式：调用 import-credential
      const res = await importDouyinCredentialApi(importAccountId.value, {
        bundle: bundle || undefined,
        cookie: cookie || undefined,
        web_protect: webProtect || undefined,
        keys: keys || undefined,
        user_agent: importForm.user_agent.trim() || undefined,
      });
      ElMessage.success(res.message || '登录态已导入');
    }
    importDialogVisible.value = false;
    loadData();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '操作失败');
  } finally {
    importSubmitting.value = false;
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
            content="登录态通过「导入Cookie」获取（粘贴浏览器 Cookie + web_protect/keys），无需扫码"
            placement="top"
          >
            <ElTag type="info">提示：通过「导入Cookie」导入登录态</ElTag>
          </ElTooltip>
        </div>

        <ElTable
          v-loading="loading"
          :data="tableData"
          border
          stripe
          height="calc(100vh - 320px)"
          :row-class-name="rowClassName"
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
          <ElTableColumn label="凭证" width="150">
            <template #default="{ row }">
              <ElTooltip
                v-if="row.last_probe_error"
                :content="`失败原因：${row.last_probe_error}`"
                placement="top"
              >
                <ElTag :type="credentialTag(row).type as any">
                  {{ row.credential_state_display || credentialTag(row).label }}
                </ElTag>
              </ElTooltip>
              <ElTag v-else :type="credentialTag(row).type as any">
                {{ row.credential_state_display || credentialTag(row).label }}
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
            prop="last_probe_at"
            label="最近探活"
            width="170"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.last_probe_at || '—' }}
            </template>
          </ElTableColumn>
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
                :type="
                  row.status === 2 || row.credential_state === 'invalid'
                    ? 'danger'
                    : 'primary'
                "
                size="small"
                @click="openImport(row)"
              >
                {{
                  row.status === 2 || row.credential_state === 'invalid'
                    ? '重新导入'
                    : '导入Cookie'
                }}
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

      <!-- 导入 Cookie 登录态弹窗（无浏览器）-->
      <ElDialog
        v-model="importDialogVisible"
        :title="importAccountId === null ? '新增账号（导入Cookie自动获取昵称）' : `导入登录态 · ${importAccountName}`"
        width="640px"
        destroy-on-close
      >
        <ElForm label-width="110px">
          <ElFormItem label="一键导入串">
            <ElInput
              v-model="importForm.bundle"
              type="textarea"
              :rows="4"
              placeholder="用「抖音登录态提取器」浏览器扩展抓取后，点『复制一键导入串』，在此粘贴（DYCRED1. 开头）。一次粘贴即含 Cookie + web_protect + keys。"
            />
          </ElFormItem>
          <div class="text-xs text-gray-400" style="margin-bottom: 8px">
            推荐用浏览器扩展生成的「一键导入串」，粘贴一次即可，避免逐项粘贴误操作。
            <ElButton link type="primary" @click="importAdvanced = !importAdvanced">
              {{ importAdvanced ? '收起手动填写' : '手动填写（高级）' }}
            </ElButton>
          </div>

          <template v-if="importAdvanced">
            <ElFormItem label="Cookie">
              <ElInput
                v-model="importForm.cookie"
                type="textarea"
                :rows="4"
                placeholder="浏览器复制的 Cookie 整行（须含 sessionid；与一键导入串二选一，单项填了会覆盖串里的同名字段）"
              />
            </ElFormItem>
            <ElFormItem label="web_protect">
              <ElInput
                v-model="importForm.web_protect"
                type="textarea"
                :rows="2"
                placeholder="bd-ticket-guard 的 web_protect JSON（发送私信才需要；仅监控可留空）"
              />
            </ElFormItem>
            <ElFormItem label="keys">
              <ElInput
                v-model="importForm.keys"
                type="textarea"
                :rows="2"
                placeholder="含 ec_privateKey 的 keys JSON（发送私信才需要；仅监控可留空）"
              />
            </ElFormItem>
            <ElFormItem label="User-Agent">
              <ElInput
                v-model="importForm.user_agent"
                placeholder="可选，与导出 Cookie 的浏览器一致的 UA"
              />
            </ElFormItem>
          </template>

          <template v-if="importAccountId === null">
            <ElDivider content-position="left">回复策略</ElDivider>
            <ElFormItem label="自动回复">
              <ElSwitch v-model="importSettings.auto_reply_enabled" />
            </ElFormItem>
            <ElFormItem label="日回复上限">
              <ElInputNumber
                v-model="importSettings.daily_reply_quota"
                :min="0"
                :max="10000"
              />
            </ElFormItem>
            <ElFormItem label="回复间隔（秒）">
              <div class="flex items-center gap-2">
                <ElInputNumber
                  v-model="importSettings.min_interval_seconds"
                  :min="1"
                  :max="600"
                />
                <span>~</span>
                <ElInputNumber
                  v-model="importSettings.max_interval_seconds"
                  :min="1"
                  :max="600"
                />
              </div>
            </ElFormItem>
            <ElFormItem label="静默时段">
              <div class="flex items-center gap-2">
                <ElTimePicker
                  v-model="importSettings.silent_start"
                  value-format="HH:mm:ss"
                  format="HH:mm"
                  placeholder="开始"
                />
                <span>~</span>
                <ElTimePicker
                  v-model="importSettings.silent_end"
                  value-format="HH:mm:ss"
                  format="HH:mm"
                  placeholder="结束"
                />
              </div>
            </ElFormItem>
            <ElFormItem label="备注">
              <ElInput
                v-model="importSettings.remark"
                type="textarea"
                :rows="2"
                placeholder="可选"
              />
            </ElFormItem>
          </template>

          <div class="text-xs text-gray-400">
            仅监控/接收消息只需 Cookie；发送私信需额外的 web_protect 与 keys（bd-ticket-guard），
            一键导入串已包含三者。补凭据时 Cookie 可留空（自动复用），未填字段保留上次的值。
          </div>
        </ElForm>
        <template #footer>
          <ElButton @click="importDialogVisible = false">取消</ElButton>
          <ElButton
            type="primary"
            :loading="importSubmitting"
            @click="onImportSubmit"
          >
            导入
          </ElButton>
        </template>
      </ElDialog>
    </div>
  </Page>
</template>

<style scoped>
/* 失效账号整行标红，运维一眼定位掉线/失效账号 */
:deep(.dy-row-invalid) {
  --el-table-tr-bg-color: var(--el-color-danger-light-9);
}
:deep(.dy-row-invalid:hover > td) {
  background-color: var(--el-color-danger-light-8) !important;
}
</style>
