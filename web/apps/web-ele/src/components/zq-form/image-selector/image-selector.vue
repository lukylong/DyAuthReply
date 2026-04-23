<script setup lang="ts">
import type {
  ImageSelectorEmits,
  ImageSelectorFile,
  ImageSelectorProps,
} from './types';

import { computed, onBeforeUnmount, ref, watch } from 'vue';

import {
  CloudUploadOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileImageOutlined,
  IconifyIcon,
} from '@vben/icons';
import { $t } from '@vben/locales';

import {
  ElButton,
  ElDialog,
  ElImageViewer,
  ElMessage,
  ElProgress,
  ElScrollbar,
} from 'element-plus';

import { getFileStreamUrl, uploadFile as uploadFileApi } from '#/api/core/file';

import { ImageCropper } from '../../image-cropper';

defineOptions({
  name: 'ImageSelector',
  inheritAttrs: false,
});

const props = withDefaults(defineProps<Props>(), {
  multiple: false,
  placeholder: () => $t('ui.placeholder.select') || '请选择图片',
  disabled: false,
  clearable: true,
  showImageInfo: true,
  maxSize: 10, // 默认10MB
  maxWidth: 0, // 不限制
  maxHeight: 0,
  minWidth: 0,
  minHeight: 0,
  accept: () => ['image/*'],
  gridColumns: 4,
  sortable: false,
  enableCrop: false,
  cropAspectRatio: undefined,
  cropShape: 'rect',
  size: undefined, // 默认不限制，使用网格自适应
  width: undefined, // 触发器宽度，优先级高于 size
  height: undefined, // 触发器高度，优先级高于 size
});

const emit = defineEmits<ImageSelectorEmits>();

interface Props extends ImageSelectorProps {}

// 扩展的图片类型，包含上传状态
type UploadingImage = ImageSelectorFile & {
  failed?: boolean;
  originalFile?: File;
  progress?: number;
  uploading?: boolean;
};

// 状态
const modalVisible = ref(false);
const uploadedImages = ref<UploadingImage[]>([]);
const confirmedImages = ref<UploadingImage[]>([]);
const uploadInputRef = ref<HTMLInputElement>();
const isDragging = ref(false);
const previewVisible = ref(false);
const previewImageUrls = ref<string[]>([]);
const previewInitialIndex = ref(0);

// 选中的图片 ID 集合
const selectedImages = ref<Set<string>>(new Set());

// 裁剪相关状态
const cropperVisible = ref(false);
const cropperImageSrc = ref('');
const cropperImageData = ref<{ file: File }>();

// 监听 modelValue 变化，加载已有图片
watch(
  () => props.modelValue,
  async (newValue) => {
    if (!newValue) {
      confirmedImages.value = [];
      return;
    }

    const ids = Array.isArray(newValue) ? newValue : [newValue];
    if (ids.length === 0) {
      confirmedImages.value = [];
      return;
    }

    // 将 ID 转换为图片对象
    confirmedImages.value = ids.map((id) => ({
      id: String(id),
      name: '',
      path: '',
      url: getFileStreamUrl(String(id)),
    }));
  },
  { immediate: true },
);

// 计算属性：acceptString
const acceptString = computed(() => {
  if (props.accept && props.accept.length > 0) {
    return props.accept.join(',');
  }
  return 'image/*';
});

// 计算属性：已选图片列表
const selectedImagesList = computed(() => {
  if (modalVisible.value && uploadedImages.value.length > 0) {
    return uploadedImages.value.filter((img) =>
      selectedImages.value.has(img.id),
    );
  }

  if (confirmedImages.value.length > 0) {
    return confirmedImages.value;
  }

  return [];
});

// 计算属性：网格样式
const gridStyle = computed(() => ({
  gridTemplateColumns: `repeat(${props.gridColumns}, 1fr)`,
}));

