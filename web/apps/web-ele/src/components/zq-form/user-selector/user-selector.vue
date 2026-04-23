<script lang="ts" setup>
import type { UserSelectorEmits, UserSelectorProps } from './types';

import {
  computed,
  defineAsyncComponent,
  onMounted,
  ref,
  useAttrs,
  watch,
} from 'vue';

import { IconifyIcon, Loader, Search } from '@vben/icons';
import { $t } from '@vben/locales';

import {
  ElButton,
  ElEmpty,
  ElInput,
  ElOption,
  ElScrollbar,
  ElSelect,
  ElSkeleton,
  ElSkeletonItem,
} from 'element-plus';

import { getDeptByParentApi, searchDeptApi } from '#/api/core/dept';
import { getUserDetailApi } from '#/api/core/user';

import { ZqDialog } from '#/components/zq-dialog';

defineOptions({
  name: 'UserSelector',
  inheritAttrs: false, // 禁用自动继承，手动控制
});

const props = withDefaults(defineProps<Props>(), {
  multiple: false,
  placeholder: () => $t('ui.placeholder.select') || 'Please select',
  disabled: false,
  clearable: true,
  filterable: true,
  displayMode: 'select', // 默认使用 select 模式
});

const emit = defineEmits<UserSelectorEmits>();

// @ts-ignore
const UserListPanel = defineAsyncComponent(
  () => import('../../user-list-panel/index.vue'),
);

interface Props extends UserSelectorProps {}

const attrs = useAttrs();

const modalVisible = ref(false);
const departments = ref<any[]>([]);
const selectedDeptId = ref<string>('');
const selectedUsers = ref<Set<string>>(
  new Set(
    Array.isArray(props.modelValue)
      ? props.modelValue
      : props.modelValue
        ? [props.modelValue]
        : [],
  ),
);
// 临时选择（用于 modal 中的选择，未确认前）
const tempSelectedUsers = ref<Set<string>>(new Set());
// 用户信息映射表（userId -> user info）
const userInfoMap = ref<
  Map<string, { id: string; name?: string; username: string }>
>(new Map());
// 分离的加载状态
const deptLoading = ref(false); // 部门加载状态
const confirmLoading = ref(false); // 确认按钮加载状态
const deptSearchText = ref('');
const expandedDeptIds = ref<Set<string>>(new Set());
const loadingDeptIds = ref<Set<string>>(new Set());
// 部门搜索相关
const deptSearchResults = ref<any[]>([]);
const isDeptSearching = ref(false);

// 计算显示值（只显示已确认的值）
const displayValue = computed({
  get() {
    if (selectedUsers.value.size === 0) return undefined;
    if (props.multiple) {
      return [...selectedUsers.value];
    }
    return [...selectedUsers.value][0];
  },
  set(_value) {
    // ElSelect 会改变这个值，但我们不需要处理
    // 因为实际的值通过 handleConfirm 来更新
  },
});

// 获取已选用户的完整信息（显示在 Select 或 Button 中）
const selectedUsersList = computed(() => {
  const result = [];
  const seenIds = new Set<string>(); // 用于去重

  // 使用已确认的 selectedUsers，而不是临时选择
  for (const userId of selectedUsers.value) {
    // 避免重复添加
    if (seenIds.has(userId)) continue;

    seenIds.add(userId);
    const userInfo = userInfoMap.value.get(userId);
    result.push({
      id: userId,
      display: userInfo ? `${userInfo.name || userInfo.username}` : userId,
    });
  }
  return result;
});

// 按钮显示文本（button 模式下不显示选中数量，只显示 placeholder）
const buttonDisplayText = computed(() => {
  return props.placeholder;
});

// 加载顶级部门数据
const loadDepartments = async () => {
  try {
    deptLoading.value = true;
    const result = await getDeptByParentApi(undefined);

    // 添加"全部部门"根节点
    const rootNode = {
      id: '0',
      name: $t('system.user.allDept') || 'All Departments',
      children: Array.isArray(result) ? result : [],
      parent_id: null,
      status: 1,
    };

    departments.value = [rootNode];
    expandedDeptIds.value.add('0');

    if (!selectedDeptId.value) {
      selectedDeptId.value = '0';
    }

    deptLoading.value = false;
  } catch (error) {
    console.error('Failed to load departments:', error);
    deptLoading.value = false;
  }
};

// 加载子部门（懒加载）
const loadChildrenDepts = async (parentId: string) => {
  try {
    loadingDeptIds.value.add(parentId);
    const data = await getDeptByParentApi(parentId);

    // 更新树数据中的子部门
    function updateNodeChildren(nodes: any[], targetId: string): boolean {
      for (const node of nodes) {
        if (node.id === targetId) {
          node.children = Array.isArray(data) ? data : [];
          return true;
        }
        if (
          node.children &&
          node.children.length > 0 &&
          updateNodeChildren(node.children, targetId)
        ) {
          return true;
        }
      }
      return false;
    }

    updateNodeChildren(departments.value, parentId);
  } catch (error) {
    console.error('Failed to load child departments:', error);
  } finally {
    loadingDeptIds.value.delete(parentId);
  }
};

