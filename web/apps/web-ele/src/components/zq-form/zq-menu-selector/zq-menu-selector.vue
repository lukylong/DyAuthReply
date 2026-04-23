<script lang="ts" setup>
/**
 * 菜单选择器组件
 * 支持两种显示模式：dialog（带触发器的弹窗选择）和 popup（只有弹窗，无触发器）
 * 支持单选和多选
 */
import type { MenuItem, MenuSelectorProps } from './types';

import { computed, ref, watch } from 'vue';

import { ChevronDown, ChevronRight, IconifyIcon } from '@vben/icons';

import { ElOption, ElScrollbar, ElSelect, ElTag } from 'element-plus';

import { ZqDialog } from '#/components/zq-dialog';

defineOptions({
  name: 'ZqMenuSelector',
});

const props = withDefaults(defineProps<MenuSelectorProps>(), {
  modelValue: null,
  mode: 'dialog',
  placeholder: '请选择菜单',
  disabled: false,
  clearable: true,
  multiple: false,
  dialogTitle: '选择菜单',
  dialogWidth: '400px',
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: null | string | string[]): void;
  (e: 'change', menu: MenuItem | MenuItem[] | null): void;
  (e: 'select', menu: MenuItem): void;
}>();

// 菜单数据
const menuTreeData = ref<MenuItem[]>([]);
const menuTreeLoaded = ref(false);
const loading = ref(false);

// 弹窗可见性
const dialogVisible = ref(false);

// 单选时选中的菜单
const selectedMenu = ref<MenuItem | null>(null);

// 多选时选中的菜单列表
const selectedMenus = ref<MenuItem[]>([]);

// 展开的菜单 ID 集合
const expandedMenuIds = ref<Set<string>>(new Set());

// 处理菜单数据，添加 title 字段用于显示
const processMenuData = (menus: any[]): MenuItem[] => {
  return menus.map((menu) => ({
    ...menu,
    title: menu.title || menu.name,
    children: menu.children ? processMenuData(menu.children) : undefined,
  }));
};

// 加载菜单树
const loadMenuTree = async () => {
  if (menuTreeLoaded.value) return;
  loading.value = true;
  try {
    const { getAllMenuTreeApi } = await import('#/api/core/menu');
    const data = await getAllMenuTreeApi();
    menuTreeData.value = processMenuData(data || []);
    menuTreeLoaded.value = true;
    // 默认展开所有父级菜单
    expandAllParents();
  } catch (error) {
    console.error('Failed to load menu tree:', error);
  } finally {
    loading.value = false;
  }
};

// 根据 ID 在树中查找菜单
const findMenuInTree = (
  id: null | string | undefined,
  menus: MenuItem[],
): MenuItem | null => {
  if (!id) return null;
  for (const menu of menus) {
    if (menu.id === id) return menu;
    if (menu.children && menu.children.length > 0) {
      const found = findMenuInTree(id, menu.children);
      if (found) return found;
    }
  }
  return null;
};

// 扁平化菜单树用于 dialog 显示（支持折叠）
const flattenedMenuList = computed(() => {
  const result: {
    hasChildren: boolean;
    isExpanded: boolean;
    level: number;
    menu: MenuItem;
  }[] = [];
  const flatten = (
    menus: MenuItem[],
    level: number,
    parentExpanded: boolean,
  ) => {
    for (const menu of menus) {
      const hasChildren = !!(menu.children && menu.children.length > 0);
      const isExpanded = expandedMenuIds.value.has(menu.id);

      // 只有父级展开时才显示子菜单
      if (parentExpanded) {
        result.push({ menu, level, hasChildren, isExpanded });
      }

      // 如果有子菜单且已展开，继续遍历
      if (hasChildren && isExpanded) {
        flatten(menu.children!, level + 1, true);
      }
    }
  };
  flatten(menuTreeData.value, 0, true);
  return result;
});

// 切换菜单展开/折叠状态
const toggleExpand = (menuId: string, event: Event) => {
  event.stopPropagation();
  if (expandedMenuIds.value.has(menuId)) {
    expandedMenuIds.value.delete(menuId);
  } else {
    expandedMenuIds.value.add(menuId);
  }
};

// 初始化时展开所有父级菜单
const expandAllParents = () => {
  const expand = (menus: MenuItem[]) => {
    for (const menu of menus) {
      if (menu.children && menu.children.length > 0) {
        expandedMenuIds.value.add(menu.id);
        expand(menu.children);
      }
    }
  };
  expand(menuTreeData.value);
};

