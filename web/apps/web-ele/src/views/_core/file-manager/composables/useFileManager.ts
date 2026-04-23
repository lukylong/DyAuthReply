import type { SystemFileManagerApi } from '#/api/core/file';

import { $t } from '@vben/locales';
import { ref, shallowRef } from 'vue';

import { getFileList } from '#/api/core/file';

// 使用单例模式或者在顶层组件 provide
const currentFolderId = ref<null | string>(null);
const viewMode = ref<'grid' | 'list'>('grid');
const selectedFileIds = ref<Set<string>>(new Set());
const fileList = shallowRef<SystemFileManagerApi.FileItem[]>([]);
const loading = ref(false);
const breadcrumbs = ref<Array<{ id: null | string; name: string }>>([
  { id: null, name: $t('file-manager.myFiles') },
]);
const createFolderDialogVisible = ref(false);

export function useFileManager() {
  const toggleViewMode = () => {
    viewMode.value = viewMode.value === 'grid' ? 'list' : 'grid';
  };

  const fetchFiles = async () => {
    loading.value = true;
    try {
      const res = await getFileList({
        parent_id: currentFolderId.value,
        page: 1,
        pageSize: 100,
      });
      fileList.value = res.items;
    } catch (error) {
      console.error(error);
    } finally {
      loading.value = false;
    }
  };

  const navigateToFolder = (folderId: null | string, folderName?: string) => {
    currentFolderId.value = folderId;
    selectedFileIds.value.clear();

    // 更新面包屑 (这里只是简单的逻辑，实际可能需要根据树结构查找完整路径)
    if (folderId === null) {
      breadcrumbs.value = [{ id: null, name: $t('file-manager.myFiles') }];
    } else if (folderName) {
      // 如果是点击面包屑导航回去，需要截断
      const index = breadcrumbs.value.findIndex((b) => b.id === folderId);
      if (index === -1) {
        // 进入新文件夹
        breadcrumbs.value.push({ id: folderId, name: folderName });
      } else {
        breadcrumbs.value = breadcrumbs.value.slice(0, index + 1);
      }
    }
    fetchFiles();
  };

  const toggleSelection = (id: string, multi: boolean) => {
    if (multi) {
      if (selectedFileIds.value.has(id)) {
        selectedFileIds.value.delete(id);
      } else {
        selectedFileIds.value.add(id);
      }
    } else {
      selectedFileIds.value.clear();
      selectedFileIds.value.add(id);
    }
  };

  const clearSelection = () => {
    selectedFileIds.value.clear();
  };

  const openCreateFolderDialog = () => {
    createFolderDialogVisible.value = true;
  };

  return {
    currentFolderId,
    viewMode,
    selectedFileIds,
    fileList,
    loading,
    breadcrumbs,
    createFolderDialogVisible,
    toggleViewMode,
    navigateToFolder,
    toggleSelection,
    clearSelection,
    fetchFiles,
    openCreateFolderDialog,
  };
}
