<script lang="ts" setup>
import type {
  DouyinQuickReply,
  DouyinQuickReplyInput,
} from '#/api/core/douyin';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElPagination,
  ElSpace,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  createQuickReply,
  deleteQuickReply,
  getQuickReplyList,
  updateQuickReply,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinQuickReply' });

const rows = ref<DouyinQuickReply[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 10, total: 0 });
const search = reactive({ title: '' });

const dialogVisible = ref(false);
const editingId = ref<null | string>(null);
const form = reactive<DouyinQuickReplyInput>({
  shortcut: '',
  title: '',
  content: '',
  is_shared: true,
  status: true,
});

async function load() {
  loading.value = true;
  try {
    const res = await getQuickReplyList({
      page: page.page,
      page_size: page.page_size,
      title: search.title || undefined,
    });
    rows.value = res.items || [];
    page.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingId.value = null;
  Object.assign(form, {
    shortcut: '',
    title: '',
    content: '',
    is_shared: true,
    status: true,
  });
  dialogVisible.value = true;
}

function openEdit(row: DouyinQuickReply) {
  editingId.value = row.id;
  Object.assign(form, {
    shortcut: row.shortcut,
    title: row.title,
    content: row.content,
    is_shared: row.is_shared,
    status: row.status,
  });
  dialogVisible.value = true;
}

async function onSave() {
  if (!form.shortcut || !form.title || !form.content) {
    ElMessage.warning('快捷键、标题、内容必填');
    return;
  }
  if (editingId.value) await updateQuickReply(editingId.value, form);
  else await createQuickReply(form);
  ElMessage.success('已保存');
  dialogVisible.value = false;
  load();
}

async function onDelete(row: DouyinQuickReply) {
  await ElMessageBox.confirm(`确定删除 "${row.title}" ?`, '提示', {
    type: 'warning',
  });
  await deleteQuickReply(row.id);
  ElMessage.success('已删除');
  load();
}

onMounted(load);
</script>

<template>
  <Page title="快捷回复" description="人工客服介入时常用的短语片段（可被聊天抽屉直接引用）">
    <ElSpace wrap class="toolbar">
      <ElInput
        v-model="search.title"
        placeholder="搜索标题"
        clearable
        style="width: 220px"
        @keyup.enter="load"
      />
      <ElButton type="primary" @click="load">搜索</ElButton>
      <ElButton type="success" @click="openCreate">新建</ElButton>
    </ElSpace>

    <ElTable :data="rows" v-loading="loading" stripe>
      <ElTableColumn prop="shortcut" label="快捷键" width="120">
        <template #default="{ row }">
          <code>{{ row.shortcut }}</code>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="title" label="标题" min-width="160" />
      <ElTableColumn prop="content" label="内容" min-width="260" show-overflow-tooltip />
      <ElTableColumn prop="use_count" label="使用次数" width="90" />
      <ElTableColumn label="共享" width="80">
        <template #default="{ row }">
          <ElTag v-if="row.is_shared" type="success" size="small">团队</ElTag>
          <ElTag v-else type="info" size="small">个人</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="状态" width="80">
        <template #default="{ row }">
          <ElTag :type="row.status ? 'success' : 'info'" size="small">
            {{ row.status ? '启用' : '停用' }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" size="small" @click="openEdit(row)">
            编辑
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
      :title="editingId ? '编辑' : '新建'"
      width="520px"
    >
      <ElForm :model="form" label-width="80px">
        <ElFormItem label="快捷键">
          <ElInput v-model="form.shortcut" placeholder="如 /hi、/price" />
        </ElFormItem>
        <ElFormItem label="标题">
          <ElInput v-model="form.title" />
        </ElFormItem>
        <ElFormItem label="内容">
          <ElInput v-model="form.content" type="textarea" :rows="5" />
        </ElFormItem>
        <ElFormItem label="团队共享">
          <ElSwitch v-model="form.is_shared" />
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

code {
  padding: 2px 6px;
  font-family: ui-monospace, monospace;
  color: #409eff;
  background: #f5f7fa;
  border-radius: 4px;
}
</style>
