# ZqDialog 对话框组件

基于 Element Plus Dialog 的二次封装，提供更便捷的使用方式。

## 特性

- 完全兼容 ElDialog 所有属性
- 内置 ElScrollbar 滚动区域
- 内容区 Loading 遮罩
- 确认按钮 Loading 状态
- 自定义头部：全屏切换 + 关闭按钮
- 内置 Footer 按钮（可自定义）
- 默认可拖拽

## 基础用法

```vue
<script setup lang="ts">
import { ref } from 'vue';
import { ZqDialog } from '@/components/zq-dialog';

const visible = ref(false);

function handleConfirm() {
  console.log('确认');
  visible.value = false;
}
</script>

<template>
  <el-button @click="visible = true">打开对话框</el-button>
  
  <ZqDialog
    v-model="visible"
    title="基础对话框"
    width="500px"
    @confirm="handleConfirm"
  >
    <p>这是对话框内容</p>
  </ZqDialog>
</template>
```

## 带 Loading 的异步提交

```vue
<script setup lang="ts">
import { ref } from 'vue';
import { ZqDialog } from '@/components/zq-dialog';

const visible = ref(false);
const loading = ref(false);
const confirmLoading = ref(false);

async function handleConfirm() {
  confirmLoading.value = true;
  try {
    await submitForm();
    visible.value = false;
  } finally {
    confirmLoading.value = false;
  }
}
</script>

<template>
  <ZqDialog
    v-model="visible"
    title="编辑用户"
    :loading="loading"
    :confirm-loading="confirmLoading"
    @confirm="handleConfirm"
  >
    <el-form>...</el-form>
  </ZqDialog>
</template>
```

## 限制内容区高度

```vue
<ZqDialog
  v-model="visible"
  title="长内容"
  max-height="400px"
>
  <div>很长的内容...</div>
</ZqDialog>
```

## 自定义 Footer

```vue
<ZqDialog v-model="visible" title="详情">
  <template #footer>
    <el-button @click="handlePrint">打印</el-button>
    <el-button type="primary" @click="visible = false">关闭</el-button>
  </template>
</ZqDialog>
```

## 隐藏 Footer

```vue
<ZqDialog v-model="visible" title="预览" :show-footer="false">
  <img src="..." />
</ZqDialog>
```

## 使用 Expose 方法

```vue
<script setup lang="ts">
import { ref } from 'vue';
import type { ZqDialogExpose } from '@/components/zq-dialog';

const dialogRef = ref<ZqDialogExpose>();

function openDialog() {
  dialogRef.value?.open();
}

function startLoading() {
  dialogRef.value?.setLoading(true);
}
</script>

<template>
  <ZqDialog ref="dialogRef" title="测试">
    内容
  </ZqDialog>
</template>
```

## Props

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| modelValue | boolean | false | v-model 控制显隐 |
| title | string | '' | 标题 |
| width | string \| number | '50%' | 宽度 |
| contentHeight | string \| number | - | 内容区固定高度 |
| maxHeight | string \| number | - | 内容区最大高度 |
| loading | boolean | false | 内容区 loading |
| confirmLoading | boolean | false | 确认按钮 loading |
| showFooter | boolean | true | 显示底部 |
| showConfirmButton | boolean | true | 显示确认按钮 |
| showCancelButton | boolean | true | 显示取消按钮 |
| confirmText | string | '确定' | 确认按钮文字 |
| cancelText | string | '取消' | 取消按钮文字 |
| confirmButtonType | string | 'primary' | 确认按钮类型 |
| showFullscreenButton | boolean | true | 显示全屏按钮 |
| showCloseButton | boolean | true | 显示关闭按钮 |
| draggable | boolean | true | 可拖拽 |
| destroyOnClose | boolean | true | 关闭销毁 |
| closeOnClickModal | boolean | false | 点击遮罩关闭 |
| appendToBody | boolean | true | 插入 body |

> 其他 ElDialog 属性通过 `v-bind="$attrs"` 透传

## Events

| 事件 | 说明 |
|------|------|
| update:modelValue | v-model 更新 |
| confirm | 点击确认按钮 |
| cancel | 点击取消按钮 |
| open | 打开时 |
| opened | 打开动画结束 |
| close | 关闭时 |
| closed | 关闭动画结束 |

## Slots

| 插槽 | 说明 |
|------|------|
| default | 对话框内容 |
| title | 自定义标题 |
| header-extra | 头部额外内容（在图标按钮之前） |
| footer | 自定义底部 |

## Expose

| 方法 | 说明 |
|------|------|
| open() | 打开对话框 |
| close() | 关闭对话框 |
| setLoading(val) | 设置内容区 loading |
| setConfirmLoading(val) | 设置确认按钮 loading |
