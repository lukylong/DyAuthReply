<script lang="ts" setup>
import type {
  DouyinTemplate,
  DouyinTemplateCategory,
  DouyinTemplateInput,
  DouyinTemplateLink,
} from '#/api/core/douyin';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  ElButton,
  ElCard,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
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
  ElTree,
} from 'element-plus';

import {
  createCategory,
  createTemplate,
  deleteCategory,
  deleteTemplate,
  getCategoryTree,
  getTemplateList,
  previewTemplate,
  updateTemplate,
} from '#/api/core/douyin';

defineOptions({ name: 'DouyinTemplate' });

// ---------- state ----------
const categories = ref<DouyinTemplateCategory[]>([]);
const currentCategoryId = ref<string>('');
const templates = ref<DouyinTemplate[]>([]);
const loading = ref(false);
const page = reactive({ page: 1, page_size: 10, total: 0 });
const search = reactive({ name: '', status: undefined as boolean | undefined });

// 编辑/新建
const dialogVisible = ref(false);
const editingId = ref<null | string>(null);
const form = reactive<DouyinTemplateInput & { id?: string }>({
  name: '',
  content: '',
  category_id: null,
  links: [],
  send_mode: 'multi_message',
  status: true,
  is_shared: false,
});

// 预览
const previewVisible = ref(false);
const previewRendered = ref('');
const previewCtx = ref('{"nickname":"小明"}');

// 分类对话框
const catDialogVisible = ref(false);
const newCatName = ref('');

// ---------- computed ----------
const categoryTreeData = computed(() => [
  { id: '', name: '全部模板', template_count: 0, children: [] },
  ...categories.value,
]);

// ---------- methods ----------
async function loadCategories() {
  categories.value = await getCategoryTree();
}

async function loadTemplates() {
  loading.value = true;
  try {
    const params: Record<string, unknown> = {
      page: page.page,
      page_size: page.page_size,
    };
    if (currentCategoryId.value) params.category_id = currentCategoryId.value;
    if (search.name) params.name = search.name;
    if (search.status !== undefined) params.status = search.status;

    const res = await getTemplateList(params);
    templates.value = res.items || [];
    page.total = res.total || 0;
  } finally {
    loading.value = false;
  }
}

function handleCategorySelect(data: DouyinTemplateCategory) {
  currentCategoryId.value = data.id;
  page.page = 1;
  loadTemplates();
}

function openCreate() {
  editingId.value = null;
  Object.assign(form, {
    name: '',
    content: '',
    category_id: currentCategoryId.value || null,
    links: [],
    send_mode: 'multi_message',
    status: true,
    is_shared: false,
    remark: '',
  });
  dialogVisible.value = true;
}

function openEdit(row: DouyinTemplate) {
  editingId.value = row.id;
  Object.assign(form, {
    name: row.name,
    content: row.content,
    category_id: row.category_id,
    links: row.links || [],
    send_mode: row.send_mode,
    status: row.status,
    is_shared: row.is_shared,
    remark: row.remark,
  });
  dialogVisible.value = true;
}

function addLinkRow() {
  form.links = [...(form.links || []), { title: '', url: '' }];
}

function removeLinkRow(idx: number) {
  form.links = (form.links || []).filter((_, i) => i !== idx);
}

async function onSave() {
  if (!form.name || !form.content) {
    ElMessage.warning('名称与内容必填');
    return;
  }
  try {
    if (editingId.value) {
      await updateTemplate(editingId.value, form);
      ElMessage.success('已更新');
    } else {
      await createTemplate(form);
      ElMessage.success('已创建');
    }
    dialogVisible.value = false;
    loadTemplates();
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '操作失败');
  }
}

async function onDelete(row: DouyinTemplate) {
  await ElMessageBox.confirm(`确定删除模板 "${row.name}" ?`, '提示', {
    type: 'warning',
  });
  await deleteTemplate(row.id);
  ElMessage.success('已删除');
  loadTemplates();
}

async function onPreview(row: DouyinTemplate) {
  previewVisible.value = true;
  previewRendered.value = '';
  try {
    const ctx = JSON.parse(previewCtx.value || '{}');
    const res = await previewTemplate(row.id, ctx);
    previewRendered.value = res.rendered;
  } catch {
    ElMessage.error('上下文必须是合法 JSON');
  }
}

async function onCreateCategory() {
  if (!newCatName.value) return;
  await createCategory({ name: newCatName.value });
  newCatName.value = '';
  catDialogVisible.value = false;
  loadCategories();
  ElMessage.success('分类已创建');
}

async function onDeleteCategory(id: string) {
  await ElMessageBox.confirm('删除后该分类下的模板将变为未分类', '提示', {
    type: 'warning',
  });
  await deleteCategory(id);
  loadCategories();
  ElMessage.success('已删除');
}

onMounted(async () => {
  await loadCategories();
  await loadTemplates();
});
</script>

