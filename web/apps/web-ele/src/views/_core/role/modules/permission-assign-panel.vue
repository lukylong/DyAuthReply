<!-- eslint-disable vue/no-unused-vars -->
<script lang="ts" setup>
import type { Role } from '#/api/core/role';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  provide,
  ref,
  watch,
} from 'vue';

import { $t } from '@vben/locales';

import {
  ElButton,
  ElCard,
  ElEmpty,
  ElMessage,
  ElScrollbar,
  ElSkeleton,
  ElSkeletonItem,
} from 'element-plus';

import {
  getMenuPermissionsApi,
  getRoleMenusApi,
  updateRoleMenusPermissionsApi,
} from '#/api/core/role';

import RenderMenuTree from './render-menu-tree.vue';

interface MenuNode {
  id: string;
  name: string;
  label?: string;
  parent_id?: string;
  permission_count?: number;
  children?: Array<MenuNode | PermissionNode>;
}

interface PermissionNode {
  id: string;
  name: string;
  label?: string;
  code?: string;
  permission_type?: number;
  permission_type_display?: string;
}

interface Props {
  role?: Role;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  success: [];
}>();

const loading = ref(false);
const saving = ref(false);
const loadingPermissions = ref(false);
const treeData = ref<MenuNode[]>([]);
const selectedMenuIds = ref<Set<string>>(new Set());
const selectedPermissions = ref<Set<string>>(new Set());
const expandedMenuIds = ref<Set<string>>(new Set());
const selectedMenuId = ref<string>();
const menuPermissionsCache = ref<Record<string, PermissionNode[]>>({});

const layoutContainerRef = ref<HTMLElement | null>(null);
const scrollAreaHeight = ref(400);
const menuScrollHeight = computed(() =>
  Math.max(scrollAreaHeight.value - 56, 240),
);

function updateScrollAreaHeight() {
  nextTick(() => {
    if (!layoutContainerRef.value) return;
    const rect = layoutContainerRef.value.getBoundingClientRect();
    const padding = 60; // 预留底部空间
    const availableHeight = window.innerHeight - rect.top - padding;
    scrollAreaHeight.value = Math.max(Math.floor(availableHeight), 240);
  });
}

onMounted(() => {
  updateScrollAreaHeight();
  window.addEventListener('resize', updateScrollAreaHeight);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateScrollAreaHeight);
});

// 权限类型映射
function getPermissionTypeName(type: number): string {
  const typeMap: Record<number, string> = {
    0: $t('role.permissions.types.button'),
    1: $t('role.permissions.types.api'),
    2: $t('role.permissions.types.data'),
    3: $t('role.permissions.types.other'),
  };
  return typeMap[type] || '';
}

/**
 * 计算菜单总数
 */
const totalMenuCount = computed(() => {
  let count = 0;
  function countMenus(nodes: Array<MenuNode | PermissionNode> | undefined) {
    if (!nodes) return;
    nodes.forEach((node) => {
      if (!('code' in node)) {
        count++;
        if ((node as MenuNode).children) {
          countMenus((node as MenuNode).children);
        }
      }
    });
  }
  countMenus(treeData.value);
  return count;
});

/**
 * 当前选中菜单的权限，按类型分组
 */
const currentMenuPermissions = computed(() => {
  if (!selectedMenuId.value) return {};

  // 从缓存中获取权限
  const permissions = menuPermissionsCache.value[selectedMenuId.value] || [];

  // 按类型分组
  const grouped: Record<number, PermissionNode[]> = {
    0: [],
    1: [],
    2: [],
    3: [],
  };

  permissions.forEach((perm) => {
    const type = perm.permission_type ?? 3; // 默认为其他权限
    if (grouped[type]) {
      grouped[type].push(perm);
    }
  });

  return grouped;
});

/**
 * 加载菜单列表（不包含权限）
 */
