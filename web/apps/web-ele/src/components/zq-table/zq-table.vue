<script lang="ts" setup>
import type { ExtendedZqTableApi, ZqTableProps } from './types';

import {
  computed,
  defineComponent,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  toRaw,
  useSlots,
  watch,
} from 'vue';

import { VbenHelpTooltip } from '@vben/common-ui';
import { usePriorityValues } from '@vben/hooks';
import {
  EmptyIcon,
  GripVertical as LucideGripVertical,
  Pin as LucidePin,
  RotateCcw as LucideRotateCcw,
} from '@vben/icons';
import { $t } from '@vben/locales';
import { usePreferences } from '@vben/preferences';
import { cn, isBoolean, isEqual, mergeWithArrayOverride } from '@vben/utils';

import { FullScreen, Refresh, Search, Setting } from '@element-plus/icons-vue';
import { useResizeObserver } from '@vueuse/core';
import {
  ElButton,
  ElCheckbox,
  ElDivider,
  ElIcon,
  ElPagination,
  ElPopover,
  ElScrollbar,
  ElTable,
  ElTableColumn,
  ElTooltip,
} from 'element-plus';
import draggable from 'vuedraggable';

import { useTableForm } from './init';

import './style.css';

interface Props extends ZqTableProps {
  api: ExtendedZqTableApi;
}

interface ColumnState {
  key: string;
  title: string;
  visible: boolean;
  fixed: 'left' | 'right' | false;
  originalIndex: number;
}

const props = withDefaults(defineProps<Props>(), {});
const emit = defineEmits([
  'selection-change',
  'sort-change',
  'row-click',
  'row-dblclick',
]);
const rootRef = ref<HTMLElement>();
const tableContainerRef = ref<HTMLElement>();
const tableWidth = ref(0);
const tableHeight = ref(0);
const tableRef = ref();

const isFullscreen = ref(false);
function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value;
}

useResizeObserver(tableContainerRef, (entries) => {
  const entry = entries[0];
  if (!entry) return;
  const { width, height } = entry.contentRect;
  tableWidth.value = width;
  tableHeight.value = height;
});

const FORM_SLOT_PREFIX = 'form-';
const CELL_SLOT_PREFIX = 'cell-';
const TOOLBAR_ACTIONS = 'toolbar-actions';
const TOOLBAR_TOOLS = 'toolbar-tools';
const TABLE_TITLE = 'table-title';

const state = props.api?.useStore?.();

const {
  gridOptions,
  class: className,
  gridClass,
  formOptions,
  tableTitle,
  tableTitleHelp,
  showSearchForm,
  separator,
} = usePriorityValues(props, state);

const { isMobile } = usePreferences();
const isSeparator = computed(() => {
  if (
    !formOptions.value ||
    showSearchForm.value === false ||
    separator.value === false
  ) {
    return false;
  }
  if (separator.value === true || separator.value === undefined) {
    return true;
  }
  return separator.value.show !== false;
});

const separatorBg = computed(() => {
  return !separator.value ||
    isBoolean(separator.value) ||
    !separator.value.backgroundColor
    ? undefined
    : separator.value.backgroundColor;
});

const slots = useSlots();
// 防抖定时器
let debounceTimer: null | ReturnType<typeof setTimeout> = null;

// 触发搜索
const triggerSearch = async () => {
  const formValues = await formApi.getValues();
  formApi.setLatestSubmissionValues(toRaw(formValues));
  props.api.reload(formValues);
};

// 防抖搜索
const debouncedSearch = () => {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
  }
  debounceTimer = setTimeout(() => {
    triggerSearch();
  }, 300);
};

