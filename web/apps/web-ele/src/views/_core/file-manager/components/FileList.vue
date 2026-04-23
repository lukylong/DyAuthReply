<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';

import {
  FileArchive,
  FileAudio,
  FileImage,
  FileText,
  FileVideo,
  Folder,
  MoreVertical,
} from '@vben/icons';

import { $t } from '@vben/locales';
import {
  ElCheckbox,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElMessage,
  ElMessageBox,
  ElTable,
  ElTableColumn,
  ElImageViewer as ImageViewer,
} from 'element-plus';

import { deleteItem, getDownloadUrl, getFileStreamUrl } from '#/api/core/file';

import { useFileManager } from '../composables/useFileManager';
import RenameDialog from './RenameDialog.vue';

const {
  currentFolderId,
  viewMode,
  fileList,
  loading,
  selectedFileIds,
  navigateToFolder,
  fetchFiles,
  clearSelection,
} = useFileManager();

const renameDialogVisible = ref(false);
const currentItem = ref<any>(null);
const tableRef = ref<InstanceType<typeof ElTable>>();

// 图片预览状态
const previewVisible = ref(false);
const previewUrlList = ref<string[]>([]);
const previewInitialIndex = ref(0);

// 全选相关计算属性
const isAllSelected = computed(() => {
  return (
    fileList.value.length > 0 &&
    selectedFileIds.value.size === fileList.value.length
  );
});

const isIndeterminate = computed(() => {
  return (
    selectedFileIds.value.size > 0 &&
    selectedFileIds.value.size < fileList.value.length
  );
});

// 处理全选
const handleSelectAll = (val: any) => {
  if (val) {
    fileList.value.forEach((item) => selectedFileIds.value.add(item.id!));
    // 如果是列表视图，也需要同步表格的选中状态
    if (viewMode.value === 'list' && tableRef.value) {
      tableRef.value.toggleAllSelection();
    }
  } else {
    clearSelection();
    if (viewMode.value === 'list' && tableRef.value) {
      tableRef.value.clearSelection();
    }
  }
};

// 监听文件夹变化，自动刷新列表并清空选中
watch(currentFolderId, () => {
  clearSelection();
  fetchFiles();
});
onMounted(fetchFiles);

// 处理表格选中变化
const handleSelectionChange = (selection: any[]) => {
  selectedFileIds.value.clear();
  selection.forEach((item) => selectedFileIds.value.add(item.id));
};

// 处理 Grid 选中
const handleGridSelect = (id: string, value: boolean) => {
  if (value) {
    selectedFileIds.value.add(id);
  } else {
    selectedFileIds.value.delete(id);
  }
};

const isImage = (ext?: string) => {
  if (!ext) return false;
  const extension = ext.toLowerCase().replace('.', '');
  return ['bmp', 'gif', 'jpeg', 'jpg', 'png', 'svg', 'webp'].includes(
    extension,
  );
};

const getFileIcon = (type: string, ext?: string) => {
  if (type === 'folder') return Folder;
  if (!ext) return FileText;
  const extension = ext.toLowerCase().replace('.', '');

  if (['bmp', 'gif', 'jpeg', 'jpg', 'png', 'svg', 'webp'].includes(extension))
    return FileImage;
  if (['avi', 'mkv', 'mov', 'mp4', 'webm'].includes(extension))
    return FileVideo;
  if (['flac', 'mp3', 'ogg', 'wav'].includes(extension)) return FileAudio;
  if (['7z', 'gz', 'rar', 'tar', 'zip'].includes(extension)) return FileArchive;

  return FileText;
};

const handleItemClick = (item: any) => {
  console.log('Clicked item:', item);
  // 尝试兼容驼峰命名，防止请求库自动转换
  const type = item.file_type || item.fileType || item.type;

  if (type === 'folder') {
    navigateToFolder(item.id, item.name);
  } else if (isImage(item.file_ext)) {
    // 收集当前列表中的所有图片，构建预览列表
    const images = fileList.value.filter((f) => isImage(f.file_ext));
    previewUrlList.value = images.map((img) => getFileStreamUrl(img.id!));

    // 找到当前点击图片的索引
    const index = images.findIndex((img) => img.id === item.id);
    previewInitialIndex.value = index === -1 ? 0 : index;

    previewVisible.value = true;
  }
};