async function loadMenuTree() {
  if (!props.role?.id) return;

  try {
    loading.value = true;
    const data = await getRoleMenusApi(props.role.id);

    // 使用后端返回的菜单树结构
    treeData.value = data.menu_tree || [];

    // 初始化已选菜单
    const selectedMenuIdsList = data.selected_menu_ids || [];
    selectedMenuIds.value = new Set(selectedMenuIdsList);

    // 清空权限缓存和选中状态
    menuPermissionsCache.value = {};
    selectedPermissions.value.clear();

    // 默认展开全部菜单
    expandAllMenus();
  } catch (error) {
    console.error($t('role.permissions.loadMenuFailed'), error);
    ElMessage.error($t('role.permissions.loadMenuFailed'));
  } finally {
    loading.value = false;
    updateScrollAreaHeight();
  }
}

/**
 * 加载指定菜单的权限
 */
async function loadMenuPermissions(menuId: string) {
  if (!props.role?.id) return;

  // 如果已经缓存，直接返回
  if (menuPermissionsCache.value[menuId]) {
    return;
  }

  try {
    loadingPermissions.value = true;
    const data = await getMenuPermissionsApi(props.role.id, menuId);

    // 缓存权限数据
    menuPermissionsCache.value[menuId] = data.permissions || [];

    // 初始化已选中的权限
    data.permissions.forEach((perm: any) => {
      if (perm.checked) {
        selectedPermissions.value.add(perm.id);
      }
    });
  } catch (error) {
    console.error($t('role.permissions.loadPermissionsFailed'), error);
    ElMessage.error($t('role.permissions.loadPermissionsFailed'));
  } finally {
    loadingPermissions.value = false;
  }
}

/**
 * 递归获取菜单的所有子孙菜单ID
 */
function getDescendantMenuIds(menuId: string): string[] {
  const ids: string[] = [];

  function findAndCollect(nodes: Array<MenuNode | PermissionNode> | undefined) {
    if (!nodes) return;
    nodes.forEach((node) => {
      if (!('code' in node)) {
        const menu = node as MenuNode;
        if (menu.id === menuId) {
          // 找到目标菜单，收集所有子孙菜单
          collectDescendants(menu.children);
        } else {
          // 继续查找
          findAndCollect(menu.children);
        }
      }
    });
  }

  function collectDescendants(
    nodes: Array<MenuNode | PermissionNode> | undefined,
  ) {
    if (!nodes) return;
    nodes.forEach((node) => {
      if (!('code' in node)) {
        const menu = node as MenuNode;
        ids.push(menu.id);
        collectDescendants(menu.children);
      }
    });
  }

  findAndCollect(treeData.value);
  return ids;
}

/**
 * 递归查找菜单的父级菜单ID
 */
function getAncestorMenuIds(menuId: string): string[] {
  const ancestors: string[] = [];

  function findParent(
    nodes: Array<MenuNode | PermissionNode> | undefined,
    targetId: string,
    parentId?: string,
  ): boolean {
    if (!nodes) return false;

    for (const node of nodes) {
      if (!('code' in node)) {
        const menu = node as MenuNode;
        if (menu.id === targetId) {
          if (parentId) {
            ancestors.push(parentId);
            // 继续向上查找
            findParent(treeData.value, parentId);
          }
          return true;
        }
        if (findParent(menu.children, targetId, menu.id)) {
          return true;
        }
      }
    }
    return false;
  }

  findParent(treeData.value, menuId);
  return ancestors;
}

/**
 * 检查菜单是否有任何子节点被选中
 */
function hasSelectedDescendants(menuId: string): boolean {
  const descendants = getDescendantMenuIds(menuId);
  return descendants.some((id) => selectedMenuIds.value.has(id));
}

/**
 * 切换菜单选中状态（级联选择）
 */
