<script lang="ts" setup>
import { reactive, ref, watch } from 'vue';

import { ElDialog, ElForm, ElFormItem, ElInput, ElMessage, ElRadio, ElRadioGroup, ElSelect, ElOption, ElDatePicker } from 'element-plus';

import { ClientAnnouncementApi } from '#/api/core/client-announcement';

interface Props {
  visible: boolean;
  mode: 'create' | 'edit';
  currentRow: ClientAnnouncementApi.AnnouncementItem | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void;
  (e: 'success'): void;
}>();

const loading = ref(false);
const formRef = ref();

const formData = reactive<ClientAnnouncementApi.CreateDto>({
  title: '',
  content: '',
  level: 'info',
  status: 'draft',
  publish_time: null,
  expire_time: null,
  target_version: null,
});

const rules = {
  title: [{ required: true, message: '请输入公告标题', trigger: 'blur' }],
  content: [{ required: true, message: '请输入公告内容', trigger: 'blur' }],
};

watch(
  () => props.visible,
  (val) => {
    if (val) {
      if (props.mode === 'edit' && props.currentRow) {
        Object.assign(formData, {
          title: props.currentRow.title,
          content: props.currentRow.content,
          level: props.currentRow.level,
          status: props.currentRow.status,
          publish_time: props.currentRow.publish_time,
          expire_time: props.currentRow.expire_time,
          target_version: props.currentRow.target_version,
        });
      } else {
        resetForm();
      }
    }
  },
);

function resetForm() {
  Object.assign(formData, {
    title: '',
    content: '',
    level: 'info',
    status: 'draft',
    publish_time: null,
    expire_time: null,
    target_version: null,
  });
  formRef.value?.clearValidate();
}

function handleClose() {
  emit('update:visible', false);
}

async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  loading.value = true;
  try {
    if (props.mode === 'create') {
      await ClientAnnouncementApi.create(formData);
      ElMessage.success('创建成功');
    } else if (props.currentRow) {
      await ClientAnnouncementApi.update(props.currentRow.id, formData);
      ElMessage.success('更新成功');
    }
    emit('success');
  } catch (error: any) {
    ElMessage.error(error.message || '操作失败');
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="mode === 'create' ? '新增公告' : '编辑公告'"
    width="600px"
    @close="handleClose"
  >
    <el-form ref="formRef" :model="formData" :rules="rules" label-width="100px">
      <el-form-item label="公告标题" prop="title">
        <el-input v-model="formData.title" placeholder="请输入公告标题" />
      </el-form-item>

      <el-form-item label="公告内容" prop="content">
        <el-input
          v-model="formData.content"
          :rows="5"
          placeholder="请输入公告内容"
          type="textarea"
        />
      </el-form-item>

      <el-form-item label="公告级别" prop="level">
        <el-radio-group v-model="formData.level">
          <el-radio value="info">普通</el-radio>
          <el-radio value="warning">警告</el-radio>
          <el-radio value="urgent">紧急</el-radio>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="状态" prop="status">
        <el-radio-group v-model="formData.status">
          <el-radio value="draft">草稿</el-radio>
          <el-radio value="published">已发布</el-radio>
          <el-radio value="revoked">已撤回</el-radio>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="发布时间">
        <el-date-picker
          v-model="formData.publish_time"
          clearable
          placeholder="留空表示立即发布"
          style="width: 100%"
          type="datetime"
          value-format="YYYY-MM-DD HH:mm:ss"
        />
      </el-form-item>

      <el-form-item label="过期时间">
        <el-date-picker
          v-model="formData.expire_time"
          clearable
          placeholder="留空表示永不过期"
          style="width: 100%"
          type="datetime"
          value-format="YYYY-MM-DD HH:mm:ss"
        />
      </el-form-item>

      <el-form-item label="目标版本">
        <el-input
          v-model="formData.target_version"
          clearable
          placeholder="留空表示所有版本，如：>=0.1.10"
        />
        <div class="text-xs text-gray-500 mt-1">
          支持版本范围表达式，例如：>=0.1.10、&lt;1.0.0
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button :loading="loading" type="primary" @click="handleSubmit">
        {{ mode === 'create' ? '创建' : '更新' }}
      </el-button>
    </template>
  </el-dialog>
</template>
