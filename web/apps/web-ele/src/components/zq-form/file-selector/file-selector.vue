<script lang="ts" setup>
import type {
  FileSelectorEmits,
  FileSelectorFile,
  FileSelectorProps,
} from './types';

import { computed, onBeforeUnmount, ref, watch } from 'vue';

import {
  CloudUploadOutlined,
  CodeOutlined,
  DeleteOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  FileZipOutlined,
  FolderOpenOutlined,
  IconifyIcon,
  SoundOutlined,
  UploadOutlined,
  VideoCameraOutlined,
} from '@vben/icons';
import { $t } from '@vben/locales';

import {
  ElBadge,
  ElButton,
  ElDialog,
  ElMessage,
  ElPopover,
  ElProgress,
  ElScrollbar,
  ElTag,
  ElTooltip,
} from 'element-plus';

import { getFileStreamUrl, uploadFile } from '#/api/core/file';

defineOptions({
  name: 'FileSelector',
  inheritAttrs: false,
});

const props = withDefaults(defineProps<Props>(), {
  multiple: false,
  placeholder: () => $t('ui.placeholder.select') || 'Please select',
  disabled: false,
  clearable: true,
  showSize: true,
  showIcon: true,
  maxSize: 100, // 默认100MB
  displayMode: 'list', // 默认使用 popover 模式
});

const emit = defineEmits<FileSelectorEmits>();

interface Props extends FileSelectorProps {}

// 扩展的文件类型，包含上传状态
type UploadingFile = FileSelectorFile & {
  failed?: boolean;
  originalFile?: File;
  previewUrl?: string;
  progress?: number;
  uploading?: boolean;
};

const modalVisible = ref(false);
const uploadedFiles = ref<Array<UploadingFile>>([]);
const confirmedFiles = ref<Array<UploadingFile>>([]); // 已确认的文件列表，用于持久化
const selectedFiles = ref<Set<string>>(
  new Set(
    Array.isArray(props.modelValue)
      ? props.modelValue
      : (props.modelValue
        ? [props.modelValue]
        : []),
  ),
);
const uploadInputRef = ref<HTMLInputElement | null>(null);
const isDragging = ref(false);

// 获取已选文件的完整信息（用于显示在触发器和 Popover 中）
const selectedFilesList = computed(() => {
  const fileMap = new Map<string, FileSelectorFile>();

  // 优先从已确认的文件列表中获取
  confirmedFiles.value.forEach((file) => {
    fileMap.set(file.id, file);
  });

  // 如果 modal 打开，从当前上传列表中获取
  uploadedFiles.value.forEach((file) => {
    fileMap.set(file.id, file);
  });

  return [...selectedFiles.value]
    .map((id) => {
      const file = fileMap.get(id);
      if (file) {
        return {
          id: file.id,
          display: file.name,
          file,
        };
      }
      return { id, display: id, file: null };
    })
    .filter((item) => item.file !== null);
});

// 打开modal
const openModal = async () => {
  if (props.disabled) return;

  // 从已确认的文件列表中恢复到上传列表
  uploadedFiles.value = confirmedFiles.value.map((file) => ({
    ...file,
    // 确保已确认的文件不会再次显示上传状态
    uploading: false,
    failed: false,
  }));

  modalVisible.value = true;
};

// 确认选择
const handleConfirm = () => {
  // 获取所有已上传完成的文件（排除上传中和失败的）
  const completedFilesList = uploadedFiles.value.filter(
    (f) => !f.uploading && !f.failed,
  );

  // 单选模式：只保留第一个文件
  let filesToSave: UploadingFile[] = [];
  if (props.multiple) {
    filesToSave = completedFilesList;
  } else if (completedFilesList.length > 0) {
    // 单选模式只取第一个文件
    const firstFile = completedFilesList[0];
    if (firstFile) {
      filesToSave = [firstFile];
    }
  }

  const completedFileIds = filesToSave.map((f) => f.id);

  // 保存已确认的文件到持久化列表
  confirmedFiles.value = filesToSave;

  selectedFiles.value = new Set(completedFileIds);

  const value = props.multiple
    ? completedFileIds
    : completedFileIds.length > 0
      ? completedFileIds[0]
      : '';

  emit('update:modelValue', value);
  emit('change', value);
  modalVisible.value = false;
};

// 清除选择
const handleClear = (e?: MouseEvent) => {
  if (e) {
    e.stopPropagation();
  }
  selectedFiles.value.clear();
  confirmedFiles.value = []; // 同时清空已确认的文件列表
  const emptyValue = props.multiple ? [] : '';
  emit('update:modelValue', emptyValue);
  emit('change', emptyValue);
};