// 判断菜单是否被选中（多选模式）
const isMenuSelected = (menuId: string): boolean => {
  if (props.multiple) {
    return selectedMenus.value.some((m) => m.id === menuId);
  }
  return selectedMenu.value?.id === menuId;
};

// 打开弹窗
const openDialog = async () => {
  if (props.disabled) return;
  dialogVisible.value = true;
  await loadMenuTree();
};

// 选择菜单
const selectMenu = (menu: MenuItem) => {
  if (props.multiple) {
    // 多选模式
    const index = selectedMenus.value.findIndex((m) => m.id === menu.id);
    if (index === -1) {
      // 未选中，添加选择
      selectedMenus.value.push(menu);
    } else {
      // 已选中，取消选择
      selectedMenus.value.splice(index, 1);
    }
    const ids = selectedMenus.value.map((m) => m.id);
    emit('update:modelValue', ids.length > 0 ? ids : null);
    emit(
      'change',
      selectedMenus.value.length > 0 ? [...selectedMenus.value] : null,
    );
  } else {
    // 单选模式
    selectedMenu.value = menu;
    emit('update:modelValue', menu.id);
    emit('change', menu);
    dialogVisible.value = false;
  }
  emit('select', menu);
};

// 移除选中的菜单（多选模式）
const removeMenu = (menuId: string) => {
  const index = selectedMenus.value.findIndex((m) => m.id === menuId);
  if (index !== -1) {
    selectedMenus.value.splice(index, 1);
    const ids = selectedMenus.value.map((m) => m.id);
    emit('update:modelValue', ids.length > 0 ? ids : null);
    emit(
      'change',
      selectedMenus.value.length > 0 ? [...selectedMenus.value] : null,
    );
  }
};

// 暴露方法供外部调用
defineExpose({
  open: openDialog,
  close: () => {
    dialogVisible.value = false;
  },
  loadMenuTree,
});

// 清除选择
const clearSelection = () => {
  if (props.multiple) {
    selectedMenus.value = [];
  } else {
    selectedMenu.value = null;
  }
  emit('update:modelValue', null);
  emit('change', null);
};

// 监听 modelValue 变化，更新选中菜单
watch(
  () => props.modelValue,
  async (newVal) => {
    if (newVal && !menuTreeLoaded.value) {
      await loadMenuTree();
    }

    if (props.multiple) {
      // 多选模式
      selectedMenus.value =
        Array.isArray(newVal) && newVal.length > 0 && menuTreeLoaded.value
          ? newVal
              .map((id) => findMenuInTree(id, menuTreeData.value))
              .filter((m): m is MenuItem => m !== null)
          : [];
    } else {
      // 单选模式
      if (typeof newVal === 'string' && menuTreeLoaded.value) {
        selectedMenu.value = findMenuInTree(newVal, menuTreeData.value);
      } else if (!newVal) {
        selectedMenu.value = null;
      }
    }
  },
  { immediate: true },
);
</script>