// 计算属性：触发器大小样式
const triggerSizeStyle = computed(() => {
  // 优先使用 width 和 height
  if (props.width || props.height) {
    return {
      width: props.width ? `${props.width}px` : (props.height ? `${props.height}px` : 'auto'),
      height: props.height ? `${props.height}px` : (props.width ? `${props.width}px` : 'auto'),
      paddingTop: 0,
      flexShrink: 0,
    };
  }
  // 其次使用 size（正方形）
  if (props.size && props.size > 0) {
    return {
      width: `${props.size}px`,
      height: `${props.size}px`,
      paddingTop: 0,
      flexShrink: 0,
    };
  }
  return {};
});

// 打开对话框
function openModal() {
  if (props.disabled) return;

  // 从已确认的图片初始化
  uploadedImages.value = confirmedImages.value.map((img) => ({ ...img }));
  selectedImages.value = new Set(confirmedImages.value.map((img) => img.id));

  modalVisible.value = true;
}

// 关闭对话框
function closeModal() {
  modalVisible.value = false;
}

// 打开文件选择器
function openFileSelector() {
  uploadInputRef.value?.click();
}

// 处理文件输入变化
async function handleFileInputChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const files = target.files;
  if (files && files.length > 0) {
    await handleImagesUpload([...files]);
  }
  // 清空 input，允许重复选择同一文件
  target.value = '';
}

// 处理图片上传
async function handleImagesUpload(files: File[]) {
  // 单选模式：清空现有图片
  if (!props.multiple) {
    if (files.length > 1) {
      ElMessage.warning('单选模式下只能选择一张图片');
      files = files[0] ? [files[0]] : [];
    }
    // 清空现有图片和预览
    uploadedImages.value.forEach((img) => {
      if (img.previewUrl) {
        URL.revokeObjectURL(img.previewUrl);
      }
    });
    uploadedImages.value = [];
    selectedImages.value.clear();
  }

  // 验证并上传
  const validFiles = await validateImages(files);

  if (validFiles.length === 0) {
    return;
  }

  // 如果启用裁剪功能，逐个打开裁剪对话框
  if (props.enableCrop) {
    for (const file of validFiles) {
      await handleCropImage(file);
    }
  } else {
    // 并发上传
    await Promise.all(validFiles.map((file) => uploadSingleImage(file)));
  }
}

// 验证图片
async function validateImages(files: File[]): Promise<File[]> {
  const validFiles: File[] = [];

  for (const file of files) {
    // 检查文件类型
    if (!file.type.startsWith('image/')) {
      ElMessage.error(`${file.name} 不是有效的图片文件`);
      continue;
    }

    // 检查文件大小
    if (props.maxSize && file.size > props.maxSize * 1024 * 1024) {
      ElMessage.error(`${file.name} 超过最大文件大小 ${props.maxSize}MB`);
      continue;
    }

    // 检查图片尺寸
    try {
      const dimensions = await getImageDimensions(file);

      if (props.minWidth && dimensions.width < props.minWidth) {
        ElMessage.error(`${file.name} 宽度小于最小要求 ${props.minWidth}px`);
        continue;
      }

      if (props.minHeight && dimensions.height < props.minHeight) {
        ElMessage.error(`${file.name} 高度小于最小要求 ${props.minHeight}px`);
        continue;
      }

      if (props.maxWidth && dimensions.width > props.maxWidth) {
        ElMessage.error(`${file.name} 宽度超过最大限制 ${props.maxWidth}px`);
        continue;
      }

      if (props.maxHeight && dimensions.height > props.maxHeight) {
        ElMessage.error(`${file.name} 高度超过最大限制 ${props.maxHeight}px`);
        continue;
      }

      validFiles.push(file);
    } catch {
      ElMessage.error(`无法读取 ${file.name} 的图片信息`);
    }
  }

  return validFiles;
}

// 获取图片尺寸
function getImageDimensions(
  file: File,
): Promise<{ height: number; width: number }> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);

    img.addEventListener('load', () => {
      URL.revokeObjectURL(url);
      resolve({ width: img.width, height: img.height });
    });

    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('Failed to load image'));
    };

    img.src = url;
  });
}