// 删除单个选中项（从 popover 中删除）
const handleRemoveFile = (fileId: string) => {
  selectedFiles.value.delete(fileId);
  // 同时从已确认的文件列表中删除
  confirmedFiles.value = confirmedFiles.value.filter((f) => f.id !== fileId);
  const value = props.multiple ? [...selectedFiles.value] : '';
  emit('update:modelValue', value);
  emit('change', value);
};

// 打开文件选择器
const openFileSelector = () => {
  uploadInputRef.value?.click();
};

// 处理文件选择变化
const handleFileInputChange = async (event: Event) => {
  const target = event.target as HTMLInputElement;
  const files = target.files;

  if (!files || files.length === 0) return;

  // 处理所有选中的文件
  const fileArray = [...files];
  await handleFilesUpload(fileArray);

  // 清空 input，以便可以重复选择相同文件
  target.value = '';
};

// 处理文件上传（统一处理函数）- 同时上传所有文件
const handleFilesUpload = async (files: File[]) => {
  // 单选模式：替换现有文件
  if (props.multiple) {
    // 多选模式：使用 Promise.all 同时上传所有文件
    const uploadPromises = files.map((file) => uploadSingleFile(file));
    await Promise.all(uploadPromises);
  } else {
    // 清理之前文件的预览URL
    uploadedFiles.value.forEach((f) => {
      if (f.previewUrl) {
        URL.revokeObjectURL(f.previewUrl);
      }
    });
    uploadedFiles.value = [];

    // 只上传第一个文件（input[multiple=false] 确保只有一个文件）
    const firstFile = files[0];
    if (firstFile) {
      await uploadSingleFile(firstFile);
    }
  }
};

// 上传单个文件
const uploadSingleFile = async (file: File) => {
  // 检查文件大小
  if (props.maxSize && file.size > props.maxSize * 1024 * 1024) {
    ElMessage.error(`文件 "${file.name}" 大小不能超过 ${props.maxSize}MB`);
    return;
  }

  // 检查文件类型
  if (props.accept && props.accept.length > 0) {
    const fileExtPart = file.name.split('.').pop();
    const fileExt = fileExtPart ? `.${fileExtPart.toLowerCase()}` : '';
    const fileType = file.type;
    const isAccepted = props.accept.some((accept) => {
      if (accept.includes('/*')) {
        // 处理通配符类型，如 'image/*'
        const typePart = accept.split('/')[0];
        return typePart && fileType.startsWith(typePart);
      }
      return accept === fileExt || accept === fileType;
    });

    if (!isAccepted) {
      ElMessage.error(`不支持的文件类型: ${fileExt || '未知'}`);
      return;
    }
  }

  // 生成临时ID
  const tempId = `temp-${Date.now()}-${Math.random()}`;

  // 添加到上传列表（显示在表格中）
  const uploadItem: FileSelectorFile & {
    failed?: boolean;
    originalFile?: File;
    previewUrl?: string;
    progress?: number;
    uploading?: boolean;
  } = {
    id: tempId,
    name: file.name,
    path: '',
    type: 'file',
    size: file.size,
    mime_type: file.type,
    progress: 0,
    uploading: true,
    failed: false,
    originalFile: file, // 保存原始文件对象，用于重新上传
    sys_create_datetime: new Date().toISOString(), // 添加当前时间
  };

  // 如果是图片，立即生成本地预览
  if (file.type.startsWith('image/')) {
    const previewUrl = URL.createObjectURL(file);
    uploadItem.previewUrl = previewUrl;
  }

  uploadedFiles.value.push(uploadItem);

  try {
    // 使用真实的上传进度
    const response = await uploadFile(file, undefined, (progressEvent) => {
      const item = uploadedFiles.value.find((f) => f.id === tempId);
      if (item) {
        item.progress = progressEvent.percentage;
      }
    });

    // 更新为实际的文件信息
    const item = uploadedFiles.value.find((f) => f.id === tempId) as
      | (FileSelectorFile & {
          previewUrl?: string;
          progress?: number;
          uploading?: boolean;
        })
      | undefined;
    if (item && response) {
      // 保留本地预览URL（不清理，继续使用前端预览）
      // 如果后端返回了URL但我们有本地预览，优先使用本地预览

      item.id = String(response.id);
      item.path = response.path || '';
      item.url = response.url;
      item.file_ext = response.file_ext;
      item.progress = 100;
      item.uploading = false;
      item.sys_create_datetime = response.sys_create_datetime;
      // previewUrl 保持不变，继续显示本地预览
    }

    ElMessage.success(`文件 "${file.name}" 上传成功`);
  } catch (error) {
    console.error('上传失败:', error);
    ElMessage.error(`文件 "${file.name}" 上传失败`);

    // 标记文件为失败状态，不删除
    const item = uploadedFiles.value.find((f) => f.id === tempId) as
      | (FileSelectorFile & {
          failed?: boolean;
          progress?: number;
          uploading?: boolean;
        })
      | undefined;

    if (item) {
      item.uploading = false;
      item.failed = true;
      item.progress = 0;
    }
  }
};