<template>
  <!-- Popup 模式（只有弹窗，无触发器） -->
  <template v-if="mode === 'popup'">
    <ZqDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      :width="dialogWidth"
      append-to-body
      :show-footer="false"
    >
      <div v-if="menuTreeLoaded" class="h-[400px]">
        <ElScrollbar height="380px">
          <div class="space-y-1 pr-2">
            <div
              v-for="item in flattenedMenuList"
              :key="item.menu.id"
              class="hover:bg-primary/10 flex cursor-pointer items-center gap-1 rounded px-2 py-2 transition-colors"
              :class="{
                'bg-primary/10': isMenuSelected(item.menu.id),
              }"
              :style="{ paddingLeft: `${8 + item.level * 16}px` }"
              @click="selectMenu(item.menu)"
            >
              <!-- 折叠/展开按钮 -->
              <span
                v-if="item.hasChildren"
                class="flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded hover:bg-gray-200 dark:hover:bg-gray-700"
                @click="toggleExpand(item.menu.id, $event)"
              >
                <ChevronDown
                  v-if="item.isExpanded"
                  class="h-4 w-4 text-gray-400"
                />
                <ChevronRight v-else class="h-4 w-4 text-gray-400" />
              </span>
              <span v-else class="w-5 shrink-0"></span>

              <IconifyIcon
                v-if="item.menu.icon"
                :icon="item.menu.icon"
                class="h-4 w-4 shrink-0 text-gray-500"
              />
              <span class="flex-1 truncate text-sm">{{ item.menu.title }}</span>
              <span
                v-if="multiple && isMenuSelected(item.menu.id)"
                class="text-primary shrink-0 text-sm"
                >✓</span>
            </div>
            <div
              v-if="flattenedMenuList.length === 0"
              class="py-8 text-center text-sm text-gray-400"
            >
              暂无可用菜单
            </div>
          </div>
        </ElScrollbar>
      </div>
      <div
        v-else
        class="flex h-[400px] items-center justify-center text-gray-400"
      >
        加载中...
      </div>
    </ZqDialog>
  </template>

  <!-- Dialog 模式（使用 ElSelect 作为触发器） -->
  <template v-else>
    <div class="zq-menu-selector w-full">
      <ElSelect
        :model-value="
          multiple ? selectedMenus.map((m) => m.id) : selectedMenu?.id || ''
        "
        :placeholder="placeholder"
        :disabled="disabled"
        :clearable="clearable"
        :multiple="multiple"
        collapse-tags
        collapse-tags-tooltip
        class="w-full"
        popper-class="zq-menu-selector-hidden-dropdown"
        @click="openDialog"
        @clear="clearSelection"
        @remove-tag="removeMenu"
      >
        <!-- 单选时的选项 -->
        <ElOption
          v-if="!multiple && selectedMenu"
          :value="selectedMenu.id"
          :label="selectedMenu.title || selectedMenu.name"
        />
        <!-- 多选时的选项 -->
        <template v-if="multiple">
          <ElOption
            v-for="menu in selectedMenus"
            :key="menu.id"
            :value="menu.id"
            :label="menu.title || menu.name"
          />
        </template>
      </ElSelect>
    </div>

    <!-- 菜单选择弹窗 -->
    <ZqDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      :width="dialogWidth"
      append-to-body
      :show-footer="multiple"
    >
      <div v-if="menuTreeLoaded" class="h-[600px]">
        <ElScrollbar height="590px">
          <div class="space-y-1 pr-2">
            <div
              v-for="item in flattenedMenuList"
              :key="item.menu.id"
              class="hover:bg-primary/10 flex cursor-pointer items-center gap-1 rounded px-2 py-2 transition-colors"
              :class="{
                'bg-primary/10': isMenuSelected(item.menu.id),
              }"
              :style="{ paddingLeft: `${8 + item.level * 16}px` }"
              @click="selectMenu(item.menu)"
            >
              <!-- 折叠/展开按钮 -->
              <span
                v-if="item.hasChildren"
                class="flex h-5 w-5 shrink-0 cursor-pointer items-center justify-center rounded hover:bg-gray-200 dark:hover:bg-gray-700"
                @click="toggleExpand(item.menu.id, $event)"
              >
                <ChevronDown
                  v-if="item.isExpanded"
                  class="h-4 w-4 text-gray-400"
                />
                <ChevronRight v-else class="h-4 w-4 text-gray-400" />
              </span>
              <span v-else class="w-5 shrink-0"></span>

              <IconifyIcon
                v-if="item.menu.icon"
                :icon="item.menu.icon"
                class="h-4 w-4 shrink-0 text-gray-500"
              />
              <span class="flex-1 truncate text-sm">{{ item.menu.title }}</span>
              <span
                v-if="multiple && isMenuSelected(item.menu.id)"
                class="text-primary shrink-0 text-sm"
                >✓</span>
            </div>
            <div
              v-if="flattenedMenuList.length === 0"
              class="py-8 text-center text-sm text-gray-400"
            >
              暂无可用菜单
            </div>
          </div>
        </ElScrollbar>
      </div>
      <div
        v-else
        class="flex h-[600px] items-center justify-center text-gray-400"
      >
        加载中...
      </div>

      <!-- 多选模式下显示已选数量和确认按钮 -->
      <template v-if="multiple" #footer>
        <div class="flex items-center justify-between">
          <span class="text-muted-foreground text-sm">
            已选择 {{ selectedMenus.length }} 项
          </span>
          <ElTag
            v-if="selectedMenus.length > 0"
            type="primary"
            class="cursor-pointer"
            @click="dialogVisible = false"
          >
            确认
          </ElTag>
        </div>
      </template>
    </ZqDialog>
  </template>
</template>

<style scoped>
.zq-menu-selector :deep(.el-select__wrapper) {
  cursor: pointer;
}
</style>

<style>
/* 隐藏下拉菜单，因为我们使用弹窗选择 */
.zq-menu-selector-hidden-dropdown {
  display: none !important;
}
</style>