function toggleMenu(menuId: string) {
  if (selectedMenuIds.value.has(menuId)) {
    // 取消选中：取消该菜单及其所有子孙菜单
    selectedMenuIds.value.delete(menuId);
    const descendants = getDescendantMenuIds(menuId);
    descendants.forEach((id) => selectedMenuIds.value.delete(id));

    // 检查父级菜单是否还应该保持选中
    const ancestors = getAncestorMenuIds(menuId);
    ancestors.forEach((ancestorId) => {
      // 如果父级菜单没有任何子节点被选中，则取消选中父级
      if (!hasSelectedDescendants(ancestorId)) {
        selectedMenuIds.value.delete(ancestorId);
      }
    });

    // 如果取消选中的菜单正好是当前显示权限的菜单，清除右侧显示
    if (selectedMenuId.value === menuId) {
      selectedMenuId.value = undefined;
    }
  } else {
    // 选中：选中该菜单及其所有子孙菜单和所有父级菜单
    selectedMenuIds.value.add(menuId);
    const descendants = getDescendantMenuIds(menuId);
    descendants.forEach((id) => selectedMenuIds.value.add(id));

    // 自动选中所有父级菜单
    const ancestors = getAncestorMenuIds(menuId);
    ancestors.forEach((id) => selectedMenuIds.value.add(id));
  }
  updateScrollAreaHeight();
}

/**
 * 选择菜单（用于显示权限）
 */
async function selectMenu(menuId: string) {
  selectedMenuId.value = menuId;
  // 加载该菜单的权限
  await loadMenuPermissions(menuId);
  updateScrollAreaHeight();
}

/**
 * 切换菜单展开/折叠
 */
function toggleMenuExpanded(menuId: string) {
  if (expandedMenuIds.value.has(menuId)) {
    expandedMenuIds.value.delete(menuId);
  } else {
    expandedMenuIds.value.add(menuId);
  }
  updateScrollAreaHeight();
}

/**
 * 切换权限选中状态
 */
function togglePermission(permissionId: string) {
  if (selectedPermissions.value.has(permissionId)) {
    selectedPermissions.value.delete(permissionId);
  } else {
    selectedPermissions.value.add(permissionId);
  }
}

/**
 * 全选所有菜单
 */
function selectAllMenus() {
  function collectMenus(nodes: Array<MenuNode | PermissionNode> | undefined) {
    if (!nodes) return;
    nodes.forEach((node) => {
      if (!('code' in node)) {
        // 这是菜单项
        selectedMenuIds.value.add(node.id);
        if ((node as MenuNode).children) {
          collectMenus((node as MenuNode).children);
        }
      }
    });
  }
  collectMenus(treeData.value);
}

/**
 * 反选所有菜单
 */
function unselectAllMenus() {
  selectedMenuIds.value.clear();
}

/**
 * 全选指定类型的权限
 */
function selectPermissionsByType(type: number) {
  if (!selectedMenuId.value) return;
  const permissions = currentMenuPermissions.value[type] || [];
  permissions.forEach((perm) => {
    selectedPermissions.value.add(perm.id);
  });
}

/**
 * 反选指定类型的权限
 */
function unselectPermissionsByType(type: number) {
  if (!selectedMenuId.value) return;
  const permissions = currentMenuPermissions.value[type] || [];
  permissions.forEach((perm) => {
    selectedPermissions.value.delete(perm.id);
  });
}

/**
 * 展开所有菜单
 */
function expandAllMenus() {
  function expandAll(nodes: Array<MenuNode | PermissionNode> | undefined) {
    if (!nodes) return;
    nodes.forEach((node) => {
      if (!('code' in node)) {
        // 这是菜单项
        expandedMenuIds.value.add(node.id);
        if ((node as MenuNode).children) {
          expandAll((node as MenuNode).children);
        }
      }
    });
  }
  expandAll(treeData.value);
}

/**
 * 保存菜单和权限选择
 */