// 检查部门是否有子部门
const hasChildren = (dept: any): boolean => {
  if (dept.child_count && typeof dept.child_count === 'number') {
    return dept.child_count > 0;
  }
  if (!dept.children) return false;
  return dept.children.length > 0;
};

// 切换部门展开/折叠
const toggleDeptExpanded = async (dept: any) => {
  if (expandedDeptIds.value.has(dept.id)) {
    expandedDeptIds.value.delete(dept.id);
  } else {
    expandedDeptIds.value.add(dept.id);
    // 如果子部门未加载，则加载
    if (!dept.children || dept.children.length === 0) {
      await loadChildrenDepts(dept.id);
    }
  }
};

/**
 * 自动展开部门搜索结果中的所有节点
 */
function autoExpandDeptSearchResults(nodes: any[]) {
  nodes.forEach((node) => {
    expandedDeptIds.value.add(node.id);
    if (node.children && node.children.length > 0) {
      autoExpandDeptSearchResults(node.children);
    }
  });
}

/**
 * 部门搜索防抖定时器
 */
let deptSearchTimer: null | ReturnType<typeof setTimeout> = null;

/**
 * 监听部门搜索文本变化，执行后端搜索
 */
watch(deptSearchText, (newVal) => {
  // 清除之前的定时器
  if (deptSearchTimer) {
    clearTimeout(deptSearchTimer);
  }

  if (!newVal.trim()) {
    deptSearchResults.value = [];
    isDeptSearching.value = false;
    return;
  }

  // 设置新的防抖定时器
  deptSearchTimer = setTimeout(async () => {
    isDeptSearching.value = true;
    try {
      const results = await searchDeptApi(newVal);
      deptSearchResults.value = results || [];
      // 自动展开搜索结果中的所有节点，显示完整路径
      if (results && results.length > 0) {
        autoExpandDeptSearchResults(results);
      }
    } catch (error) {
      console.error('搜索部门失败:', error);
      deptSearchResults.value = [];
    } finally {
      isDeptSearching.value = false;
    }
  }, 300);
});

// 部门树过滤：如果有搜索结果则使用搜索结果，否则使用完整树
const filteredDepts = computed(() => {
  if (deptSearchText.value.trim() && deptSearchResults.value.length > 0) {
    return deptSearchResults.value;
  }
  return departments.value;
});

// 渲染树列表（扁平化）
const renderTreeList = (nodes: any[], level: number = 0): any[] => {
  return nodes.flatMap((node) => [
    { node, level, isNode: true },
    ...(expandedDeptIds.value.has(node.id) &&
    node.children &&
    node.children.length > 0
      ? renderTreeList(node.children, level + 1)
      : []),
  ]);
};

const flattenedTree = computed(() => renderTreeList(filteredDepts.value));

// 处理部门选择
const handleDeptSelect = (deptId: string) => {
  selectedDeptId.value = deptId;
};

// 处理用户选择
const handleUserSelect = (userId: string, user: any) => {
  // 保存用户信息到映射表
  if (user) {
    userInfoMap.value.set(userId, {
      id: user.id,
      name: user.name,
      username: user.username,
    });
  }

  if (props.multiple) {
    if (tempSelectedUsers.value.has(userId)) {
      tempSelectedUsers.value.delete(userId);
    } else {
      tempSelectedUsers.value.add(userId);
    }
  } else {
    // 单选模式
    tempSelectedUsers.value.clear();
    tempSelectedUsers.value.add(userId);
  }
};

// 打开modal
const openModal = async () => {
  if (props.disabled) return;
  // 打开 modal 时，将已确认的值复制到临时选择
  tempSelectedUsers.value = new Set(selectedUsers.value);
  modalVisible.value = true;
};

// 打开modal后加载数据
const handleModalOpened = async () => {
  updateInternalValue();

  if (departments.value.length === 0) {
    // 先设置默认部门为"全部部门"
    if (!selectedDeptId.value) {
      selectedDeptId.value = '0';
    }

    // 加载部门列表
    await loadDepartments();
  }
};