// 上传单个图片
async function uploadSingleImage(file: File) {
  const uploadItem: UploadingImage = {
    id: `temp_${Date.now()}_${Math.random()}`,
    name: file.name,
    path: '',
    uploading: true,
    progress: 0,
    failed: false,
    originalFile: file,
    sys_create_datetime: new Date().toISOString(),
  };

  // 生成本地预览
  uploadItem.previewUrl = URL.createObjectURL(file);

  // 获取图片尺寸
  try {
    const dimensions = await getImageDimensions(file);
    uploadItem.width = dimensions.width;
    uploadItem.height = dimensions.height;
  } catch {
    // 忽略错误
  }

  uploadedImages.value.push(uploadItem);

  try {
    // 使用真实的上传进度
    const response = await uploadFileApi(file, undefined, (progressEvent) => {
      const item = uploadedImages.value.find((img) => img.id === uploadItem.id);
      if (item) {
        item.progress = progressEvent.percentage;
      }
    });

    if (response && response.id) {
      // 通过数组查找来更新，确保响应式更新
      const item = uploadedImages.value.find((img) => img.id === uploadItem.id);
      if (item) {
        item.id = String(response.id);
        item.path = response.path || '';
        // 使用 getFileStreamUrl 生成正确的 API 访问 URL
        item.url = getFileStreamUrl(String(response.id));
        item.size = response.size;
        item.mime_type = response.mime_type;
        item.uploading = false;
        item.progress = 100;

        // 自动选中上传成功的图片
        selectedImages.value.add(item.id);
      }

      ElMessage.success(`${file.name} 上传成功`);
    } else {
      throw new Error('Upload failed');
    }
  } catch (error) {
    console.error('Upload error:', error);
    const item = uploadedImages.value.find((img) => img.id === uploadItem.id);
    if (item) {
      item.uploading = false;
      item.failed = true;
      item.progress = 0;
    }
    ElMessage.error(`${file.name} 上传失败`);
  }
}

// 打开裁剪对话框
async function handleCropImage(file: File): Promise<void> {
  return new Promise((resolve) => {
    // 创建预览 URL
    cropperImageSrc.value = URL.createObjectURL(file);
    cropperImageData.value = { file };
    cropperVisible.value = true;

    // 等待裁剪完成或取消
    const unwatch = watch(cropperVisible, (newVal) => {
      if (!newVal) {
        // 清理预览 URL
        if (cropperImageSrc.value) {
          URL.revokeObjectURL(cropperImageSrc.value);
          cropperImageSrc.value = '';
        }
        cropperImageData.value = undefined;
        unwatch();
        resolve();
      }
    });
  });
}

// 处理裁剪确认
async function handleCropConfirm(result: {
  blob: Blob;
  canvas: HTMLCanvasElement;
}) {
  if (!cropperImageData.value) {
    return;
  }

  const { file } = cropperImageData.value;

  // 创建裁剪后的文件
  const croppedFile = new File([result.blob], file.name, { type: file.type });

  // 上传裁剪后的图片
  await uploadSingleImage(croppedFile);

  // 关闭裁剪对话框
  cropperVisible.value = false;
}

// 重试上传
async function handleRetryUpload(image: UploadingImage) {
  if (!image.originalFile) {
    ElMessage.error('无法重新上传：原始文件已丢失');
    return;
  }

  // 重置状态
  image.uploading = true;
  image.failed = false;
  image.progress = 0;

  try {
    // 使用真实的上传进度
    const response = await uploadFileApi(
      image.originalFile,
      undefined,
      (progressEvent) => {
        const item = uploadedImages.value.find((img) => img.id === image.id);
        if (item) {
          item.progress = progressEvent.percentage;
        }
      },
    );

    if (response && response.id) {
      image.id = String(response.id);
      image.path = response.path || '';
      // 使用 getFileStreamUrl 生成正确的 API 访问 URL
      image.url = getFileStreamUrl(String(response.id));
      image.size = response.size;
      image.mime_type = response.mime_type;
      image.uploading = false;
      image.failed = false;
      image.progress = 100;

      // 自动选中重新上传成功的图片
      selectedImages.value.add(image.id);

      ElMessage.success(`${image.name} 重新上传成功`);
    } else {
      throw new Error('Upload failed');
    }
  } catch (error) {
    console.error('Retry upload error:', error);
    image.uploading = false;
    image.failed = true;
    image.progress = 0;
    ElMessage.error(`${image.name} 重新上传失败`);
  }
}