async function saveSelection() {
  if (!props.role?.id) {
    ElMessage.warning($t('role.permissions.noRoleSelected'));
    return;
  }

  // if (selectedMenuIds.value.size === 0) {
  //   ElMessage.warning('请至少选择一个菜单');
  //   return;
  // }

  try {
    saving.value = true;
    const menuIds = [...selectedMenuIds.value];
    const permissionIds = [...selectedPermissions.value];

    console.log('保存 - 选中的菜单:', menuIds);
    console.log('保存 - 选中的权限:', permissionIds);

    await updateRoleMenusPermissionsApi(props.role.id, {
      menu_ids: menuIds,
      permission_ids: permissionIds,
    });

    ElMessage.success($t('role.permissions.saveSuccess'));
    emit('success');
  } catch (error) {
    console.error($t('role.permissions.saveFailed'), error);
    ElMessage.error($t('role.permissions.saveFailed'));
  } finally {
    saving.value = false;
  }
}

watch(
  () => props.role?.id,
  () => {
    if (props.role?.id) {
      expandedMenuIds.value.clear();
      selectedMenuId.value = undefined;
      loadMenuTree();
    } else {
      treeData.value = [];
      selectedMenuIds.value.clear();
      selectedPermissions.value.clear();
      expandedMenuIds.value.clear();
      selectedMenuId.value = undefined;
    }
    updateScrollAreaHeight();
  },
);

// 提供给递归组件使用
provide('toggleMenu', toggleMenu);
provide('selectMenu', selectMenu);
provide('toggleMenuExpanded', toggleMenuExpanded);
provide('togglePermission', togglePermission);
provide('selectedMenuIds', selectedMenuIds);
provide('selectedMenuId', selectedMenuId);
provide('selectedPermissions', selectedPermissions);
provide('expandedMenuIds', expandedMenuIds);
</script>

