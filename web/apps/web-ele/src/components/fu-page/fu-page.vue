<script setup lang="ts">
import { computed } from 'vue';

import { Page } from '@vben/common-ui';

import { ElCard, ElScrollbar, ElSplitter, ElSplitterPanel } from 'element-plus';

interface Props {
  /**
   * 左侧面板宽度
   * @default '250px'
   */
  leftWidth?: number | string;

  /**
   * 左侧面板最小宽度
   * @default 200
   */
  leftMinWidth?: number;

  /**
   * 左侧面板最大宽度
   * @default 400
   */
  leftMaxWidth?: number;

  /**
   * 左侧面板是否可折叠
   * @default true
   */
  leftCollapsible?: boolean;

  /**
   * 左侧面板是否可调整大小
   * @default true
   */
  leftResizable?: boolean;

  /**
   * 左侧卡片标题
   */
  leftTitle?: string;

  /**
   * 右侧卡片标题
   */
  rightTitle?: string;

  /**
   * 左侧卡片是否显示阴影
   * @default 'never'
   */
  leftShadow?: 'always' | 'hover' | 'never';

  /**
   * 右侧卡片是否显示阴影
   * @default 'never'
   */
  rightShadow?: 'always' | 'hover' | 'never';

  /**
   * 左侧内容区域的高度（用于 ElScrollbar）
   * @default 'calc(100vh - 200px)'
   */
  leftHeight?: string;

  /**
   * 右侧内容区域的高度（用于 ElScrollbar）
   * @default 'calc(100vh - 200px)'
   */
  rightHeight?: string;

  /**
   * 是否自动计算内容高度
   * @default true
   */
  autoContentHeight?: boolean;

  /**
   * Page 组件的标题
   */
  pageTitle?: string;

  /**
   * Page 组件的描述
   */
  pageDescription?: string;

  /**
   * 左侧内容区域 padding
   * @default true - 使用默认 padding (20px)
   * false - 无 padding
   * string - 自定义 padding 类名
   */
  leftPadding?: boolean | string;

  /**
   * 右侧内容区域 padding
   * @default true - 使用默认 padding (20px)
   * false - 无 padding
   * string - 自定义 padding 类名
   */
  rightPadding?: boolean | string;

  /**
   * 是否使用 ElScrollbar（全局控制，优先级低于单独控制）
   * @default true
   */
  useScrollbar?: boolean;

  /**
   * 左侧是否使用 ElScrollbar
   * @default undefined - 使用 useScrollbar 的值
   */
  leftUseScrollbar?: boolean;

  /**
   * 右侧是否使用 ElScrollbar
   * @default undefined - 使用 useScrollbar 的值
   */
  rightUseScrollbar?: boolean;

  /**
   * 左侧内容区域自定义 class
   */
  leftContentClass?: string;

  /**
   * 右侧内容区域自定义 class
   */
  rightContentClass?: string;
}

const props = withDefaults(defineProps<Props>(), {
  leftWidth: '250px',
  leftMinWidth: 200,
  leftMaxWidth: 400,
  leftCollapsible: true,
  leftResizable: true,
  leftShadow: 'never',
  rightShadow: 'never',
  leftHeight: '100%',
  rightHeight: '100%',
  autoContentHeight: true,
  leftPadding: true,
  rightPadding: true,
  useScrollbar: true,
  leftUseScrollbar: undefined,
  rightUseScrollbar: undefined,
});

// 计算左侧宽度
const computedLeftWidth = computed(() => {
  if (typeof props.leftWidth === 'number') {
    return `${props.leftWidth}px`;
  }
  return props.leftWidth;
});

// 计算左侧 padding class
const leftPaddingClass = computed(() => {
  if (props.leftPadding === false) return 'fu-page-no-padding';
  if (typeof props.leftPadding === 'string') return props.leftPadding;
  return '';
});

// 计算右侧 padding class
const rightPaddingClass = computed(() => {
  if (props.rightPadding === false) return 'fu-page-no-padding';
  if (typeof props.rightPadding === 'string') return props.rightPadding;
  return '';
});