// Initialize Form
const [Form, formApi] = useTableForm({
  compact: true,
  handleSubmit: async () => {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    await triggerSearch();
  },
  handleReset: async () => {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    const prevValues = await formApi.getValues();
    await formApi.resetForm();
    const formValues = await formApi.getValues();
    formApi.setLatestSubmissionValues(formValues);
    if (isEqual(prevValues, formValues) || !formOptions.value?.submitOnChange) {
      props.api.reload(formValues);
    }
  },
  commonConfig: {
    componentProps: {
      class: 'w-full',
      clearable: true,
    },
  },
  showCollapseButton: true,
  collapseTriggerResize: true, // 自动检测是否需要折叠
  submitOnChange: true, // 默认启用输入时触发搜索
  submitButtonOptions: {
    content: computed(() => $t('common.search')),
  },
  resetButtonOptions: {
    content: computed(() => $t('common.reset')),
    type: undefined, // 覆盖默认的 type="button"，避免 ElButton 警告
  },
  wrapperClass: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
});

// 监听表单值变化，自动触发搜索
watch(
  () => formApi.form?.values,
  (newValues, oldValues) => {
    // 只有当 submitOnChange 为 true 时才自动搜索
    if (
      formOptions.value?.submitOnChange &&
      newValues &&
      oldValues && // 检查值是否真的变化了
      !isEqual(newValues, oldValues)
    ) {
      debouncedSearch();
    }
  },
  { deep: true },
);

// Toolbar logic
const showTableTitle = computed(() => {
  return !!slots[TABLE_TITLE] || tableTitle.value;
});

const showToolbar = computed(() => {
  return (
    !!slots[TOOLBAR_ACTIONS] ||
    !!slots[TOOLBAR_TOOLS] ||
    showTableTitle.value ||
    !!gridOptions.value?.toolbarConfig
  );
});

const delegatedSlots = computed(() => {
  const resultSlots: string[] = [];
  for (const key of Object.keys(slots)) {
    if (
      !['empty', 'form', 'loading', TOOLBAR_ACTIONS, TOOLBAR_TOOLS].includes(
        key,
      )
    ) {
      resultSlots.push(key);
    }
  }
  return resultSlots;
});

const delegatedFormSlots = computed(() => {
  const resultSlots: string[] = [];
  for (const key of Object.keys(slots)) {
    if (key.startsWith(FORM_SLOT_PREFIX)) {
      resultSlots.push(key);
    }
  }
  return resultSlots.map((key) => key.replace(FORM_SLOT_PREFIX, ''));
});

// Data & Pagination binding
const tableData = props.api.tableData;
const total = props.api.total;
const loading = props.api.loading;
const pagination = props.api.pagination;

function onPageChange(currentPage: number) {
  props.api.handlePageChange(currentPage, pagination.pageSize);
}

function onPageSizeChange(pageSize: number) {
  props.api.handlePageChange(pagination.currentPage, pageSize);
}

function onSearchBtnClick() {
  props.api.toggleSearchForm();
}

function onRefreshBtnClick() {
  props.api.reload();
}

// Init logic
async function init() {
  await nextTick();
  const defaultGridOptions = mergeWithArrayOverride(
    {},
    toRaw(gridOptions.value),
  );

  const autoLoad = defaultGridOptions.proxyConfig?.autoLoad;
  if (autoLoad) {
    props.api.reload();
  }
}

// Column Setting State
const columnState = ref<ColumnState[]>([]);

function initColumnState() {
  const cols = gridOptions.value?.columns;
  if (cols) {
    columnState.value = cols.map((col: any, index: number) => ({
      key: col.key || col.dataKey,
      title: col.title || col.label || col.key || col.dataKey, // el-table uses label
      visible: col.hidden !== true,
      fixed: col.fixed === true ? 'left' : col.fixed || false,
      originalIndex: index,
    }));
  }
}

watch(
  () => gridOptions.value?.columns,
  () => {
    initColumnState();
  },
  { immediate: true, deep: true },
);

function handleResetColumn() {
  initColumnState();
}

function handleToggleFixed(col: ColumnState, type: 'left' | 'right') {
  col.fixed = col.fixed === type ? false : type;
}

watch(
  formOptions,
  () => {
    formApi.setState((prev) => {
      const finalFormOptions = mergeWithArrayOverride(
        {},
        formOptions.value,
        prev,
      );
      return {
        ...finalFormOptions,
        // 自动检测是否需要折叠按钮
        collapseTriggerResize: finalFormOptions.showCollapseButton ?? true,
        // 默认启用输入时触发搜索，除非明确设置为 false
        submitOnChange: finalFormOptions.submitOnChange ?? true,
      };
    });
  },
  { immediate: true },
);

