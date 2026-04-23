<script setup lang="ts">
import { ref } from 'vue';

import {
  ChevronRight,
  Home,
  LayoutGrid,
  List,
  Plus,
  RotateCw,
  Search,
  Trash2,
  Upload,
} from '@vben/icons';

import { $t } from '@vben/locales';
import {
  ElBreadcrumb,
  ElBreadcrumbItem,
  ElButton,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElInput,
  ElMessage,
  ElMessageBox,
} from 'element-plus';

import { batchDelete, uploadFile } from '#/api/core/file';

import { useFileManager } from '../composables/useFileManager';

const {
  viewMode,
  breadcrumbs,
  currentFolderId,
  selectedFileIds,
  navigateToFolder,
  openCreateFolderDialog,
  fetchFiles,
  clearSelection,
} = useFileManager();

const fileInputRef = ref<HTMLInputElement | null>(null);
// ... (rest of the code)

const handleBatchDelete = async () => {
  if (selectedFileIds.value.size === 0) return;

  try {
    await ElMessageBox.confirm(
      $t('file-manager.batchDeleteConfirm', {
        count: selectedFileIds.value.size,
      }),
      $t('file-manager.tip'),
      {
        type: 'warning',
        confirmButtonText: $t('file-manager.confirm'),
        cancelButtonText: $t('file-manager.cancel'),
      },
    );

    await batchDelete({ ids: [...selectedFileIds.value] });
    ElMessage.success($t('file-manager.deleteSuccess'));
    clearSelection();
    fetchFiles();
  } catch (error) {
    if (error !== 'cancel') {
      console.error(error);
    }
  }
};
const folderInputRef = ref<HTMLInputElement | null>(null);
const uploading = ref(false);

const handleBreadcrumbClick = (id: null | string, name: string) => {
  navigateToFolder(id, name);
};

const handleCreateFolder = () => {
  openCreateFolderDialog();
};

const handleUploadFile = () => {
  fileInputRef.value?.click();
};

const handleUploadFolder = () => {
  folderInputRef.value?.click();
};

const handleFileChange = async (event: Event) => {
  const target = event.target as HTMLInputElement;
  const files = target.files;

  if (!files || files.length === 0) return;

  uploading.value = true;
  let successCount = 0;
  let failCount = 0;

  try {
    // 串行上传，避免并发过大
    for (const file of files) {
      if (!file) continue;

      try {
        await uploadFile(file, currentFolderId.value || undefined);
        successCount++;
      } catch (error) {
        console.error(`Failed to upload ${file.name}`, error);
        failCount++;
      }
    }

    if (successCount > 0) {
      ElMessage.success($t('file-manager.uploadSuccess', { count: successCount }));
      fetchFiles();
    }
    if (failCount > 0) {
      ElMessage.error($t('file-manager.uploadFailed', { count: failCount }));
    }
  } catch (error) {
    console.error(error);
    ElMessage.error($t('file-manager.uploadError'));
  } finally {
    uploading.value = false;
    // 清空 input，允许重复上传同名文件（虽然业务逻辑可能需要去重，但前端先允许）
    target.value = '';
  }
};
</script>

<template>
  <div
    class="border-border flex items-center justify-between border-b px-4 py-3"
  >
    <!-- Hidden Inputs -->
    <input
      ref="fileInputRef"
      type="file"
      multiple
      class="hidden"
      @change="handleFileChange"
    />
    <input
      ref="folderInputRef"
      type="file"
      webkitdirectory
      class="hidden"
      @change="handleFileChange"
    />

    <!-- Breadcrumbs -->
    <div class="mr-4 flex flex-1 items-center overflow-hidden">
      <ElBreadcrumb :separator-icon="ChevronRight">
        <ElBreadcrumbItem
          v-for="(item, index) in breadcrumbs"
          :key="item.id || 'root'"
        >
          <span
            class="hover:text-primary flex cursor-pointer items-center gap-1"
            :class="{
              'text-foreground font-bold': index === breadcrumbs.length - 1,
            }"
            @click="handleBreadcrumbClick(item.id, item.name)"
          >
            <Home v-if="index === 0" class="size-4" />
            {{ item.name }}
          </span>
        </ElBreadcrumbItem>
      </ElBreadcrumb>
    </div>

    <!-- Actions -->
    <div class="flex flex-shrink-0 items-center gap-2 sm:gap-3">
      <ElInput :placeholder="$t('file-manager.search')" class="w-32 sm:w-48">
        <template #prefix>
          <Search class="size-4" />
        </template>
      </ElInput>

      <div class="flex items-center rounded-lg border p-1">
        <button
          class="hover:bg-accent p-2"
          :class="{ 'bg-accent text-primary': viewMode === 'list' }"
          @click="viewMode = 'list'"
          :title="$t('file-manager.listView')"
        >
          <List class="size-4" />
        </button>
        <button
          class="hover:bg-accent p-2"
          :class="{ 'bg-accent text-primary': viewMode === 'grid' }"
          @click="viewMode = 'grid'"
          :title="$t('file-manager.gridView')"
        >
          <LayoutGrid class="size-4" />
        </button>
      </div>

      <ElButton circle @click="fetchFiles">
        <template #icon>
          <RotateCw class="size-4" :class="{ 'animate-spin': uploading }" />
        </template>
      </ElButton>

      <ElButton
        v-if="selectedFileIds.size > 0"
        type="danger"
        plain
        @click="handleBatchDelete"
      >
        <Trash2 class="mr-2 size-4" /> {{ $t('file-manager.batchDelete') }}
      </ElButton>

      <ElButton
        type="primary"
        plain
        @click="handleCreateFolder"
        class="hidden sm:flex"
      >
        <Plus class="mr-2 size-4" /> {{ $t('file-manager.newFolder') }}
      </ElButton>

      <ElDropdown trigger="click">
        <ElButton type="primary" :loading="uploading" class="hidden sm:flex">
          <Upload class="mr-2 size-4" /> {{ $t('file-manager.upload') }}
        </ElButton>
        <ElButton
          circle
          type="primary"
          :loading="uploading"
          class="flex sm:hidden"
        >
          <Plus class="size-4" />
        </ElButton>
        <template #dropdown>
          <ElDropdownMenu>
            <ElDropdownItem @click="handleUploadFile">{{ $t('file-manager.uploadFile') }}</ElDropdownItem>
            <ElDropdownItem @click="handleUploadFolder">
              {{ $t('file-manager.uploadFolder') }}
            </ElDropdownItem>
          </ElDropdownMenu>
        </template>
      </ElDropdown>
    </div>
  </div>
</template>