// 计算左侧是否使用 scrollbar
const leftScrollbar = computed(() => {
  return props.leftUseScrollbar === undefined
    ? props.useScrollbar
    : props.leftUseScrollbar;
});

// 计算右侧是否使用 scrollbar
const rightScrollbar = computed(() => {
  return props.rightUseScrollbar === undefined
    ? props.useScrollbar
    : props.rightUseScrollbar;
});
</script>

<template>
  <Page
    :auto-content-height="autoContentHeight"
    :title="pageTitle"
    :description="pageDescription"
  >
    <!-- Page 头部插槽 -->
    <template v-if="$slots.header" #header>
      <slot name="header"></slot>
    </template>

    <!-- Page 额外内容插槽 -->
    <template v-if="$slots.extra" #extra>
      <slot name="extra"></slot>
    </template>

    <!-- 主内容区域 -->
    <div class="h-full">
      <ElSplitter>
        <!-- 左侧面板 -->
        <ElSplitterPanel
          :size="computedLeftWidth"
          :min="leftMinWidth"
          :max="leftMaxWidth"
          :collapsible="leftCollapsible"
          :resizable="leftResizable"
        >
          <ElCard
            :shadow="leftShadow"
            class="mr-1.5 h-full"
            :class="[leftPaddingClass]"
          >
            <!-- 左侧卡片头部 -->
            <template v-if="leftTitle || $slots['left-header']" #header>
              <slot name="left-header">
                <span>{{ leftTitle }}</span>
              </slot>
            </template>

            <!-- 左侧内容区域 -->
            <component
              :is="leftScrollbar ? ElScrollbar : 'div'"
              :height="leftScrollbar ? leftHeight : undefined"
              :class="[leftContentClass]"
            >
              <slot name="left"></slot>
            </component>
          </ElCard>
        </ElSplitterPanel>

        <!-- 右侧面板 -->
        <ElSplitterPanel>
          <ElCard
            :shadow="rightShadow"
            class="ml-1.5 h-full"
            :class="[rightPaddingClass]"
          >
            <!-- 右侧卡片头部 -->
            <template v-if="rightTitle || $slots['right-header']" #header>
              <slot name="right-header">
                <span>{{ rightTitle }}</span>
              </slot>
            </template>

            <!-- 右侧内容区域 -->
            <component
              :is="rightScrollbar ? ElScrollbar : 'div'"
              :height="rightScrollbar ? rightHeight : undefined"
              :class="[rightContentClass]"
            >
              <slot name="right"></slot>
            </component>
          </ElCard>
        </ElSplitterPanel>
      </ElSplitter>
    </div>

    <!-- Page 底部插槽 -->
    <template v-if="$slots.footer" #footer>
      <slot name="footer"></slot>
    </template>
  </Page>
</template>

<style scoped>
/* 确保卡片占满容器 */
:deep(.el-card) {
  display: flex;
  flex-direction: column;
  height: 100%;
  border: none;
}

:deep(.el-card__body) {
  flex: 1;
  padding: var(--fu-page-card-padding, 20px);
  overflow: hidden;
}

:deep(.el-card__header) {
  padding: 14px 20px;
}

/* 调整分隔条样式 */
:deep(.el-splitter__pane) {
  overflow: visible;
}

/* 隐藏分隔线 - 将分隔线设置为透明 */
:deep(.el-splitter-bar__dragger)::before,
:deep(.el-splitter-bar__dragger)::after {
  background-color: transparent !important;
}

/* 隐藏折叠图标 */
:deep(.el-splitter-bar__collapse-icon) {
  background: transparent !important;
  opacity: 0 !important;
}

/* 禁用 padding 时 */
:deep(.fu-page-no-padding .el-card__body) {
  padding: 0 !important;
}

/* 当不使用 scrollbar 时，确保内容可滚动 */
:deep(.el-card__body > div:not(.el-scrollbar)) {
  height: 100%;
  overflow: auto;
}
</style>
