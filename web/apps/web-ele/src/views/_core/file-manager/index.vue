<script setup lang="ts">
import { Page } from '@vben/common-ui';

import CreateFolderDialog from './components/CreateFolderDialog.vue';
import FileList from './components/FileList.vue';
import FileToolbar from './components/FileToolbar.vue';
import FolderTree from './components/FolderTree.vue';
</script>

<template>
  <Page auto-content-height>
    <div class="flex h-full w-full gap-3">
      <FolderTree />
      <div
        class="bg-background flex flex-1 flex-col overflow-hidden rounded-[10px]"
      >
        <FileToolbar />
        <div class="flex-1 overflow-hidden">
          <FileList />
        </div>
      </div>
      <CreateFolderDialog />
    </div>
  </Page>
</template>