<template>
  <ElCard
    class="h-full"
    :class="[role ? 'flex flex-col' : 'empty-state-card']"
    shadow="never"
    style="border: none"
    :body-style="!role ? { height: '100%', padding: 0 } : { padding: '6px' }"
  >
    <!-- 未选择角色时显示空状态 -->
    <div v-if="!role" class="flex h-full w-full items-center justify-center">
      <ElEmpty :description="$t('role.permissions.selectRoleFirst')" />
    </div>

    <!-- 角色信息 -->
    <template v-if="role" #header>
      <div class="flex w-full items-center justify-between">
        <div class="flex items-center gap-4">
          <span class="text-base font-medium">{{
            $t('role.permissions.title')
          }}</span>
          <span class="text-sm text-gray-500">
            {{ role.name }} ({{ role.code }})
          </span>
        </div>
        <!-- 右上角按钮 -->
        <div class="flex gap-2">
          <ElButton
            :loading="saving"
            type="primary"
            size="small"
            @click="saveSelection"
          >
            {{ $t('role.permissions.save') }}
          </ElButton>
        </div>
      </div>
    </template>

    <!-- 主要内容 -->
    <div v-if="role" class="flex flex-1 flex-col overflow-hidden">
      <!-- 加载状态 - 骨架屏 -->
      <div v-if="loading" class="flex-1 p-3">
        <div class="flex h-full gap-3">
          <!-- 左侧：菜单列表骨架 -->
          <div class="w-64 pr-3">
            <ElCard
              class="h-full border border-[var(--el-border-color)]"
              shadow="never"
            >
              <template #header>
                <div class="flex items-center justify-between">
                  <ElSkeleton :loading="true" animated :throttle="0">
                    <template #template>
                      <div class="flex items-center gap-2">
                        <ElSkeletonItem
                          variant="text"
                          style="width: 40px; height: 16px"
                        />
                        <ElSkeletonItem
                          variant="text"
                          style="width: 40px; height: 16px"
                        />
                      </div>
                    </template>
                  </ElSkeleton>
                  <ElSkeleton :loading="true" animated :throttle="0">
                    <template #template>
                      <div class="flex gap-1">
                        <ElSkeletonItem
                          variant="text"
                          style="width: 40px; height: 16px"
                        />
                        <ElSkeletonItem
                          variant="text"
                          style="width: 40px; height: 16px"
                        />
                      </div>
                    </template>
                  </ElSkeleton>
                </div>
              </template>
              <ElSkeleton :loading="true" animated :rows="10" :throttle="0">
                <template #template>
                  <div class="space-y-2">
                    <div
                      v-for="i in 10"
                      :key="i"
                      class="flex h-[42px] items-center gap-2"
                    >
                      <ElSkeletonItem
                        variant="text"
                        style="width: 16px; height: 16px; border-radius: 4px"
                      />
                      <ElSkeletonItem
                        variant="text"
                        style="width: 16px; height: 16px; border-radius: 4px"
                      />
                      <ElSkeletonItem
                        variant="text"
                        :style="{
                          width: `${50 + Math.random() * 40}%`,
                          height: '16px',
                        }"
                      />
                      <div class="flex-1"></div>
                      <!-- <ElSkeletonItem variant="text" style="width: 50px; height: 14px" /> -->
                    </div>
                  </div>
                </template>
              </ElSkeleton>
            </ElCard>
          </div>

          <!-- 右侧：4个权限卡片骨架 -->
          <div class="grid flex-1 grid-cols-4 gap-4">
            <ElCard
              v-for="i in 4"
              :key="i"
              class="border border-[var(--el-border-color)]"
              shadow="never"
            >
              <template #header>
                <ElSkeleton :loading="true" animated :throttle="0">
                  <template #template>
                    <div class="flex items-center justify-between">
                      <div class="flex items-center gap-2">
                        <ElSkeletonItem
                          variant="text"
                          style="width: 35px; height: 16px"
                        />
                        <ElSkeletonItem
                          variant="text"
                          style="width: 35px; height: 16px"
                        />
                      </div>
                      <div class="flex gap-1">
                        <ElSkeletonItem
                          variant="text"
                          style="width: 35px; height: 16px"
                        />
                        <ElSkeletonItem
                          variant="text"
                          style="width: 35px; height: 16px"
                        />
                      </div>
                    </div>
                  </template>
                </ElSkeleton>
              </template>
              <ElSkeleton :loading="true" animated :rows="8" :throttle="0">
                <template #template>
                  <div class="space-y-2">
                    <div
                      v-for="j in 8"
                      :key="j"
                      class="flex h-[36px] items-center gap-2"
                    >
                      <ElSkeletonItem
                        variant="text"
                        style="width: 14px; height: 14px; border-radius: 3px"
                      />
                      <ElSkeletonItem
                        variant="text"
                        :style="{
                          width: `${45 + Math.random() * 40}%`,
                          height: '14px',
                        }"
                      />
                    </div>
                  </div>
                </template>
              </ElSkeleton>
            </ElCard>
          </div>
        </div>
      </div>

      <div
        v-else-if="treeData.length === 0"
        class="flex flex-1 items-center justify-center"
      >
        <ElEmpty :description="$t('role.permissions.noPermissionData')" />
      </div>

      <!-- 左右分栏布局 -->
      <div v-else ref="layoutContainerRef" class="flex min-h-0 flex-1 p-3">
        <!-- 左侧：菜单树 -->
        <div class="min-h-0 w-80 pr-3">
          <ElCard
            class="flex flex-col border border-[var(--el-border-color)]"
            shadow="never"
            :style="{ height: `${scrollAreaHeight}px` }"
            :body-style="{
              padding: '0',
              display: 'flex',
              flexDirection: 'column',
              flex: 1,
            }"
          >
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium text-gray-600">{{
                    $t('role.permissions.menuList')
                  }}</span>
                  <span class="text-xs text-gray-400">
                    ({{ selectedMenuIds.size }}/{{ totalMenuCount }})
                  </span>
                </div>
                <div class="flex gap-1">
                  <ElButton
                    link
                    type="primary"
                    size="small"
                    @click="selectAllMenus"
                  >
                    {{ $t('role.permissions.selectAll') }}
                  </ElButton>
                  <ElButton
                    link
                    type="primary"
                    size="small"
                    @click="unselectAllMenus"
                  >
                    {{ $t('role.permissions.unselectAll') }}
                  </ElButton>
                </div>
              </div>
            </template>
            <ElScrollbar :height="menuScrollHeight">
              <div class="space-y-0.5 pr-2">
                <!-- 递归菜单树渲染 -->
                <template v-for="menu in treeData" :key="menu.id">
                  <RenderMenuTree :menu="menu" :level="0" />
                </template>
              </div>
            </ElScrollbar>
          </ElCard>
        </div>

        <!-- 右侧：权限列表 -->
        <div class="flex min-h-0 flex-1 flex-col">
          <div
            v-if="!selectedMenuId"
            class="flex items-center justify-center"
            :style="{ minHeight: `${menuScrollHeight}px` }"
          >
            <ElEmpty :description="$t('role.permissions.selectMenuPrompt')" />
          </div>
          <div v-else class="grid grid-cols-2 gap-2 pb-2 pr-2">
            <!-- 4列显示：按钮权限、API权限、数据权限、其他权限 -->
            <template v-for="type in [0, 1]" :key="type">
              <ElCard
                class="flex flex-col border border-[var(--el-border-color)]"
                shadow="never"
                :style="{ height: `${scrollAreaHeight}px` }"
                :body-style="{
                  padding: '0',
                  display: 'flex',
                  flexDirection: 'column',
                  height: '100%',
                }"
              >
                <!-- 权限类型标题 -->
                <template #header>
                  <div class="flex items-center justify-between gap-2">
                    <div class="flex items-center gap-2">
                      <span class="text-xs font-medium text-gray-700">
                        {{ getPermissionTypeName(type) }}
                      </span>
                      <span class="text-xs text-gray-400">
                        ({{
                          currentMenuPermissions[type]?.filter(
                            (p: PermissionNode) =>
                              selectedPermissions.has(p.id),
                          ).length || 0
                        }}/{{ currentMenuPermissions[type]?.length || 0 }})
                      </span>
                    </div>
                    <div class="flex flex-shrink-0">
                      <ElButton
                        link
                        type="primary"
                        size="small"
                        @click="selectPermissionsByType(type)"
                      >
                        {{ $t('role.permissions.selectAll') }}
                      </ElButton>
                      <ElButton
                        link
                        type="primary"
                        size="small"
                        @click="unselectPermissionsByType(type)"
                      >
                        {{ $t('role.permissions.unselectAll') }}
                      </ElButton>
                    </div>
                  </div>
                </template>

                <!-- 权限列表 -->
                <div class="min-h-0 flex-1">
                  <ElScrollbar style="height: 100%">
                    <div
                      v-if="loadingPermissions"
                      class="flex h-20 items-center justify-center"
                    >
                      <span class="text-xs text-gray-400">{{
                        $t('role.permissions.loading')
                      }}</span>
                    </div>
                    <div
                      v-else-if="
                        !currentMenuPermissions[type] ||
                        currentMenuPermissions[type].length === 0
                      "
                      class="flex h-20 items-center justify-center"
                    >
                      <span class="text-xs text-gray-400">{{
                        $t('role.permissions.noPermissions')
                      }}</span>
                    </div>
                    <div v-else class="space-y-1 p-2">
                      <div
                        v-for="permission in currentMenuPermissions[type]"
                        :key="permission.id"
                        class="flex h-[36px] cursor-pointer items-center rounded-[6px] px-2 transition-colors hover:bg-[var(--el-fill-color-light)]"
                        @click="togglePermission(permission.id)"
                      >
                        <input
                          type="checkbox"
                          :checked="selectedPermissions.has(permission.id)"
                          class="mr-2 size-3.5 flex-shrink-0 cursor-pointer rounded border-gray-300 transition-colors"
                          @change="togglePermission(permission.id)"
                          @click.stop
                        />
                        <span
                          class="flex-1 truncate text-xs"
                          :title="permission.label || permission.name"
                        >
                          {{ permission.label || permission.name }}
                        </span>
                      </div>
                    </div>
                  </ElScrollbar>
                </div>
              </ElCard>
            </template>
          </div>
        </div>
      </div>
    </div>
  </ElCard>
</template>

<style scoped>
.empty-state-card :deep(.el-card__body) {
  height: 100%;
  padding: 0;
}
</style>
