<script setup lang="ts">
import { Cloud, Folder, FolderOpen } from '@vben/icons';
import { $t } from '@vben/locales';
import { ElTree } from 'element-plus';

import { getFileList } from '#/api/core/file';

import { useFileManager } from '../composables/useFileManager';

const { navigateToFolder } = useFileManager();

interface TreeData {
  id: null | string;
  name: string;
  isLeaf?: boolean;
}

const props = {
  label: 'name',
  children: 'children',
  isLeaf: 'isLeaf',
};

const loadNode = async (node: any, resolve: (data: TreeData[]) => void) => {
  if (node.level === 0) {
    return resolve([{ id: null, name: $t('file-manager.myFiles') }]);
  }

  try {
    const parentId = node.data.id;
    // 如果是根节点'我的文件' (id=null)，传给后端 parent_id=null
    const res = await getFileList({
      parent_id: parentId,
      type: 'folder',
      page: 1,
      pageSize: 100, // 暂时获取所有
    });

    const folders = res.items.map((item) => ({
      id: item.id!,
      name: item.name,
      isLeaf: false, // 假设所有文件夹都可能有子文件夹
    }));
    resolve(folders);
  } catch (error) {
    console.error('Failed to load folders', error);
    resolve([]);
  }
};

const handleNodeClick = (data: TreeData) => {
  navigateToFolder(data.id, data.name);
};
</script>

<template>
  <div class="bg-background flex h-full w-64 flex-col rounded-[10px]">
    <div class="text-foreground flex items-center gap-2 p-4 text-sm font-bold">
      <Cloud class="text-primary size-5" />
      {{ $t('file-manager.fileManagement') }}
    </div>
    <div class="flex-1 overflow-y-auto p-2">
      <ElTree
        lazy
        :load="loadNode"
        :props="props"
        :expand-on-click-node="false"
        node-key="id"
        highlight-current
        @node-click="handleNodeClick"
      >
        <template #default="{ node }">
          <span class="flex items-center gap-2 py-1">
            <FolderOpen v-if="node.expanded" class="size-4 text-blue-500" />
            <Folder v-else class="size-4 text-gray-500" />
            <span class="truncate text-sm">{{ node.label }}</span>
          </span>
        </template>
      </ElTree>
    </div>
  </div>
</template>

<style scoped lang="less">
/* 调整树节点高度 */
:deep(.el-tree-node__content) {
  height: 34px;
  line-height: 34px;
  border-radius: 6px;
  margin-bottom: 4px;
}

/* 调整展开/收起图标的对齐 */
:deep(.el-tree-node__expand-icon) {
  padding: 6px;
}

/* 选中节点的背景色 - 使用primary色 */
:deep(.el-tree-node.is-current > .el-tree-node__content) {
  background-color: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

/* 悬停效果 */
:deep(.el-tree-node__content:hover) {
  background-color: var(--el-fill-color-light);
}

/* 选中节点悬停时保持primary色 */
:deep(.el-tree-node.is-current > .el-tree-node__content:hover) {
  background-color: var(--el-color-primary-light-8);
}
</style>