const isCompactForm = computed(() => {
  return formApi.getState()?.compact;
});

onMounted(() => {
  props.api.mount(formApi);
  init();
});

onUnmounted(() => {
  props.api.unmount();
});

// 获取单元格插槽名称列表
const cellSlotNames = computed(() => {
  const result: Record<string, string> = {};
  for (const key of Object.keys(slots)) {
    if (key.startsWith(CELL_SLOT_PREFIX)) {
      const colKey = key.replace(CELL_SLOT_PREFIX, '');
      result[colKey] = key;
    }
  }
  return result;
});

// Renderer Component for VNode
const VNodeRenderer = defineComponent({
  props: {
    renderer: { type: Function, required: true },
    params: { type: Object, required: true },
  },
  setup(props) {
    return () => props.renderer(props.params);
  },
});

// 设置弹性列（最后一个非固定、非特殊列），移除其 width 让它自动填充剩余空间
function setFlexColumn(cols: any[]) {
  // 从后往前找第一个非固定、非 type（selection/index）、非 actions 的列
  for (let i = cols.length - 1; i >= 0; i--) {
    const col = cols[i];
    if (
      !col.type &&
      !col.fixed &&
      col.prop !== 'actions' &&
      col.key !== 'actions'
    ) {
      // 移除 width，保留 minWidth
      delete col.width;
      if (!col.minWidth) {
        col.minWidth = 100;
      }
      break;
    }
  }
  return cols;
}

// Table Columns
const columns = computed(() => {
  const cols = (gridOptions.value?.columns || []) as any[];

  const processCol = (col: any) => {
    const colKey = col.key || col.dataKey || col.prop;
    const slotName = cellSlotNames.value[colKey];

    return {
      ...col,
      prop: colKey, // el-table uses prop
      label: col.title || col.label, // el-table uses label
      slotName: slotName && slots[slotName] ? slotName : undefined,
      resizable: col.resizable ?? true,
      showOverflowTooltip: col.showOverflowTooltip ?? true, // 默认开启溢出提示
    };
  };

  // 如果没有初始化 columnState，直接返回所有列
  if (columnState.value.length === 0) {
    const processedCols = cols.map(processCol);

    // 处理 Selection 和 Index
    const prefixCols: any[] = [];
    if (gridOptions.value?.showSelection) {
      prefixCols.push({
        type: 'selection',
        width: 50,
        fixed: 'left',
        align: 'center',
      });
    }
    if (gridOptions.value?.showIndex) {
      prefixCols.push({
        type: 'index',
        width: 60,
        label: '#',
        fixed: 'left',
        align: 'center',
      });
    }

    const final = [...prefixCols, ...processedCols];

    // 强制 actions 列在最后且固定右侧
    const actionIndex = final.findIndex(
      (c: any) => c.prop === 'actions' || c.key === 'actions',
    );
    if (actionIndex !== -1) {
      const actionCol = final.splice(actionIndex, 1)[0];
      actionCol.fixed = 'right';
      final.push(actionCol);
    }
    return setFlexColumn(final);
  }

  const finalCols: any[] = [];

  // 处理 Selection 和 Index
  if (gridOptions.value?.showSelection) {
    finalCols.push({
      type: 'selection',
      width: 50,
      fixed: 'left',
      align: 'center',
    });
  }
  if (gridOptions.value?.showIndex) {
    finalCols.push({
      type: 'index',
      width: 60,
      label: '#',
      fixed: 'left',
      align: 'center',
    });
  }

  columnState.value.forEach((state) => {
    if (!state.visible) return;

    const originalCol = cols.find(
      (c) => (c.key || c.dataKey || c.prop) === state.key,
    );
    if (originalCol) {
      const processedCol = processCol(originalCol);
      finalCols.push({
        ...processedCol,
        fixed: state.fixed,
      });
    }
  });

  // 强制 actions 列在最后且固定右侧
  const actionIndex = finalCols.findIndex(
    (c: any) => c.prop === 'actions' || c.key === 'actions',
  );
  if (actionIndex !== -1) {
    const actionCol = finalCols.splice(actionIndex, 1)[0];
    actionCol.fixed = 'right';
    finalCols.push(actionCol);
  }

  return setFlexColumn(finalCols);
});