// 重新上传失败的文件
const handleRetryUpload = async (file: UploadingFile) => {
  if (!file.originalFile) {
    ElMessage.error('无法重新上传：原始文件已丢失');
    return;
  }

  // 重置状态
  const item = uploadedFiles.value.find((f) => f.id === file.id);

  if (!item || !item.originalFile) return;

  item.uploading = true;
  item.failed = false;
  item.progress = 0;

  try {
    // 使用真实的上传进度
    const response = await uploadFile(
      item.originalFile,
      undefined,
      (progressEvent) => {
        const currentItem = uploadedFiles.value.find((f) => f.id === file.id);
        if (currentItem) {
          currentItem.progress = progressEvent.percentage;
        }
      },
    );

    // 更新为实际的文件信息
    if (response) {
      item.id = String(response.id);
      item.path = response.path || '';
      item.url = response.url;
      item.file_ext = response.file_ext;
      item.progress = 100;
      item.uploading = false;
      item.failed = false;
      item.sys_create_datetime = response.sys_create_datetime;
    }

    ElMessage.success(`文件 "${item.name}" 重新上传成功`);
  } catch (error) {
    console.error('重新上传失败:', error);
    ElMessage.error(`文件 "${item.name}" 重新上传失败`);
    item.uploading = false;
    item.failed = true;
    item.progress = 0;
  }
};

// 删除文件
const handleDeleteFile = (
  file: FileSelectorFile & { previewUrl?: string; uploading?: boolean },
) => {
  // 如果正在上传，不允许删除
  if (file.uploading) {
    ElMessage.warning('文件正在上传中，请等待上传完成');
    return;
  }

  const index = uploadedFiles.value.findIndex((f) => f.id === file.id);
  if (index !== -1) {
    const fileToDelete = uploadedFiles.value[index] as FileSelectorFile & {
      previewUrl?: string;
    };
    // 清理本地预览URL
    if (fileToDelete.previewUrl) {
      URL.revokeObjectURL(fileToDelete.previewUrl);
    }
    uploadedFiles.value.splice(index, 1);
  }
};

// 格式化文件大小
const formatFileSize = (bytes?: number) => {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

// 格式化日期时间
const formatDateTime = (dateStr?: string) => {
  if (!dateStr) return '-';
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();

    // 1分钟内
    if (diff < 60 * 1000) {
      return '刚刚';
    }
    // 1小时内
    if (diff < 60 * 60 * 1000) {
      return `${Math.floor(diff / (60 * 1000))}分钟前`;
    }
    // 今天
    if (date.toDateString() === now.toDateString()) {
      return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      });
    }
    // 今年
    if (date.getFullYear() === now.getFullYear()) {
      return date.toLocaleDateString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
      });
    }
    // 往年
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  } catch {
    return '-';
  }
};

