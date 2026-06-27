<script lang="ts" setup>
import type { DouyinCard, DouyinCardInput } from '#/api/core/douyin';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElImage,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElPagination,
  ElSpace,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElUpload,
} from 'element-plus';

import {
  batchDeleteCard,
  createCard,
  deleteCard,
  getCardList,
  updateCard,
} from '#/api/core/douyin';
import { getFileProxyUrl, uploadFile } from '#/api/core/file';

defineOptions({ name: 'DouyinCard' });

const rows = ref<DouyinCard[]>([]);
const loading = ref(false);
const uploading = ref(false);
const page = reactive({ page: 1, page_size: 10, total: 0 });
const search = reactive({
  title: '',
  status: undefined as boolean | undefined,
});

const dialogVisible = ref(false);
const editingId = ref<null | string>(null);
const coverPreview = ref<string>('');
const form = reactive<DouyinCardInput>({
  title: '',
  description: '',
  cover_file_id: null,
  target_url: '',
  remark: '',
  status: true,
});

async function load() {
  loading.value = true;
  try {
    const res = await getCardList({
      page: page.page,
      page_size: page.page_size,
      title: search.title || undefined,
      status: search.status,
    });
    rows.value = res.items || [];
    page.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

function resetForm() {
  Object.assign(form, {
    title: '',
    description: '',
    cover_file_id: null,
    target_url: '',
    remark: '',
    status: true,
  });
  coverPreview.value = '';
}

function openCreate() {
  editingId.value = null;
  resetForm();
  dialogVisible.value = true;
}

function openEdit(row: DouyinCard) {
  editingId.value = row.id;
  Object.assign(form, {
    title: row.title,
    description: row.description,
    cover_file_id: row.cover_file_id ?? null,
    target_url: row.target_url,
    remark: row.remark,
    status: row.status,
  });
  coverPreview.value = row.cover_url || '';
  dialogVisible.value = true;
}

/** 自定义上传：上传到 file_manager，存 file_id，预览用 proxy 直链 */
async function onUploadCover(options: { file: File }) {
  uploading.value = true;
  try {
    const res = await uploadFile(options.file);
    const fileId = (res as any)?.id;
    if (!fileId) {
      ElMessage.error('上传失败：未返回文件ID');
      return;
    }
    form.cover_file_id = fileId;
    coverPreview.value = getFileProxyUrl(fileId);
    ElMessage.success('封面已上传');
  } catch {
    ElMessage.error('封面上传失败');
  } finally {
    uploading.value = false;
  }
}

function beforeUpload(file: File) {
  const isImage = file.type.startsWith('image/');
  if (!isImage) ElMessage.warning('请上传图片文件');
  const lt5m = file.size / 1024 / 1024 < 5;
  if (!lt5m) ElMessage.warning('图片需小于 5MB');
  return isImage && lt5m;
}

function isValidUrl(u: string) {
  return /^https?:\/\//i.test(u.trim());
}

async function onSave() {
  if (!form.title?.trim()) {
    ElMessage.warning('请输入卡片标题');
    return;
  }
  if (!isValidUrl(form.target_url || '')) {
    ElMessage.warning('请输入有效的目标链接（http/https）');
    return;
  }
  if (editingId.value) await updateCard(editingId.value, form);
  else await createCard(form);
  ElMessage.success('已保存');
  dialogVisible.value = false;
  load();
}

async function onDelete(row: DouyinCard) {
  await ElMessageBox.confirm(`确定删除卡片 "${row.title}" ?`, '提示', {
    type: 'warning',
  });
  await deleteCard(row.id);
  ElMessage.success('已删除');
  load();
}

const selected = ref<DouyinCard[]>([]);
function onSelectionChange(val: DouyinCard[]) {
  selected.value = val;
}

async function onBatchDelete() {
  if (selected.value.length === 0) return;
  await ElMessageBox.confirm(`确定删除选中的 ${selected.value.length} 张卡片?`, '提示', {
    type: 'warning',
  });
  await batchDeleteCard(selected.value.map((r) => r.id));
  ElMessage.success('已删除');
  load();
}

function copyLanding(row: DouyinCard) {
  if (!row.landing_url) return;
  navigator.clipboard?.writeText(row.landing_url);
  ElMessage.success('已复制落地页链接');
}

onMounted(load);
</script>

<template>
  <Page>
    <ElSpace wrap class="toolbar">
      <ElInput
        v-model="search.title"
        placeholder="搜索标题"
        clearable
        style="width: 220px"
      />
      <ElButton type="primary" @click="load">搜索</ElButton>
      <ElButton type="success" @click="openCreate">新增卡片</ElButton>
      <ElButton
        type="danger"
        :disabled="selected.length === 0"
        @click="onBatchDelete"
      >
        批量删除
      </ElButton>
    </ElSpace>

    <ElTable
      :data="rows"
      v-loading="loading"
      stripe
      @selection-change="onSelectionChange"
    >
      <ElTableColumn type="selection" width="48" />
      <ElTableColumn label="封面" width="80">
        <template #default="{ row }">
          <ElImage
            v-if="row.cover_url"
            :src="row.cover_url"
            fit="cover"
            style="width: 44px; height: 44px; border-radius: 6px"
            :preview-src-list="[row.cover_url]"
            preview-teleported
          />
          <span v-else class="no-cover">无</span>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="title" label="标题" min-width="160" show-overflow-tooltip />
      <ElTableColumn prop="description" label="描述" min-width="160" show-overflow-tooltip />
      <ElTableColumn prop="target_url" label="目标链接" min-width="200" show-overflow-tooltip />
      <ElTableColumn prop="remark" label="备注" min-width="120" show-overflow-tooltip />
      <ElTableColumn label="状态" width="80">
        <template #default="{ row }">
          <ElTag :type="row.status ? 'success' : 'info'" size="small">
            {{ row.status ? '启用' : '停用' }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" size="small" @click="openEdit(row)">
            编辑
          </ElButton>
          <ElButton link type="info" size="small" @click="copyLanding(row)">
            复制链接
          </ElButton>
          <ElButton link type="danger" size="small" @click="onDelete(row)">
            删除
          </ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pager">
      <ElPagination
        v-model:current-page="page.page"
        v-model:page-size="page.page_size"
        :total="page.total"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="load"
      />
    </div>

    <ElDialog
      v-model="dialogVisible"
      :title="editingId ? '编辑卡片' : '新增卡片'"
      width="560px"
    >
      <ElForm :model="form" label-width="90px">
        <ElFormItem label="卡片标题" required>
          <ElInput v-model="form.title" maxlength="200" placeholder="抖音卡片显示的标题" />
        </ElFormItem>
        <ElFormItem label="卡片描述">
          <ElInput
            v-model="form.description"
            type="textarea"
            :rows="2"
            maxlength="500"
            placeholder="卡片副标题/描述"
          />
        </ElFormItem>
        <ElFormItem label="封面图">
          <div class="cover-uploader">
            <ElImage
              v-if="coverPreview"
              :src="coverPreview"
              fit="cover"
              style="width: 88px; height: 88px; border-radius: 8px"
            />
            <ElUpload
              :show-file-list="false"
              :http-request="onUploadCover as any"
              :before-upload="beforeUpload"
              accept="image/*"
            >
              <ElButton :loading="uploading">
                {{ coverPreview ? '更换封面' : '上传封面' }}
              </ElButton>
            </ElUpload>
          </div>
        </ElFormItem>
        <ElFormItem label="目标链接" required>
          <ElInput
            v-model="form.target_url"
            placeholder="https://... 用户点击卡片后跳转的真实链接"
          />
        </ElFormItem>
        <ElFormItem label="备注">
          <ElInput v-model="form.remark" placeholder="仅后台展示" />
        </ElFormItem>
        <ElFormItem label="启用">
          <ElSwitch v-model="form.status" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" @click="onSave">保存</ElButton>
      </template>
    </ElDialog>
  </Page>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.cover-uploader {
  display: flex;
  align-items: center;
  gap: 12px;
}

.no-cover {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}
</style>