function handleSelectionChange(val: any[]) {
  emit('selection-change', val);
}

function handleSortChange(data: any) {
  emit('sort-change', data);
}
</script>

<template>
  <div
    :class="
      cn('bg-card flex h-full flex-col rounded-md', className, {
        'zq-table-fullscreen': isFullscreen,
      })
    "
    ref="rootRef"
  >
    <!-- Form -->
    <div
      v-if="formOptions"
      v-show="showSearchForm !== false"
      :class="
        cn(
          'relative rounded p-4',
          isCompactForm
            ? isSeparator
              ? 'pb-8'
              : 'pb-4'
            : isSeparator
              ? 'pb-4'
              : 'pb-0',
        )
      "
    >
      <slot name="form">
        <Form>
          <template
            v-for="slotName in delegatedFormSlots"
            :key="slotName"
            #[slotName]="slotProps"
          >
            <slot
              :name="`${FORM_SLOT_PREFIX}${slotName}`"
              v-bind="slotProps"
            ></slot>
          </template>
        </Form>
      </slot>
      <div
        v-if="isSeparator"
        :style="{
          ...(separatorBg ? { backgroundColor: separatorBg } : undefined),
        }"
        class="bg-background-deep z-100 absolute -left-2 bottom-1 h-2 w-[calc(100%+8px)] overflow-hidden md:bottom-2 md:h-3"
      ></div>
    </div>

    <!-- Toolbar -->
    <div
      v-if="showToolbar"
      class="flex items-center justify-between px-4 pb-4 pt-2"
    >
      <!-- Left: Title / Actions -->
      <div class="flex items-center">
        <slot v-if="showTableTitle" name="table-title">
          <div class="mr-1 pl-1 text-[1rem] font-medium">
            {{ tableTitle }}
            <VbenHelpTooltip v-if="tableTitleHelp" trigger-class="pb-1">
              {{ tableTitleHelp }}
            </VbenHelpTooltip>
          </div>
        </slot>
        <slot name="toolbar-actions"></slot>
      </div>

      <!-- Right: Tools -->
      <div class="flex items-center">
        <slot name="toolbar-tools"></slot>

        <!-- Default Tools -->
        <ElButton
          v-if="gridOptions?.toolbarConfig?.search && !!formOptions"
          circle
          :type="showSearchForm ? 'primary' : ''"
          :icon="Search"
          @click="onSearchBtnClick"
          :title="$t('common.search')"
        />
        <ElButton
          v-if="gridOptions?.toolbarConfig?.refresh !== false"
          circle
          @click="onRefreshBtnClick"
          :title="$t('common.refresh')"
        >
          <ElIcon :class="{ 'zq-spin': loading }">
            <Refresh />
          </ElIcon>
        </ElButton>
        <ElButton
          v-if="gridOptions?.toolbarConfig?.zoom !== false"
          circle
          :type="isFullscreen ? 'primary' : ''"
          :icon="FullScreen"
          @click="toggleFullscreen"
          :title="
            isFullscreen ? $t('common.exitFullscreen') : $t('common.fullscreen')
          "
        />
        <ElPopover
          v-if="gridOptions?.toolbarConfig?.custom !== false"
          placement="bottom-end"
          :width="280"
          trigger="click"
        >
          <template #reference>
            <ElButton circle :icon="Setting" :title="$t('common.setting')" />
          </template>
          <div class="p-2">
            <div class="mb-2 flex items-center justify-between">
              <span class="font-bold">{{ $t('common.columnSetting') }}</span>
              <ElButton
                link
                size="small"
                @click="handleResetColumn"
                :icon="LucideRotateCcw"
              >
                {{ $t('common.reset') }}
              </ElButton>
            </div>
            <ElDivider class="!my-2" />
            <ElScrollbar max-height="300px">
              <draggable
                v-model="columnState"
                item-key="key"
                handle=".drag-handle"
                :animation="200"
              >
                <template #item="{ element }">
                  <div
                    class="hover:bg-accent/50 group mb-1 flex items-center rounded p-1"
                  >
                    <LucideGripVertical
                      class="text-muted-foreground drag-handle mr-2 h-4 w-4 cursor-move opacity-0 group-hover:opacity-100"
                    />
                    <ElCheckbox
                      v-model="element.visible"
                      class="mr-2 !h-6 flex-1 truncate"
                      :label="element.title"
                      :title="element.title"
                    />

                    <div
                      class="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100"
                    >
                      <ElTooltip content="固定到左侧" placement="top">
                        <LucidePin
                          class="h-3.5 w-3.5 cursor-pointer"
                          :class="
                            element.fixed === 'left'
                              ? 'text-primary rotate-[-45deg]'
                              : 'text-muted-foreground hover:text-foreground'
                          "
                          @click="handleToggleFixed(element, 'left')"
                        />
                      </ElTooltip>
                      <ElTooltip content="固定到右侧" placement="top">
                        <LucidePin
                          class="h-3.5 w-3.5 scale-x-[-1] cursor-pointer"
                          :class="
                            element.fixed === 'right'
                              ? 'text-primary rotate-[-45deg]'
                              : 'text-muted-foreground hover:text-foreground'
                          "
                          @click="handleToggleFixed(element, 'right')"
                        />
                      </ElTooltip>
                    </div>
                  </div>
                </template>
              </draggable>
            </ElScrollbar>
          </div>
        </ElPopover>
      </div>
    </div>

    <!-- Table Body -->
    <div
      class="relative flex-1 overflow-hidden px-3"
      :class="gridClass"
      v-loading="loading"
    >
      <div class="h-full w-full" ref="tableContainerRef">
        <ElTable
          ref="tableRef"
          v-bind="gridOptions"
          :data="tableData"
          :height="tableHeight"
          :style="{ width: '100%' }"
          header-row-class-name="zq-table-header"
          @selection-change="handleSelectionChange"
          @sort-change="handleSortChange"
        >
          <ElTableColumn
            v-for="col in columns"
            :key="col.prop || col.key || col.type"
            v-bind="col"
          >
            <!-- 对于 selection 和 index 类型的列，不使用自定义插槽 -->
            <template v-if="!col.type" #default="scope">
              <!-- 优先使用插槽 -->
              <slot
                v-if="col.slotName"
                :name="col.slotName"
                v-bind="{ row: scope.row, $index: scope.$index }"
              ></slot>
              <!-- 其次使用 cellRenderer (函数渲染 VNode) -->
              <VNodeRenderer
                v-else-if="col.cellRenderer"
                :renderer="col.cellRenderer"
                :params="{
                  cellData: scope.row[col.prop],
                  rowData: scope.row,
                  rowIndex: scope.$index,
                  column: col,
                }"
              />
              <!-- 默认显示 -->
              <span v-else>{{ scope.row[col.prop] }}</span>
            </template>
          </ElTableColumn>

          <template #empty>
            <slot name="empty">
              <div
                class="text-muted-foreground flex h-full flex-col items-center justify-center"
              >
                <EmptyIcon class="mx-auto" />
                <div class="mt-2">{{ $t('common.noData') }}</div>
              </div>
            </slot>
          </template>
        </ElTable>
      </div>
    </div>

    <!-- Pagination -->
    <div
      v-if="gridOptions?.pagerConfig?.enabled !== false"
      class="flex justify-end p-4"
    >
      <ElPagination
        v-model:current-page="pagination.currentPage"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="gridOptions?.pagerConfig?.pageSizes || [10, 20, 50, 100]"
        :layout="
          gridOptions?.pagerConfig?.layout ||
          'total, sizes, prev, pager, next, jumper'
        "
        :background="gridOptions?.pagerConfig?.background !== false"
        size="small"
        @current-change="onPageChange"
        @size-change="onPageSizeChange"
      />
    </div>
  </div>
</template>

<style scoped></style>