// 获取文件图标组件（支持文件对象或文件名字符串）
const getFileIconComponent = (
  fileOrName:
    | FileSelectorFile
    | string
    | { display: string; file: FileSelectorFile | null },
) => {
  let ext = '';
  let mimeType = '';

  if (typeof fileOrName === 'string') {
    // 如果是字符串，从文件名推断扩展名
    const parts = fileOrName.split('.');
    ext = parts.length > 1 ? parts.pop()?.toLowerCase() || '' : '';
  } else if ('file' in fileOrName && fileOrName.file) {
    // 如果是 selectedFilesList 项
    const file = fileOrName.file;
    ext =
      file.file_ext?.toLowerCase() ||
      file.name?.split('.').pop()?.toLowerCase() ||
      '';
    mimeType = file.mime_type || '';
  } else {
    // 如果是 FileSelectorFile 对象
    const file = fileOrName as FileSelectorFile;
    ext =
      file.file_ext?.toLowerCase() ||
      file.name?.split('.').pop()?.toLowerCase() ||
      '';
    mimeType = file.mime_type || '';
  }

  // 图片
  if (
    mimeType.startsWith('image/') ||
    ['bmp', 'gif', 'jpeg', 'jpg', 'png', 'svg', 'webp'].includes(ext)
  ) {
    return FileImageOutlined;
  }

  // 视频
  if (
    mimeType.startsWith('video/') ||
    ['avi', 'flv', 'mkv', 'mov', 'mp4', 'wmv'].includes(ext)
  ) {
    return VideoCameraOutlined;
  }

  // 音频
  if (
    mimeType.startsWith('audio/') ||
    ['aac', 'flac', 'mp3', 'ogg', 'wav'].includes(ext)
  ) {
    return SoundOutlined;
  }

  // 文档
  if (['pdf'].includes(ext)) return FilePdfOutlined;
  if (['doc', 'docx'].includes(ext)) return FileWordOutlined;
  if (['xls', 'xlsx'].includes(ext)) return FileExcelOutlined;
  if (['ppt', 'pptx'].includes(ext)) return FilePptOutlined;
  if (['md', 'txt'].includes(ext)) return FileTextOutlined;

  // 压缩文件
  if (['7z', 'gz', 'rar', 'tar', 'zip'].includes(ext)) return FileZipOutlined;

  // 代码文件
  if (
    [
      'c',
      'cpp',
      'go',
      'java',
      'js',
      'jsx',
      'py',
      'rs',
      'ts',
      'tsx',
      'vue',
    ].includes(ext)
  ) {
    return CodeOutlined;
  }

  // 默认
  return FileOutlined;
};

// 获取文件缩略图
const getFileThumbnail = (
  file: FileSelectorFile & { previewUrl?: string },
): string | undefined => {
  if (file.mime_type?.startsWith('image/')) {
    // 优先使用本地预览URL（上传时生成的），其次使用服务器URL
    return file.previewUrl || file.url || getFileStreamUrl(file.id);
  }
  return undefined;
};

// 拖拽事件处理
const handleDragEnter = (e: DragEvent) => {
  e.preventDefault();
  e.stopPropagation();
  isDragging.value = true;
};

const handleDragOver = (e: DragEvent) => {
  e.preventDefault();
  e.stopPropagation();
};

const handleDragLeave = (e: DragEvent) => {
  e.preventDefault();
  e.stopPropagation();
  // 只有当离开整个拖拽区域时才设置为false
  if (e.currentTarget === e.target) {
    isDragging.value = false;
  }
};

const handleDrop = async (e: DragEvent) => {
  e.preventDefault();
  e.stopPropagation();
  isDragging.value = false;

  const files = e.dataTransfer?.files;
  if (files && files.length > 0) {
    const fileArray = [...files];
    await handleFilesUpload(fileArray);
  }
};

// 获取文件类型接受字符串
const acceptString = computed(() => {
  if (!props.accept || props.accept.length === 0) return '';
  return props.accept.join(',');
});

// 清理所有预览URL的函数
const cleanupPreviewUrls = () => {
  uploadedFiles.value.forEach((file) => {
    const fileWithPreview = file as FileSelectorFile & { previewUrl?: string };
    if (fileWithPreview.previewUrl) {
      URL.revokeObjectURL(fileWithPreview.previewUrl);
    }
  });
};

// 监听对话框关闭，清理预览URL
watch(modalVisible, (newVal) => {
  if (!newVal) {
    // 对话框关闭时清理预览URL
    cleanupPreviewUrls();
  }
});

// 组件卸载时清理所有预览URL
onBeforeUnmount(() => {
  cleanupPreviewUrls();
});

defineExpose({
  openModal,
});
</script>

