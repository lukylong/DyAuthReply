# 字典管理模块实现指南

## 📋 目录

1. [模块概述](#模块概述)
2. [架构设计](#架构设计)
3. [文件组织](#文件组织)
4. [核心功能](#核心功能)
5. [使用说明](#使用说明)
6. [扩展指南](#扩展指南)

## 模块概述

字典管理模块是一个完整的系统字典管理解决方案，提供：
- 系统字典的CRUD操作
- 字典项的管理
- 灵活的搜索和过滤
- 国际化支持
- 高性能缓存

## 架构设计

### 两栏布局架构

```
┌─────────────────────────────────────┐
│       字典管理页面                   │
│  ┌────────────┬───────────────────┐ │
│  │            │                   │ │
│  │ DictList   │  DictItemList     │ │
│  │  (20%)     │     (80%)         │ │
│  │            │                   │ │
│  └────────────┴───────────────────┘ │
└─────────────────────────────────────┘
```

### 组件通信流

```
index.vue (主页面)
    ↓
DictList (字典选择) ──emit──→ onDictSelect
                              ↓
                          DictItemList (字典项展示)
```

### 数据流向

```
User Action
    ↓
Component Method
    ↓
API Call (via dict.ts)
    ↓
Backend API (/api/core/dict*)
    ↓
Response & UI Update
```

## 文件组织

### 核心文件

| 文件 | 用途 | 重要性 |
|------|------|--------|
| `index.vue` | 主页面容器 | ⭐⭐⭐ 必读 |
| `data.ts` | 表单和列配置 | ⭐⭐ 需要时查看 |
| `dict-list.vue` | 字典列表组件 | ⭐⭐⭐ 核心组件 |
| `dict-item-list.vue` | 字典项列表组件 | ⭐⭐⭐ 核心组件 |

### 表单文件

| 文件 | 用途 |
|------|------|
| `dict-form-modal.vue` | 字典编辑表单 |
| `dict-item-form-modal.vue` | 字典项编辑表单 |

### 配置文件

| 文件 | 位置 | 说明 |
|------|------|------|
| `dict.ts` | `src/api/core/` | API接口定义 |
| `dict.json` | `src/locales/langs/zh-CN/` | 中文翻译 |

## 核心功能

### 1. 字典管理

#### 查看
- 分页显示字典列表
- 搜索过滤（名称、编码）
- 显示字典状态

#### 创建
```typescript
// 打开创建表单
function onAddDict() {
  dictFormModalApi.setData(null).open();
}

// 提交数据
await createDictApi({
  name: '新字典',
  code: 'new_dict',
  status: true,
  remark: '备注'
})
```

#### 更新
```typescript
// 打开编辑表单
function onEditDict(dict: Dict) {
  dictFormModalApi.setData(dict).open();
}

// 提交更新
await updateDictApi(dict.id, { /* 更新数据 */ })
```

#### 删除
```typescript
// 确认删除
await ElMessageBox.confirm('确定删除？')
await deleteDictApi(dict.id)
// 级联删除所有字典项
```

### 2. 字典项管理

#### 查看
- 根据选中字典显示项目
- 搜索过滤（标签、值）
- 表格式展示

#### 创建
```typescript
// 打开创建表单
function onAddDictItem() {
  dictItemFormModalApi.setData(null).open();
}

// 提交数据
await createDictItemApi({
  dict_id: currentDictId,
  label: '标签',
  value: '值',
  status: true
})
```

#### 编辑/删除
```typescript
// 编辑
function onEditDictItem(item: DictItem) {
  dictItemFormModalApi.setData(item).open();
}

// 删除
await deleteDictItemApi(item.id)
```

## 使用说明

### 基本使用流程

1. **访问页面**
   ```
   导航到 /core/dict
   ```

2. **选择字典**
   ```
   点击左侧列表中的字典
   右侧自动显示该字典的所有项目
   ```

3. **管理字典项**
   ```
   点击右侧的 + 按钮添加新项
   点击编辑/删除进行操作
   ```

### 常用代码片段

#### 获取字典项用于选择器

```typescript
import { getDictItemByCodeApi } from '#/api/core/dict'

const options = ref([])

onMounted(async () => {
  const items = await getDictItemByCodeApi('user_status')
  options.value = items.map(item => ({
    label: item.label,
    value: item.value
  }))
})
```

#### 在表单中使用字典

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { getDictItemByCodeApi } from '#/api/core/dict'

const form = ref({
  status: ''
})
const statusOptions = ref([])

const loadDictItems = async () => {
  const items = await getDictItemByCodeApi('user_status')
  statusOptions.value = items
}
</script>

<template>
  <el-select 
    v-model="form.status"
    :options="statusOptions"
    option-label="label"
    option-value="value"
  />
</template>
```

## 扩展指南

### 添加新的搜索字段

1. **修改后端Filter**
   ```python
   # backend-v5/core/dict/dict_schema.py
   class DictFilters(FuFilters):
       remark: Optional[str] = Field(None, q="remark__contains")
   ```

2. **修改前端搜索Schema**
   ```typescript
   // data.ts
   export function useDictSearchFormSchema() {
     return [
       // ... 其他字段
       {
         component: 'Input',
         fieldName: 'remark',
         label: $t('dict.remark')
       }
     ]
   }
   ```

3. **更新组件**
   ```typescript
   // dict-list.vue
   const filteredDictList = computed(() => {
     // 添加新的过滤逻辑
   })
   ```

### 自定义字段

例如添加颜色字段：

1. **后端模型**
   ```python
   class DictItem(RootModel):
       color = models.CharField(...)
   ```

2. **前端Schema**
   ```typescript
   {
     component: 'ColorPicker',
     fieldName: 'color',
     label: 'Color'
   }
   ```

3. **表格列**
   ```typescript
   {
     field: 'color',
     title: 'Color',
     minWidth: 100,
     cellRender: {
       name: 'ColorCell'
     }
   }
   ```

### 批量操作

可参考post模块实现批量删除、批量更新等功能。

## API文档

### 字典API

```typescript
// 创建
createDictApi(data: DictCreateInput): Promise<Dict>

// 查询列表
getDictListApi(params?: DictListParams): Promise<PaginatedResponse<Dict>>

// 查询所有
getAllDictApi(): Promise<Dict[]>

// 查询详情
getDictDetailApi(dictId: string): Promise<Dict>

// 更新
updateDictApi(dictId: string, data: DictUpdateInput): Promise<Dict>

// 删除
deleteDictApi(dictId: string): Promise<Dict>
```

### 字典项API

```typescript
// 创建
createDictItemApi(data: DictItemCreateInput): Promise<DictItem>

// 查询列表
getDictItemListApi(params?: DictItemListParams): Promise<PaginatedResponse<DictItem>>

// 查询所有
getAllDictItemApi(): Promise<DictItem[]>

// 按编码查询
getDictItemByCodeApi(code: string): Promise<DictItem[]>

// 查询详情
getDictItemDetailApi(dictItemId: string): Promise<DictItem>

// 更新
updateDictItemApi(dictItemId: string, data: DictItemUpdateInput): Promise<DictItem>

// 删除
deleteDictItemApi(dictItemId: string): Promise<DictItem>
```

## 故障排查

### 问题：页面显示空白

**可能原因**：
- 路由配置错误
- 后端API未运行

**解决方案**：
1. 检查浏览器控制台错误信息
2. 验证API是否可访问
3. 检查路由配置

### 问题：搜索不工作

**可能原因**：
- 搜索逻辑错误
- API不支持该字段

**解决方案**：
1. 检查搜索关键词是否为空
2. 查看API是否返回正确数据
3. 验证搜索过滤逻辑

### 问题：表单验证失败

**可能原因**：
- 必填字段未填
- 格式验证不通过

**解决方案**：
1. 查看错误提示信息
2. 按照规则填写字段
3. 检查表单schema配置

## 最佳实践

### 1. 使用缓存API
```typescript
// 好 - 使用缓存
const dicts = await getAllDictApi()

// 不好 - 频繁分页请求
for (let i = 1; i <= 10; i++) {
  await getDictListApi({ page: i, pageSize: 100 })
}
```

### 2. 错误处理
```typescript
try {
  await createDictApi(data)
  ElMessage.success('创建成功')
} catch (error) {
  ElMessage.error('创建失败')
  console.error(error)
}
```

### 3. 及时清理
```typescript
// 组件卸载时清理资源
onBeforeUnmount(() => {
  // 清理定时器、监听等
})
```

## 性能优化建议

1. **使用虚拟滚动**处理大量字典
2. **减少API调用**，使用缓存
3. **延迟加载**字典项列表
4. **防抖搜索**避免频繁请求

## 相关资源

- [Vue 3 文档](https://vuejs.org/)
- [Element Plus 文档](https://element-plus.org/)
- [后端API文档](../../../backend-v5/core/dict/dict_api.py)
- [Post模块参考](../post/README.md)