// 删除图片
function handleDeleteImage(imageId: string) {
  const index = uploadedImages.value.findIndex((img) => img.id === imageId);
  if (index !== -1) {
    const image = uploadedImages.value[index];
    if (image && image.previewUrl) {
      URL.revokeObjectURL(image.previewUrl);
    }
    uploadedImages.value.splice(index, 1);
    selectedImages.value.delete(imageId);
  }
}

// 切换图片选中状态
function toggleImageSelection(imageId: string) {
  if (props.multiple) {
    // 多选模式：切换选中状态
    if (selectedImages.value.has(imageId)) {
      selectedImages.value.delete(imageId);
    } else {
      selectedImages.value.add(imageId);
    }
  } else {
    // 单选模式：清空其他选择
    selectedImages.value.clear();
    selectedImages.value.add(imageId);
  }
}

// 预览图片
function handlePreviewImage(image: UploadingImage) {
  // 如果对话框打开，使用 uploadedImages；否则使用 confirmedImages
  const imageSource = modalVisible.value
    ? uploadedImages.value
    : confirmedImages.value;

  // 获取所有已上传完成的图片URL（优先使用服务器URL）
  const imageList = imageSource
    .filter((img) => !img.uploading && !img.failed)
    .map((img) => img.url || img.previewUrl || '');

  // 找到当前图片的索引
  const currentIndex = imageSource
    .filter((img) => !img.uploading && !img.failed)
    .findIndex((img) => img.id === image.id);

  previewImageUrls.value = imageList;
  previewInitialIndex.value = Math.max(currentIndex, 0);
  previewVisible.value = true;
}

// 关闭预览
function handleClosePreview() {
  previewVisible.value = false;
}

// 确认选择
function handleConfirm() {
  const selected = uploadedImages.value.filter(
    (img) => selectedImages.value.has(img.id) && !img.uploading && !img.failed,
  );

  if (selected.length === 0) {
    ElMessage.warning('请至少选择一张图片');
    return;
  }

  // 更新已确认的图片（不保留 previewUrl，避免 blob URL 失效）
  confirmedImages.value = selected.map((img) => {
    const { previewUrl, ...rest } = img;
    // 确保使用正确的 API URL
    if (!rest.url || !rest.url.startsWith('/basic-api/')) {
      rest.url = getFileStreamUrl(rest.id);
    }
    return rest;
  });

  // 更新 v-model
  if (props.multiple) {
    const ids = selected.map((img) => img.id);
    selectedImages.value = new Set(ids);
    emit('update:modelValue', ids);
    emit('change', ids);
  } else {
    const id = selected[0]?.id;
    selectedImages.value = new Set(id ? [id] : []);
    emit('update:modelValue', id);
    emit('change', id);
  }

  closeModal();
}

// 清空选择
function handleClear() {
  selectedImages.value.clear();
  confirmedImages.value = [];
  uploadedImages.value = [];
  emit('update:modelValue', props.multiple ? [] : undefined);
  emit('change', props.multiple ? [] : undefined);
}

// 从触发器中移除图片
function handleRemoveFromTrigger(imageId: string) {
  const index = confirmedImages.value.findIndex((img) => img.id === imageId);
  if (index !== -1) {
    const image = confirmedImages.value[index];
    if (image && image.previewUrl) {
      URL.revokeObjectURL(image.previewUrl);
    }
    confirmedImages.value.splice(index, 1);
  }

  selectedImages.value.delete(imageId);

  if (props.multiple) {
    const ids = confirmedImages.value.map((img) => img.id);
    emit('update:modelValue', ids);
    emit('change', ids);
  } else {
    emit('update:modelValue', undefined);
    emit('change', undefined);
  }
}

// 拖拽事件处理
function handleDragEnter(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
  isDragging.value = true;
}

function handleDragOver(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
}

function handleDragLeave(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
  // 只有当离开整个对话框区域时才设置为 false
  if (e.target === e.currentTarget) {
    isDragging.value = false;
  }
}