<template>
  <div class="file-selector">
    <!-- Popover 模式 -->
    <ElPopover
      v-if="displayMode === 'popover'"
      :disabled="selectedFiles.size === 0"
      placement="bottom"
      :width="400"
      trigger="hover"
    >
      <template #reference>
        <div
          class="file-selector-trigger"
          :class="{
            'has-files': selectedFiles.size > 0,
            disabled,
            multiple,
          }"
          @click="!disabled && openModal()"
        >
          <!-- 左侧：图标和文字 -->
          <div class="trigger-content">
            <ElBadge
              v-if="selectedFiles.size > 0"
              :value="selectedFiles.size"
              :max="99"
              class="file-badge"
            >
              <div class="trigger-icon">
                <FolderOpenOutlined />
              </div>
            </ElBadge>
            <div v-else class="trigger-icon">
              <FolderOpenOutlined />
            </div>

            <div class="trigger-text">
              <template v-if="selectedFiles.size === 0">
                {{ placeholder }}
              </template>
              <template v-else-if="multiple">
                已选择 {{ selectedFiles.size }} 个文件
              </template>
              <template v-else>
                {{ selectedFilesList[0]?.display }}
              </template>
            </div>
          </div>

          <!-- 右侧：操作按钮 -->
          <div class="trigger-actions">
            <ElButton
              v-if="clearable && selectedFiles.size > 0"
              text
              size="small"
              class="clear-btn"
              @click.stop="handleClear"
            >
              <IconifyIcon icon="i-carbon:close" />
            </ElButton>
            <div class="trigger-arrow">
              <IconifyIcon icon="i-carbon:chevron-down" />
            </div>
          </div>
        </div>
      </template>

      <!-- Popover 内容：已选文件列表 -->
      <div class="file-popover-content">
        <div class="popover-header">
          <span class="popover-title">已选择的文件</span>
          <ElButton text size="small" @click="openModal">
            <IconifyIcon icon="i-carbon:edit" class="mr-1" />
            编辑
          </ElButton>
        </div>

        <ElScrollbar max-height="300px">
          <div class="popover-file-list">
            <div
              v-for="file in selectedFilesList"
              :key="file.id"
              class="popover-file-item"
            >
              <component
                :is="getFileIconComponent(file)"
                class="popover-file-icon"
              />
              <!-- <ElTooltip :content="file.display" placement="top"> -->
              <span class="popover-file-name">{{ file.display }}</span>
              <!-- </ElTooltip> -->
              <ElButton
                text
                type="danger"
                size="small"
                @click="handleRemoveFile(file.id)"
              >
                <DeleteOutlined />
              </ElButton>
            </div>
          </div>
        </ElScrollbar>
      </div>
    </ElPopover>

    <!-- List 模式 -->
    <div v-else class="file-selector-list-mode">
      <!-- 触发器 -->
      <div
        class="file-selector-trigger"
        :class="{
          'has-files': selectedFiles.size > 0,
          disabled,
          multiple,
        }"
        @click="!disabled && openModal()"
      >
        <!-- 左侧：图标和文字 -->
        <div class="trigger-content">
          <ElBadge
            v-if="selectedFiles.size > 0"
            :value="selectedFiles.size"
            :max="99"
            class="file-badge"
          >
            <div class="trigger-icon">
              <FolderOpenOutlined />
            </div>
          </ElBadge>
          <div v-else class="trigger-icon">
            <FolderOpenOutlined />
          </div>

          <div class="trigger-text">
            <template v-if="selectedFiles.size === 0">
              {{ placeholder }}
            </template>
            <template v-else-if="multiple">
              已选择 {{ selectedFiles.size }} 个文件
            </template>
            <template v-else>
              {{ selectedFilesList[0]?.display }}
            </template>
          </div>
        </div>

        <!-- 右侧：操作按钮 -->
        <div class="trigger-actions">
          <ElButton
            v-if="clearable && selectedFiles.size > 0"
            text
            size="small"
            class="clear-btn"
            @click.stop="handleClear"
          >
            <IconifyIcon icon="i-carbon:close" />
          </ElButton>
          <div class="trigger-arrow">
            <IconifyIcon icon="i-carbon:chevron-down" />
          </div>
        </div>
      </div>

      <!-- 已选文件列表 -->
      <div v-if="selectedFiles.size > 0" class="selected-files-list">
        <div
          v-for="file in selectedFilesList"
          :key="file.id"
          class="selected-file-item"
        >
          <component
            :is="getFileIconComponent(file)"
            class="selected-file-icon"
          />
          <!-- <ElTooltip :content="file.display" placement="top"> -->
          <span class="selected-file-name">{{ file.display }}</span>
          <!-- </ElTooltip> -->
          <ElButton
            text
            type="danger"
            size="small"
            class="selected-file-remove"
            @click="handleRemoveFile(file.id)"
          >
            <DeleteOutlined />
          </ElButton>
        </div>
      </div>
    </div>

    <!-- Modal -->
    <ElDialog
      v-model="modalVisible"
      :title="$t('上传文件') || 'Upload Files'"
      min-width="45%"
      class="file-selector-dialog"
    >
      <div
        class="file-selector-modal"
        :class="{ 'is-dragging': isDragging }"
        @dragenter="handleDragEnter"
        @dragover="handleDragOver"
        @dragleave="handleDragLeave"
        @drop="handleDrop"
      >
        <!-- 隐藏的文件输入框 -->
        <input
          ref="uploadInputRef"
          type="file"
          :accept="acceptString"
          :multiple="multiple"
          style="display: none"
          @change="handleFileInputChange"
        />

        <!-- 右上角上传按钮 -->
        <!-- <div v-if="uploadedFiles.length === 0" class="upload-button-corner">
          <ElButton type="primary" @click="openFileSelector">
            <UploadOutlined class="icon-wrapper" />
            上传文件
          </ElButton>
        </div> -->

        <!-- 拖拽提示遮罩（拖拽时显示） -->
        <div v-if="isDragging" class="drag-overlay">
          <div class="upload-area">
            <CloudUploadOutlined class="upload-area-icon" />
            <div class="upload-area-title">松开鼠标上传文件</div>
            <div class="upload-area-hint">
              <span v-if="accept && accept.length > 0">
                支持格式：{{ accept.join(', ') }}
              </span>
              <span v-if="maxSize"> · 单个文件最大 {{ maxSize }}MB </span>
            </div>
          </div>
        </div>

        <!-- 上传区域（没有文件时显示，居中显示） -->
        <div
          v-if="uploadedFiles.length === 0 && !isDragging"
          class="upload-area-wrapper"
        >
          <div class="upload-area" @click="openFileSelector">
            <CloudUploadOutlined class="upload-area-icon" />
            <div class="upload-area-title">点击或拖拽文件到此处上传</div>
            <div class="upload-area-hint">
              <span v-if="accept && accept.length > 0">
                支持格式：{{ accept.join(', ') }}
              </span>
              <span v-if="maxSize"> · 单个文件最大 {{ maxSize }}MB </span>
            </div>
          </div>
        </div>

        <!-- 文件列表（有文件时显示） -->
        <div v-if="uploadedFiles.length > 0" class="file-selector-content">
          <!-- 右上角上传按钮 -->
          <div class="upload-button-corner">
            <ElButton type="primary" @click="openFileSelector">
              <UploadOutlined class="icon-wrapper" />
              {{ multiple ? '继续上传' : '替换文件' }}
            </ElButton>
          </div>

          <ElScrollbar height="450px" class="file-list-scrollbar">
            <div class="file-list">
              <div
                v-for="file in uploadedFiles"
                :key="file.id"
                class="file-item"
                :class="{
                  uploading: file.uploading,
                  failed: file.failed,
                }"
              >
                <!-- 左侧：图标 + 文件信息 -->
                <div class="file-main">
                  <div class="file-icon-wrapper">
                    <img
                      v-if="getFileThumbnail(file)"
                      :src="getFileThumbnail(file)"
                      class="file-thumbnail"
                      alt="thumbnail"
                    />
                    <component
                      :is="getFileIconComponent(file)"
                      v-else-if="showIcon"
                      class="file-icon-component"
                    />
                  </div>

                  <div class="file-info">
                    <ElTooltip :content="file.name" placement="top">
                      <div class="file-name">{{ file.name }}</div>
                    </ElTooltip>
                    <div class="file-meta">
                      <span class="file-size">{{
                        formatFileSize(file.size)
                      }}</span>
                      <span class="file-time">{{
                        formatDateTime(file.sys_create_datetime)
                      }}</span>
                    </div>
                  </div>
                </div>

                <!-- 右侧：状态 + 操作 -->
                <div class="file-actions">
                  <!-- 上传进度 -->
                  <div v-if="file.uploading" class="file-status">
                    <ElProgress
                      :percentage="file.progress || 0"
                      :status="file.progress === 100 ? 'success' : undefined"
                      :stroke-width="6"
                      style="width: 150px"
                    />
                    <span class="status-text">上传中...</span>
                  </div>

                  <!-- 上传失败 -->
                  <div v-else-if="file.failed" class="file-status">
                    <ElTag type="danger" size="small" effect="plain">
                      <template #icon>
                        <IconifyIcon icon="i-carbon:warning-filled" />
                      </template>
                      上传失败
                    </ElTag>
                  </div>

                  <!-- 上传完成 -->
                  <div v-else class="file-status">
                    <ElTag type="success" size="small" effect="plain">
                      <template #icon>
                        <IconifyIcon icon="i-carbon:checkmark-filled" />
                      </template>
                      已完成
                    </ElTag>
                  </div>

                  <!-- 重新上传按钮（失败时显示） -->
                  <ElButton
                    v-if="file.failed"
                    text
                    type="warning"
                    size="small"
                    @click="handleRetryUpload(file)"
                  >
                    <template #icon>
                      <IconifyIcon icon="i-carbon:renew" />
                    </template>
                    重试
                  </ElButton>

                  <!-- 删除按钮 -->
                  <ElButton
                    text
                    type="danger"
                    size="small"
                    @click="handleDeleteFile(file)"
                    :disabled="file.uploading"
                  >
                    删除
                  </ElButton>
                </div>
              </div>
            </div>
          </ElScrollbar>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <ElButton @click="modalVisible = false">取消</ElButton>
          <ElButton
            type="primary"
            @click="handleConfirm"
            :disabled="
              uploadedFiles.length === 0 ||
              uploadedFiles.some((f) => f.uploading) ||
              uploadedFiles.some((f) => f.failed) ||
              uploadedFiles.filter((f) => !f.uploading && !f.failed).length ===
                0
            "
          >
            确定（{{
              uploadedFiles.filter((f) => !f.uploading && !f.failed).length
            }}）
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped lang="scss">
.file-selector {
  width: 100%;
}