const closePreview = () => {
  previewVisible.value = false;
};

const handleDownload = (item: any) => {
  if (item.type === 'folder') {
    ElMessage.warning($t('file-manager.folderDownloadNotSupported'));
    return;
  }
  // 使用 window.open 或创建 a 标签下载
  const url = getDownloadUrl(item.storage_path || item.path);
  window.open(url, '_blank');
};

const handleDelete = async (item: any) => {
  try {
    await ElMessageBox.confirm(
      $t('file-manager.deleteConfirm', { name: item.name }),
      $t('file-manager.tip'),
      {
        type: 'warning',
        confirmButtonText: $t('file-manager.confirm'),
        cancelButtonText: $t('file-manager.cancel'),
      },
    );

    await deleteItem(item.id);
    ElMessage.success($t('file-manager.deleteSuccess'));
    fetchFiles();
  } catch (error) {
    if (error !== 'cancel') {
      console.error(error);
    }
  }
};

const openRenameDialog = (item: any) => {
  currentItem.value = item;
  renameDialogVisible.value = true;
};

const handleAction = (action: string, item: any) => {
  switch (action) {
    case 'delete': {
      handleDelete(item);
      break;
    }
    case 'download': {
      handleDownload(item);
      break;
    }
    case 'open': {
      handleItemClick(item);
      break;
    }
    case 'rename': {
      openRenameDialog(item);
      break;
    }
  }
};