// 确认选择
const handleConfirm = async () => {
  // 将临时选择的值保存到 selectedUsers（已确认）
  selectedUsers.value = new Set(tempSelectedUsers.value);

  const value = props.multiple
    ? [...selectedUsers.value]
    : selectedUsers.value.size > 0
      ? [...selectedUsers.value][0]
      : '';

  // 如果是 button 模式且有 onConfirm 回调，调用回调而不是 emit change
  if (props.displayMode === 'button' && props.onConfirm) {
    try {
      // 确保 value 不是 undefined
      if (value !== undefined && value !== '') {
        confirmLoading.value = true;
        await props.onConfirm(value);
        // 调用成功后清空选择
        tempSelectedUsers.value.clear();
        selectedUsers.value.clear();
        modalVisible.value = false;
      }
    } catch (error) {
      console.error('onConfirm callback failed:', error);
      // 失败时不清空，让用户可以重试
    } finally {
      confirmLoading.value = false;
    }
  } else {
    // select 模式或没有 onConfirm，使用原来的逻辑
    emit('update:modelValue', value);
    emit('change', value);
    modalVisible.value = false;
  }
};

// 清除选择
const handleClear = (e?: MouseEvent) => {
  if (e) {
    e.stopPropagation();
  }
  tempSelectedUsers.value.clear();
  selectedUsers.value.clear();
  const emptyValue = props.multiple ? [] : '';
  emit('update:modelValue', emptyValue);
  emit('change', emptyValue);
};

// 删除单个选中项（多选模式下点击标签删除按钮）
const handleRemoveTag = (userId: string) => {
  selectedUsers.value.delete(userId);
  const value = props.multiple ? [...selectedUsers.value] : '';
  emit('update:modelValue', value);
  emit('change', value);
};

/**
 * 加载初始用户信息
 */
const loadInitialUserInfo = async (userIds: string[]) => {
  // 过滤掉已经在 userInfoMap 中的用户
  const idsToLoad = userIds.filter((id) => !userInfoMap.value.has(id));

  if (idsToLoad.length === 0) {
    return;
  }

  // 并行加载所有用户信息
  const promises = idsToLoad.map(async (userId) => {
    try {
      const user = await getUserDetailApi(userId);
      if (user) {
        userInfoMap.value.set(userId, {
          id: user.id,
          name: user.name,
          username: user.username,
        });
      }
    } catch (error) {
      console.error(`Failed to load user ${userId}:`, error);
      // 加载失败时，至少保存ID
      userInfoMap.value.set(userId, {
        id: userId,
        name: undefined,
        username: userId,
      });
    }
  });

  await Promise.all(promises);
};

// 监听外部 modelValue 变化
const updateInternalValue = async () => {
  selectedUsers.value.clear();
  tempSelectedUsers.value.clear();

  const userIds: string[] = [];

  if (Array.isArray(props.modelValue)) {
    props.modelValue.forEach((v) => {
      selectedUsers.value.add(v);
      userIds.push(v);
    });
  } else if (props.modelValue) {
    selectedUsers.value.add(props.modelValue);
    userIds.push(props.modelValue);
  }

  // 加载用户信息
  if (userIds.length > 0) {
    await loadInitialUserInfo(userIds);
  }

  // 打开 modal 时初始化临时选择
  if (modalVisible.value) {
    tempSelectedUsers.value = new Set(selectedUsers.value);
  }
};

// 监听 modelValue 变化
watch(
  () => props.modelValue,
  () => {
    updateInternalValue();
  },
  { immediate: true },
);

// 组件挂载时
onMounted(() => {
  // 不需要初始加载，UserListPanel 会自己处理
});

defineExpose({
  openModal,
});
</script>