// 自定义触发器样式
.file-selector-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 62px;
  padding: 6px 12px;
  cursor: pointer;
  background-color: hsl(var(--background));
  border: 2px dashed hsl(var(--border));
  border-radius: 6px;
  transition: all 0.3s;

  &:hover:not(.disabled) {
    background-color: hsl(var(--primary) / 3%);
    border-color: hsl(var(--primary));
  }

  &.has-files {
    border-color: hsl(var(--primary) / 50%);
  }

  &.disabled {
    cursor: not-allowed;
    background-color: hsl(var(--background) / 50%);
    opacity: 0.6;
  }
}

.trigger-content {
  display: flex;
  flex: 1;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.file-badge {
  :deep(.el-badge__content) {
    height: 18px;
    padding: 0 6px;
    font-size: 11px;
    line-height: 18px;
  }
}

.trigger-icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  font-size: 30px;
  color: hsl(var(--foreground) / 50%);
}

.trigger-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 14px;
  color: hsl(var(--foreground));
  white-space: nowrap;

  .file-selector-trigger:not(.has-files) & {
    color: hsl(var(--foreground) / 50%);
  }
}

.trigger-actions {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
}

.clear-btn {
  padding: 4px;
  font-size: 16px;
  opacity: 0.6;
  transition: opacity 0.3s;

  &:hover {
    opacity: 1;
  }
}