async function handleDrop(e: DragEvent) {
  e.preventDefault();
  e.stopPropagation();
  isDragging.value = false;

  const files = e.dataTransfer?.files;
  if (files && files.length > 0) {
    await handleImagesUpload([...files]);
  }
}

// 格式化文件大小
function formatFileSize(bytes?: number): string {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

// 格式化图片尺寸
function formatImageDimensions(image: UploadingImage): string {
  if (image.width && image.height) {
    return `${image.width} × ${image.height}`;
  }
  return '-';
}

// 清理预览 URL
function cleanupPreviewUrls() {
  uploadedImages.value.forEach((img) => {
    if (img.previewUrl) {
      URL.revokeObjectURL(img.previewUrl);
    }
  });
}

// 监听对话框关闭
watch(modalVisible, (newVal) => {
  if (!newVal) {
    cleanupPreviewUrls();
  }
});

// 组件卸载时清理
onBeforeUnmount(() => {
  cleanupPreviewUrls();
  confirmedImages.value.forEach((img) => {
    if (img.previewUrl) {
      URL.revokeObjectURL(img.previewUrl);
    }
  });
});

// 暴露方法
defineExpose({
  openModal,
});
</script>

<template>
  <div class="image-selector">
    <!-- 触发器 - 网格显示 -->
    <div
      class="image-selector-trigger-grid"
      :class="{
        disabled,
        multiple,
        'has-size': size,
      }"
    >
      <!-- 已选图片显示 -->
      <template v-if="selectedImagesList.length === 0">
        <!-- 空状态 - 点击上传 -->
        <div
          class="image-grid-item upload-placeholder"
          :class="{ 'is-circle': cropShape === 'circle' }"
          :style="triggerSizeStyle"
          @click="!disabled && openModal()"
        >
          <FileImageOutlined class="placeholder-icon" />
          <span class="placeholder-text">{{ placeholder }}</span>
        </div>
      </template>

      <template v-else>
        <!-- 单选模式：只显示一张图片 -->
        <template v-if="!multiple && selectedImagesList[0]">
          <div
            class="image-grid-item image-preview"
            :class="{ 'is-circle': cropShape === 'circle' }"
            :style="triggerSizeStyle"
          >
            <img
              :src="
                selectedImagesList[0].url || selectedImagesList[0].previewUrl
              "
              :alt="selectedImagesList[0].name"
              class="preview-image"
              :class="{ 'is-circle': cropShape === 'circle' }"
            />
            <!-- 悬停操作 -->
            <div class="image-hover-actions">
              <ElButton
                circle
                size="small"
                @click.stop="
                  handlePreviewImage(selectedImagesList[0] as UploadingImage)
                "
              >
                <EyeOutlined />
              </ElButton>
              <ElButton
                v-if="clearable"
                circle
                size="small"
                type="danger"
                @click.stop="handleRemoveFromTrigger(selectedImagesList[0].id)"
              >
                <DeleteOutlined />
              </ElButton>
            </div>
          </div>
        </template>

        <!-- 多选模式：显示所有图片 + 上传按钮 -->
        <template v-else>
          <div
            v-for="image in selectedImagesList"
            :key="image.id"
            class="image-grid-item image-preview"
            :class="{ 'is-circle': cropShape === 'circle' }"
            :style="triggerSizeStyle"
          >
            <img
              :src="image.url || image.previewUrl"
              :alt="image.name"
              class="preview-image"
              :class="{ 'is-circle': cropShape === 'circle' }"
            />
            <!-- 悬停操作 -->
            <div class="image-hover-actions">
              <ElButton
                circle
                size="small"
                @click.stop="handlePreviewImage(image as UploadingImage)"
              >
                <EyeOutlined />
              </ElButton>
              <ElButton
                circle
                size="small"
                type="danger"
                @click.stop="handleRemoveFromTrigger(image.id)"
              >
                <DeleteOutlined />
              </ElButton>
            </div>
          </div>

          <!-- 继续上传按钮 -->
          <div
            class="image-grid-item upload-placeholder"
            :style="triggerSizeStyle"
            @click="!disabled && openModal()"
          >
            <CloudUploadOutlined class="placeholder-icon" />
            <span class="placeholder-text">继续上传</span>
          </div>
        </template>
      </template>
    </div>

    <!-- Modal -->
    <ElDialog
      v-model="modalVisible"
      :title="multiple ? '选择图片' : '选择图片'"
      width="50%"
      class="image-selector-dialog"
    >
      <div
        class="image-selector-modal"
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

        <!-- 拖拽提示遮罩 -->
        <div v-if="isDragging" class="drag-overlay">
          <div class="upload-area">
            <CloudUploadOutlined class="upload-area-icon" />
            <div class="upload-area-title">松开鼠标上传图片</div>
            <div class="upload-area-hint">
              <span v-if="accept && accept.length > 0">
                支持格式：{{ accept.join(', ') }}
              </span>
              <span v-if="maxSize"> · 单张图片最大 {{ maxSize }}MB </span>
            </div>
          </div>
        </div>

        <!-- 上传区域（没有图片时显示） -->
        <div
          v-if="uploadedImages.length === 0 && !isDragging"
          class="upload-area-wrapper"
        >
          <div class="upload-area" @click="openFileSelector">
            <CloudUploadOutlined class="upload-area-icon" />
            <div class="upload-area-title">点击或拖拽图片到此处上传</div>
            <div class="upload-area-hint">
              <span v-if="accept && accept.length > 0">
                支持格式：{{ accept.join(', ') }}
              </span>
              <span v-if="maxSize"> · 单张图片最大 {{ maxSize }}MB </span>
              <span v-if="minWidth || minHeight || maxWidth || maxHeight">
                ·
                <template v-if="minWidth && minHeight">
                  最小尺寸 {{ minWidth }}×{{ minHeight }}px
                </template>
                <template v-if="maxWidth && maxHeight">
                  · 最大尺寸 {{ maxWidth }}×{{ maxHeight }}px
                </template>
              </span>
            </div>
          </div>
        </div>

        <!-- 图片网格（有图片时显示） -->
        <div v-if="uploadedImages.length > 0" class="image-selector-content">
          <!-- 顶部工具栏 -->
          <div class="toolbar">
            <div class="toolbar-info">
              <span class="info-text">
                已上传
                {{
                  uploadedImages.filter((img) => !img.uploading && !img.failed)
                    .length
                }}
                张
                <template v-if="!multiple">（单选）</template>
                <template v-else-if="selectedImages.size > 0">
                  · 已选 {{ selectedImages.size }} 张
                </template>
              </span>
            </div>
            <div class="toolbar-actions">
              <ElButton type="primary" @click="openFileSelector">
                <CloudUploadOutlined class="icon-wrapper" />
                {{ multiple ? '继续上传' : '替换图片' }}
              </ElButton>
            </div>
          </div>

          <!-- 图片网格 -->
          <ElScrollbar max-height="500px" class="image-grid-scrollbar">
            <div class="image-grid" :style="gridStyle">
              <div
                v-for="image in uploadedImages"
                :key="image.id"
                class="image-card"
                :class="{
                  selected: selectedImages.has(image.id),
                  uploading: image.uploading,
                  failed: image.failed,
                }"
              >
                <!-- 选中标记 -->
                <div
                  v-if="!image.uploading && !image.failed"
                  class="image-card-checkbox"
                  @click="toggleImageSelection(image.id)"
                >
                  <div class="checkbox-inner">
                    <IconifyIcon
                      v-if="selectedImages.has(image.id)"
                      icon="i-carbon:checkmark"
                    />
                  </div>
                </div>

                <!-- 图片预览 -->
                <div class="image-card-preview">
                  <img
                    :src="image.previewUrl || image.url"
                    :alt="image.name"
                    class="image-card-img"
                    @click="
                      !image.uploading &&
                      !image.failed &&
                      handlePreviewImage(image)
                    "
                  />

                  <!-- 上传中遮罩 -->
                  <div v-if="image.uploading" class="image-card-mask">
                    <ElProgress
                      type="circle"
                      :percentage="image.progress || 0"
                      :width="80"
                      :stroke-width="6"
                      color="#67c23a"
                    />
                  </div>

                  <!-- 失败遮罩 -->
                  <div v-if="image.failed" class="image-card-mask failed">
                    <div class="failed-content">
                      <IconifyIcon
                        icon="i-carbon:warning"
                        class="failed-icon"
                      />
                      <div class="failed-text">上传失败</div>
                      <ElButton
                        size="small"
                        type="primary"
                        @click="handleRetryUpload(image)"
                      >
                        重试
                      </ElButton>
                    </div>
                  </div>

                  <!-- 操作按钮 -->
                  <div v-if="!image.uploading" class="image-card-actions">
                    <ElButton
                      circle
                      size="small"
                      @click.stop="handlePreviewImage(image)"
                    >
                      <EyeOutlined />
                    </ElButton>
                    <ElButton
                      circle
                      size="small"
                      type="danger"
                      @click.stop="handleDeleteImage(image.id)"
                    >
                      <DeleteOutlined />
                    </ElButton>
                  </div>
                </div>

                <!-- 图片信息 -->
                <div v-if="showImageInfo" class="image-card-info">
                  <div class="image-card-name" :title="image.name">
                    {{ image.name }}
                  </div>
                  <div class="image-card-meta">
                    <span>{{ formatImageDimensions(image) }}</span>
                    <span>{{ formatFileSize(image.size) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </ElScrollbar>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <ElButton @click="closeModal">取消</ElButton>
          <ElButton
            type="primary"
            :disabled="
              selectedImages.size === 0 ||
              uploadedImages.some((img) => img.uploading || img.failed)
            "
            @click="handleConfirm"
          >
            确定
            <template v-if="selectedImages.size > 0">
              ({{ selectedImages.size }})
            </template>
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <!-- 图片预览 - 使用 Element Plus 的 ImageViewer -->
    <ElImageViewer
      v-if="previewVisible"
      :url-list="previewImageUrls"
      :initial-index="previewInitialIndex"
      :z-index="3000"
      :hide-on-click-modal="true"
      @close="handleClosePreview"
    />

    <!-- 图片裁剪对话框 -->
    <ImageCropper
      v-model="cropperVisible"
      :image-src="cropperImageSrc"
      :aspect-ratio="cropAspectRatio"
      :shape="cropShape"
      @confirm="handleCropConfirm"
    />
  </div>
</template>

<style lang="scss" scoped>
.image-selector {
  width: 100%;
}

// 触发器 - 网格布局
.image-selector-trigger-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;

  &.disabled {
    pointer-events: none;
    opacity: 0.6;
  }

  // 当设置了 size 时，使用 flex 布局
  &.has-size {
    display: flex;
    flex-wrap: wrap;
    grid-template-columns: none;
  }
}

// 网格项 - 正方形
.image-grid-item {
  position: relative;
  width: 100%;
  padding-top: 100%; // 1:1 正方形
  overflow: hidden;
  border: 2px dashed hsl(var(--border));
  border-radius: 8px;
  transition: all 0.3s;

  &.upload-placeholder {
    cursor: pointer;
    background-color: hsl(var(--background));
    border-style: dashed;

    &:hover {
      background-color: hsl(var(--primary) / 5%);
      border-color: hsl(var(--primary));
    }

    &.is-circle {
      border-radius: 50%;
    }

    .placeholder-icon,
    .placeholder-text {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
    }

    .placeholder-icon {
      margin-top: -20px;
      font-size: 32px;
      color: hsl(var(--muted-foreground));
    }

    .placeholder-text {
      margin-top: 20px;
      font-size: 14px;
      color: hsl(var(--muted-foreground));
      white-space: nowrap;
    }
  }

  &.image-preview {
    cursor: default;
    border-color: hsl(var(--border));
    border-style: solid;

    &:hover {
      border-color: hsl(var(--primary));

      .image-hover-actions {
        opacity: 1;
      }
    }
  }
}

// 预览图片
.preview-image {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;

  &.is-circle {
    border-radius: 50%;
  }
}

// 圆形容器
.image-preview.is-circle {
  overflow: hidden;
  border-radius: 50%;
}

// 悬停操作按钮
.image-hover-actions {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background-color: rgb(0 0 0 / 50%);
  opacity: 0;
  transition: opacity 0.3s;

  .el-button {
    background-color: rgb(255 255 255 / 90%);
    border: none;

    &:hover {
      background-color: rgb(255 255 255 / 100%);
    }

    &.el-button--danger {
      color: white;
      background-color: rgb(245 108 108 / 90%);

      &:hover {
        background-color: rgb(245 108 108 / 100%);
      }
    }
  }
}

// 对话框样式
.image-selector-dialog {
  :deep(.el-dialog__body) {
    padding: 0;
  }
}

.image-selector-modal {
  position: relative;
  min-height: 400px;
  background-color: hsl(var(--background));
}

.is-dragging {
  .drag-overlay {
    pointer-events: auto;
    opacity: 1;
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
    background-color: hsl(var(--background) / 98%);
    border-color: hsl(var(--primary));
    box-shadow: 0 4px 20px rgb(0 0 0 / 15%);

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
  }
}

.upload-area-wrapper {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 85%;
  transform: translate(-50%, -50%);
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
    font-size: 18px;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .upload-area-hint {
    font-size: 14px;
    color: hsl(var(--foreground) / 60%);
    text-align: center;

    span {
      margin: 0 4px;
    }
  }
}

.image-selector-content {
  padding: 20px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  margin-bottom: 20px;
  border-bottom: 1px solid hsl(var(--border));

  .toolbar-info {
    .info-text {
      font-size: 14px;
      color: hsl(var(--foreground) / 80%);
    }
  }

  .toolbar-actions {
    .icon-wrapper {
      margin-right: 4px;
      font-size: 18px;
    }
  }
}

.image-grid-scrollbar {
  :deep(.el-scrollbar__view) {
    padding: 4px;
  }
}

.image-grid {
  display: grid;
  gap: 16px;
}

.image-card {
  position: relative;
  overflow: hidden;
  cursor: pointer;
  background-color: hsl(var(--background));
  border: 2px solid transparent;
  border-radius: 8px;
  transition: all 0.3s;

  &:hover:not(.uploading):not(.failed) {
    box-shadow: 0 4px 12px rgb(0 0 0 / 10%);

    .image-card-actions {
      opacity: 1;
    }
  }

  &.selected {
    border-color: hsl(var(--primary));

    .image-card-checkbox .checkbox-inner {
      background-color: hsl(var(--primary));
      border-color: hsl(var(--primary));
    }
  }

  &.uploading {
    pointer-events: none;
  }

  &.failed {
    border-color: hsl(var(--destructive) / 30%);
  }
}

.image-card-checkbox {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 10;
  cursor: pointer;

  .checkbox-inner {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    font-size: 14px;
    color: white;
    background-color: rgb(0 0 0 / 30%);
    border: 2px solid white;
    border-radius: 4px;
    transition: all 0.3s;

    &:hover {
      background-color: hsl(var(--primary) / 80%);
      border-color: hsl(var(--primary));
    }
  }
}

.image-card-preview {
  position: relative;
  width: 100%;
  padding-top: 75%; // 4:3 aspect ratio，更合理的缩略图比例
  overflow: hidden;
  background-color: hsl(var(--background) / 50%);
}

.image-card-img {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
  object-fit: cover;
}

.image-card-mask {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgb(0 0 0 / 75%);

  // 上传进度样式优化
  :deep(.el-progress) {
    .el-progress__text {
      font-size: 14px !important;
      font-weight: 600;
      color: white !important;
    }

    .el-progress-circle__track {
      stroke: rgb(255 255 255 / 20%);
    }
  }

  &.failed {
    background-color: rgb(0 0 0 / 85%);
  }
}

.failed-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  color: white;

  .failed-icon {
    font-size: 32px;
    color: hsl(var(--destructive));
  }

  .failed-text {
    font-size: 14px;
  }
}

.image-card-actions {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 5;
  display: flex;
  gap: 8px;
  opacity: 0;
  transform: translate(-50%, -50%);
  transition: opacity 0.3s;
}

.image-card-info {
  padding: 8px 12px;
  background-color: hsl(var(--background));
}

.image-card-name {
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.image-card-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);

  span {
    &:first-child {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}

.dialog-footer {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
}
</style>