<template>
  <div class="w-full">
    <!-- Select 模式 -->
    <div
      v-if="displayMode === 'select'"
      class="cursor-pointer"
      :class="{ 'cursor-not-allowed opacity-60': disabled }"
    >
      <ElSelect
        v-bind="attrs"
        v-model="displayValue"
        :placeholder="placeholder"
        :disabled="disabled"
        :clearable="clearable && selectedUsers.size > 0"
        :multiple="multiple"
        readonly
        @click="openModal"
        @clear="() => handleClear()"
        @remove-tag="handleRemoveTag"
      >
        <ElOption
          v-for="item in selectedUsersList"
          :key="item.id"
          :label="item.display"
          :value="item.id"
        />
      </ElSelect>
    </div>

    <!-- Button 模式 -->
    <div
      v-else
      class="flex items-center gap-2"
      :class="{ 'pointer-events-none opacity-60': disabled }"
    >
      <ElButton :disabled="disabled" type="primary" @click="openModal">
        {{ buttonDisplayText }}
      </ElButton>
      <ElButton
        v-if="clearable && selectedUsers.size > 0 && !props.onConfirm"
        :disabled="disabled"
        text
        type="danger"
        size="small"
        @click="handleClear"
      >
        {{ $t('common.clear') }}
      </ElButton>
    </div>

    <!-- Modal -->
    <ZqDialog
      v-model="modalVisible"
      :title="$t('user.selectUser')"
      width="55%"
      :show-fullscreen-button="false"
      @opened="handleModalOpened"
    >
      <div class="grid h-[600px] grid-cols-1 gap-4 p-0 md:grid-cols-[1fr_3fr]">
        <!-- 左侧：部门树 -->
        <div
          class="relative flex flex-col overflow-hidden rounded-[var(--radius)] border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2.5 shadow-sm transition-shadow duration-300 hover:shadow-md"
        >
          <!-- 部门搜索 -->
          <div class="py-3">
            <ElInput
              v-model="deptSearchText"
              :placeholder="$t('common.search') || 'Search'"
              clearable
              :prefix-icon="Search"
            />
          </div>

          <!-- 部门树列表 -->
          <ElScrollbar class="flex-1" :distance="40">
            <ElSkeleton
              :loading="deptLoading || isDeptSearching"
              animated
              :count="8"
            >
              <template #template>
                <div class="flex w-full flex-1 flex-col py-2">
                  <div v-for="i in 8" :key="i">
                    <ElSkeletonItem
                      variant="text"
                      style="width: 100%; height: 40px; margin: 8px 0"
                    />
                  </div>
                </div>
              </template>
              <template #default>
                <div class="flex w-full flex-1 flex-col py-2">
                  <ElEmpty
                    v-if="flattenedTree.length === 0"
                    :description="$t('common.noData')"
                  />
                  <div v-else class="flex flex-col">
                    <div
                      v-for="(item, index) in flattenedTree"
                      :key="`${item.node.id}-${index}`"
                      class="my-0.5 flex h-[42px] cursor-pointer items-center rounded-lg px-3 transition-all duration-200"
                      :class="[
                        selectedDeptId === item.node.id
                          ? 'bg-primary/15 dark:bg-accent text-primary'
                          : 'hover:bg-[var(--el-fill-color-light)]',
                      ]"
                      :style="{
                        paddingLeft: `calc(12px + ${item.level * 20}px)`,
                      }"
                      @click="handleDeptSelect(item.node.id)"
                    >
                      <!-- 展开/折叠按钮 -->
                      <div
                        v-if="hasChildren(item.node)"
                        class="mr-1 flex h-5 w-5 flex-shrink-0 items-center justify-center"
                      >
                        <div
                          class="flex cursor-pointer items-center justify-center transition-transform duration-200 hover:text-[var(--primary)]"
                          @click.stop="toggleDeptExpanded(item.node)"
                        >
                          <IconifyIcon
                            v-if="!loadingDeptIds.has(item.node.id)"
                            icon="ep:caret-right"
                            class="size-4 transform transition-transform"
                            :class="
                              expandedDeptIds.has(item.node.id)
                                ? 'rotate-90'
                                : ''
                            "
                          />
                          <Loader v-else class="size-4 animate-spin" />
                        </div>
                      </div>
                      <div v-else class="mr-1 w-5 flex-shrink-0"></div>

                      <!-- 部门名称 -->
                      <div
                        class="flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-sm transition-colors duration-200"
                      >
                        {{ item.node.name }}
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </ElSkeleton>
          </ElScrollbar>
        </div>

        <!-- 右侧：用户列表 -->
        <UserListPanel
          :data-source="selectedDeptId === '0' ? 'all' : 'dept'"
          :source-id="selectedDeptId === '0' ? undefined : selectedDeptId"
          :temp-selected-users="tempSelectedUsers"
          :filterable="filterable"
          :multiple="multiple"
          :selectable="true"
          :show-border="true"
          @user-select="handleUserSelect"
          @remove-user="(userId) => tempSelectedUsers.delete(userId)"
        />
      </div>

      <template #footer>
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="selected-count">
              {{ tempSelectedUsers.size }} {{ $t('common.selected') }}
            </span>
            <ElButton
              v-if="tempSelectedUsers.size > 0"
              link
              type="danger"
              size="small"
              @click="() => tempSelectedUsers.clear()"
            >
              {{ $t('common.clear') }}
            </ElButton>
          </div>
          <div class="ml-auto flex items-center gap-2">
            <ElButton :disabled="confirmLoading" @click="modalVisible = false">
              {{ $t('common.cancel') }}
            </ElButton>
            <ElButton
              type="primary"
              :loading="confirmLoading"
              :disabled="confirmLoading"
              @click="handleConfirm"
            >
              {{ $t('common.confirm') }}
            </ElButton>
          </div>
        </div>
      </template>
    </ZqDialog>
  </div>
</template>

<style scoped>
/* 保留必要的 deep 样式用于修改 Element Plus 组件 */
:deep(.el-input.is-disabled) {
  background-color: var(--background-deep, #f5f7fa);
}

:deep(.el-dialog__header) {
  border-bottom: 1px solid var(--border, #ebeef5);
}

:deep(.el-dialog__body) {
  padding: 20px;
}
</style>