.trigger-arrow {
  display: flex;
  align-items: center;
  font-size: 14px;
  color: hsl(var(--foreground) / 50%);
  transition: transform 0.3s;

  .file-selector-trigger:hover & {
    transform: rotate(180deg);
  }
}

// Popover 内容样式
.file-popover-content {
  padding: 0;
}

.popover-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background-color: hsl(var(--background) / 50%);
  border-bottom: 1px solid hsl(var(--border));
}

.popover-title {
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.popover-file-list {
  padding: 8px;
}

.popover-file-item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px;
  margin-bottom: 4px;
  background-color: hsl(var(--background));
  border-radius: 6px;
  transition: all 0.3s;

  &:last-child {
    margin-bottom: 0;
  }

  &:hover {
    background-color: hsl(var(--primary) / 5%);
  }
}

.popover-file-icon {
  flex-shrink: 0;
  font-size: 24px;
  color: hsl(var(--primary));
}

.popover-file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.file-selector-dialog {
  :deep(.el-dialog__body) {
    padding: 20px;
  }
}

.file-selector-modal {
  position: relative;
  min-height: 400px;
  margin: 10px;

  &.is-dragging {
    .drag-overlay {
      pointer-events: all;
      opacity: 1;
    }
  }
}

.upload-button-corner {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 10;

  .icon-wrapper {
    margin-right: 4px;
    font-size: 18px;
  }
}