<template>
  <Page
    title="回复模板管理"
    description="模板化、可复用、支持变量与链接；规则与快捷回复都可以引用这里的模板"
  >
    <div class="tpl-layout">
      <ElCard class="sidebar" shadow="never" header="分类">
        <template #header>
          <div class="card-header">
            <span>分类</span>
            <ElButton size="small" type="primary" @click="catDialogVisible = true">
              新建
            </ElButton>
          </div>
        </template>
        <ElTree
          :data="categoryTreeData"
          node-key="id"
          :default-expand-all="true"
          @node-click="handleCategorySelect"
        >
          <template #default="{ node, data }">
            <span style="flex: 1">
              {{ data.name }}
              <ElTag v-if="data.template_count" size="small" type="info">
                {{ data.template_count }}
              </ElTag>
            </span>
            <ElButton
              v-if="data.id"
              link
              type="danger"
              size="small"
              @click.stop="onDeleteCategory(data.id)"
            >
              删除
            </ElButton>
          </template>
        </ElTree>
      </ElCard>

      <ElCard class="main" shadow="never">
        <ElSpace wrap class="toolbar">
          <ElInput
            v-model="search.name"
            placeholder="搜索模板名"
            clearable
            style="width: 220px"
            @keyup.enter="loadTemplates"
          />
          <ElSelect
            v-model="search.status"
            placeholder="状态"
            clearable
            style="width: 120px"
          >
            <ElOption label="启用" :value="true" />
            <ElOption label="停用" :value="false" />
          </ElSelect>
          <ElButton type="primary" @click="loadTemplates">搜索</ElButton>
          <ElButton type="success" @click="openCreate">新建模板</ElButton>
        </ElSpace>

        <ElTable :data="templates" v-loading="loading" stripe>
          <ElTableColumn prop="name" label="模板名" min-width="140" />
          <ElTableColumn label="分类" width="120">
            <template #default="{ row }">
              {{ row.category_name || '未分类' }}
            </template>
          </ElTableColumn>
          <ElTableColumn prop="content" label="内容" min-width="260" show-overflow-tooltip />
          <ElTableColumn label="变量" width="160">
            <template #default="{ row }">
              <ElTag v-for="v in row.variables" :key="v" size="small" class="mr-4">
                <span v-text="'{{' + v + '}}'" />
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="use_count" label="引用" width="70" sortable />
          <ElTableColumn label="共享" width="70">
            <template #default="{ row }">
              <ElTag v-if="row.is_shared" type="success" size="small">是</ElTag>
              <ElTag v-else type="info" size="small">否</ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="状态" width="80">
            <template #default="{ row }">
              <ElTag :type="row.status ? 'success' : 'info'" size="small">
                {{ row.status ? '启用' : '停用' }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="210" fixed="right">
            <template #default="{ row }">
              <ElButton link type="primary" size="small" @click="onPreview(row)">
                预览
              </ElButton>
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
            :page-sizes="[10, 20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            @current-change="loadTemplates"
            @size-change="loadTemplates"
          />
        </div>
      </ElCard>
    </div>

    <!-- 编辑/创建对话框 -->
    <ElDialog
      v-model="dialogVisible"
      :title="editingId ? '编辑模板' : '新建模板'"
      width="680px"
    >
      <ElForm :model="form" label-width="90px">
        <ElFormItem label="模板名">
          <ElInput v-model="form.name" />
        </ElFormItem>
        <ElFormItem label="分类">
          <ElSelect v-model="form.category_id" clearable placeholder="未分类">
            <ElOption
              v-for="c in categories"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="内容">
          <ElInput
            v-model="form.content"
            type="textarea"
            :rows="5"
            placeholder="支持 {{nickname}} / {{time_greeting}} 等变量占位"
          />
        </ElFormItem>
        <ElFormItem label="链接列表">
          <div style="flex: 1">
            <div
              v-for="(link, idx) in form.links"
              :key="idx"
              class="link-row"
            >
              <ElInput
                v-model="(form.links as DouyinTemplateLink[])[idx].title"
                placeholder="标题"
                style="width: 40%"
              />
              <ElInput
                v-model="(form.links as DouyinTemplateLink[])[idx].url"
                placeholder="https://"
                style="width: 55%"
              />
              <ElButton link type="danger" @click="removeLinkRow(idx)">
                删除
              </ElButton>
            </div>
            <ElButton plain size="small" @click="addLinkRow">+ 添加链接</ElButton>
          </div>
        </ElFormItem>
        <ElFormItem label="发送模式">
          <ElSelect v-model="form.send_mode">
            <ElOption label="文本 + 每条链接独立消息" value="multi_message" />
            <ElOption label="合并成一条消息" value="merged" />
            <ElOption label="卡片失败降级到文本" value="card_fallback" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="共享">
          <ElSwitch v-model="form.is_shared" />
          <span class="hint">开启后所有账号可引用</span>
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

    <!-- 预览对话框 -->
    <ElDialog v-model="previewVisible" title="模板预览" width="520px">
      <ElFormItem label="变量(JSON)">
        <ElInput v-model="previewCtx" type="textarea" :rows="3" />
      </ElFormItem>
      <ElCard shadow="never" header="渲染结果">
        <pre class="preview-box">{{ previewRendered }}</pre>
      </ElCard>
    </ElDialog>

    <!-- 新建分类 -->
    <ElDialog v-model="catDialogVisible" title="新建分类" width="360px">
      <ElInput v-model="newCatName" placeholder="分类名称" />
      <template #footer>
        <ElButton @click="catDialogVisible = false">取消</ElButton>
        <ElButton type="primary" @click="onCreateCategory">创建</ElButton>
      </template>
    </ElDialog>
  </Page>
</template>

<style scoped>
.tpl-layout {
  display: flex;
  gap: 16px;
}

.sidebar {
  width: 240px;
  flex-shrink: 0;
}

.main {
  flex: 1;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.toolbar {
  margin-bottom: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.link-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}

.mr-4 {
  margin-right: 4px;
}

.preview-box {
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, 'JetBrains Mono', monospace;
  font-size: 13px;
}
</style>