// 格式化大小
const formatSize = (size?: number) => {
  if (size === undefined || size === null) return '-';
  if (size === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let s = size;
  while (s >= 1024 && i < units.length - 1) {
    s /= 1024;
    i++;
  }
  return `${s.toFixed(2)} ${units[i]}`;
};
</script>

<template>
  <div v-loading="loading" class="h-full w-full overflow-y-auto">
    <div
      v-if="fileList.length === 0 && !loading"
      class="flex h-full items-center justify-center"
    >
      <ElEmpty :description="$t('file-manager.noFiles')" />
    </div>

    <!-- Grid View -->
    <template v-else-if="viewMode === 'grid'">
      <div class="bg-background sticky top-0 z-20 flex items-center px-4 py-2">
        <ElCheckbox
          :model-value="isAllSelected"
          :indeterminate="isIndeterminate"
          @change="handleSelectAll"
        >
          {{ $t('file-manager.selectAll') }} {{ selectedFileIds.size > 0 ? `(${selectedFileIds.size})` : '' }}
        </ElCheckbox>
      </div>
      <div
        class="grid grid-cols-2 gap-4 px-4 py-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6"
      >
        <div
          v-for="item in fileList"
          :key="item.id"
          class="border-border bg-card hover:bg-accent group relative flex cursor-pointer flex-col rounded-lg border p-4 transition-colors"
          :class="{
            'ring-primary bg-accent ring-2': selectedFileIds.has(item.id!),
          }"
          @click="handleItemClick(item)"
        >
          <!-- Checkbox for Grid -->
          <div class="absolute left-2 top-2 z-10" @click.stop>
            <ElCheckbox
              :model-value="selectedFileIds.has(item.id!)"
              @change="(val) => handleGridSelect(item.id!, Boolean(val))"
            />
          </div>

          <!-- 操作按钮 -->
          <div class="absolute right-2 top-2 z-10" @click.stop>
            <ElDropdown trigger="click">
              <button
                class="invisible rounded-full p-1 hover:bg-gray-200 group-hover:visible dark:hover:bg-gray-700"
              >
                <MoreVertical class="size-4 text-gray-500" />
              </button>
              <template #dropdown>
                <ElDropdownMenu>
                  <ElDropdownItem @click="handleAction('open', item)">
                    {{ $t('file-manager.open') }}
                  </ElDropdownItem>
                  <ElDropdownItem @click="handleAction('download', item)">
                    {{ $t('file-manager.download') }}
                  </ElDropdownItem>
                  <ElDropdownItem divided @click="handleAction('rename', item)">
                    {{ $t('file-manager.rename') }}
                  </ElDropdownItem>
                  <!-- <ElDropdownItem>移动</ElDropdownItem> -->
                  <ElDropdownItem
                    class="text-red-500"
                    @click="handleAction('delete', item)"
                  >
                    {{ $t('file-manager.delete') }}
                  </ElDropdownItem>
                </ElDropdownMenu>
              </template>
            </ElDropdown>
          </div>

          <div
            class="flex flex-1 items-center justify-center overflow-hidden py-4"
          >
            <img
              v-if="isImage(item.file_ext)"
              :src="getFileStreamUrl(item.id!)"
              class="h-16 w-full object-contain"
              loading="lazy"
            />
            <component
              v-else
              :is="getFileIcon(item.file_type || item.type, item.file_ext)"
              class="size-16"
              :class="
                (item.file_type || item.type) === 'folder'
                  ? 'text-blue-500'
                  : 'text-gray-500'
              "
            />
          </div>
          <div class="px-2 text-center">
            <span
              class="block truncate text-sm font-medium"
              :title="item.name"
              >{{ item.name }}</span
            >
          </div>
        </div>
      </div>
    </template>

    <!-- List View -->
    <div v-else class="h-full px-4 py-2">
      <ElTable
        ref="tableRef"
        :data="fileList"
        style="width: 100%"
        row-class-name="file-list-row"
        @row-click="handleItemClick"
        @selection-change="handleSelectionChange"
      >
        <ElTableColumn type="selection" width="50" />
        <ElTableColumn width="60">
          <template #default="{ row }">
            <img
              v-if="isImage(row.file_ext)"
              :src="getFileStreamUrl(row.id!)"
              class="size-5 rounded object-cover"
              loading="lazy"
            />
            <component
              v-else
              :is="getFileIcon(row.file_type || row.type, row.file_ext)"
              class="size-5"
              :class="
                (row.file_type || row.type) === 'folder'
                  ? 'text-blue-500'
                  : 'text-gray-500'
              "
            />
          </template>
        </ElTableColumn>
        <ElTableColumn prop="name" :label="$t('file-manager.name')" min-width="200" sortable />
        <ElTableColumn prop="file_size" :label="$t('file-manager.size')" width="120">
          <template #default="{ row }">
            {{
              (row.file_type || row.type) === 'folder'
                ? '-'
                : formatSize(row.file_size || row.size)
            }}
          </template>
        </ElTableColumn>
        <ElTableColumn prop="updated_time" :label="$t('file-manager.modifiedTime')" width="180">
          <template #default="{ row }">
            {{ row.updated_time?.substring(0, 16) }}
          </template>
        </ElTableColumn>
        <ElTableColumn :label="$t('file-manager.actions')" width="80" fixed="right">
          <template #default="{ row }">
            <ElDropdown trigger="click" @click.stop>
              <button
                class="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <MoreVertical class="size-4" />
              </button>
              <template #dropdown>
                <ElDropdownMenu>
                  <ElDropdownItem @click="handleAction('open', row)">
                    {{ $t('file-manager.open') }}
                  </ElDropdownItem>
                  <ElDropdownItem @click="handleAction('download', row)">
                    {{ $t('file-manager.download') }}
                  </ElDropdownItem>
                  <ElDropdownItem divided @click="handleAction('rename', row)">
                    {{ $t('file-manager.rename') }}
                  </ElDropdownItem>
                  <!-- <ElDropdownItem>移动</ElDropdownItem> -->
                  <ElDropdownItem
                    class="text-red-500"
                    @click="handleAction('delete', row)"
                  >
                    {{ $t('file-manager.delete') }}
                  </ElDropdownItem>
                </ElDropdownMenu>
              </template>
            </ElDropdown>
          </template>
        </ElTableColumn>
      </ElTable>
    </div>

    <RenameDialog v-model:visible="renameDialogVisible" :item="currentItem" />

    <ImageViewer
      v-if="previewVisible"
      :url-list="previewUrlList"
      :initial-index="previewInitialIndex"
      @close="closePreview"
    />
  </div>
</template>

<style scoped>
:deep(.el-table__row td) {
  padding: 16px 0;
  border-bottom: none !important;
}

:deep(.el-table__inner-wrapper::before) {
  display: none;
}

:deep(.el-table__header th.el-table__cell) {
  border-bottom: none !important;
}
</style>