.drag-overlay {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 100;
  width: 85%;
  pointer-events: none;
  opacity: 0;
  transform: translate(-50%, -50%);
  transition: opacity 0.2s;

  .upload-area {
    cursor: default;
    background-color: hsl(var(--background) / 98%); // 几乎完全不透明的背景
    border-color: hsl(var(--primary));
    box-shadow: 0 4px 20px rgb(0 0 0 / 15%); // 添加阴影效果

    .upload-area-icon {
      color: hsl(var(--primary));
      transform: scale(1.1);
    }

    .upload-area-title {
      color: hsl(var(--primary));
    }

    .upload-area-hint {
      color: hsl(var(--foreground) / 70%);
    }

    &:hover {
      background-color: hsl(var(--background) / 98%);
      border-color: hsl(var(--primary));

      .upload-area-icon {
        transform: scale(1.1);
      }
    }
  }
}

.upload-area-wrapper {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 85%;
  transform: translate(-50%, -50%);
  // padding: 20px
}

.upload-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 40px;
  cursor: pointer;
  background-color: hsl(var(--background) / 50%);
  border: 2px dashed hsl(var(--border));
  border-radius: 12px;
  transition: all 0.3s;

  &:hover {
    background-color: hsl(var(--primary) / 5%);
    border-color: hsl(var(--primary));

    .upload-area-icon {
      transform: scale(1.1);
    }
  }

  .upload-area-icon {
    margin-bottom: 24px;
    font-size: 72px;
    color: hsl(var(--foreground) / 40%);
    transition: all 0.3s;
  }

  .upload-area-title {
    margin-bottom: 12px;
    font-size: 20px;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .upload-area-hint {
    font-size: 14px;
    color: hsl(var(--foreground) / 60%);
    text-align: center;
  }
}

.file-list-header {
  margin-bottom: 12px;
}

.file-selector-content {
  position: relative;
}

.file-list-scrollbar {
  padding: 40px 10px;
  background-color: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.file-list {
  padding: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  margin-bottom: 8px;
  background-color: hsl(var(--background));
  border: 1px solid hsl(var(--border) / 50%);
  border-radius: 8px;
  transition: all 0.3s ease;

  &:last-child {
    margin-bottom: 0;
  }

  &:hover {
    background-color: hsl(var(--primary) / 3%);
    border-color: hsl(var(--primary) / 50%);
    box-shadow: 0 2px 8px hsl(var(--primary) / 10%);
  }

  &.uploading {
    background-color: hsl(var(--primary) / 5%);
    border-color: hsl(var(--primary) / 30%);
  }

  &.failed {
    background-color: hsl(0deg 84% 60% / 5%);
    border-color: hsl(0deg 84% 60% / 30%);
  }
}

.file-main {
  display: flex;
  flex: 1;
  gap: 16px;
  align-items: center;
  min-width: 0;
}

.file-icon-wrapper {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;

  .file-thumbnail {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border: 1px solid hsl(var(--border));
    border-radius: 6px;
  }

  .file-icon-component {
    font-size: 40px;
    color: hsl(var(--primary));
  }
}

.file-info {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 15px;
  font-weight: 500;
  line-height: 1.4;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.file-meta {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 13px;
  color: hsl(var(--foreground) / 60%);

  .file-size {
    &::before {
      content: '📦 ';
    }
  }

  .file-time {
    &::before {
      content: '🕐 ';
    }
  }
}

.file-actions {
  display: flex;
  flex-shrink: 0;
  gap: 16px;
  align-items: center;
}

.file-status {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 150px;

  .status-text {
    font-size: 13px;
    color: hsl(var(--foreground) / 60%);
    white-space: nowrap;
  }
}

.delete-btn {
  font-size: 18px;
  opacity: 0.6;
  transition: opacity 0.3s;

  &:hover {
    opacity: 1;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.3;
  }
}

.upload-progress {
  padding: 4px 0;
}

.dialog-footer {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
}

// List 模式样式
.file-selector-list-mode {
  width: 100%;
}

.selected-files-list {
  max-height: 300px;
  padding: 12px;
  margin-top: 12px;
  overflow-y: auto;
  background-color: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.selected-file-item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 8px;
  background-color: hsl(var(--background));
  border: 1px solid hsl(var(--border) / 50%);
  border-radius: 6px;
  transition: all 0.3s;

  &:last-child {
    margin-bottom: 0;
  }

  &:hover {
    background-color: hsl(var(--primary) / 5%);
    border-color: hsl(var(--primary) / 30%);

    .selected-file-remove {
      opacity: 1;
    }
  }
}

.selected-file-icon {
  flex-shrink: 0;
  font-size: 24px;
  color: hsl(var(--primary));
}

.selected-file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 14px;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.selected-file-remove {
  flex-shrink: 0;
  opacity: 0.6;
  transition: opacity 0.3s;

  &:hover {
    opacity: 1;
  }
}
</style>
