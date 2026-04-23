# 字典管理模块

## 模块概述

字典管理模块用于管理系统字典数据和字典项，采用左右两栏布局设计：
- **左侧**：字典列表（支持搜索、新增、编辑、删除）
- **右侧**：字典项列表（支持搜索、新增、编辑、删除）

该设计参考 `post` 模块的布局方式，提供一致的用户体验。

## 功能特性

### 字典管理
- ✅ **查看列表**：分页获取所有字典
- ✅ **搜索**：按字典名称和编码模糊搜索
- ✅ **新增**：创建新字典（包含名称、编码、状态、备注）
- ✅ **编辑**：修改现有字典信息
- ✅ **删除**：删除字典及其关联的所有字典项
- ✅ **缓存支持**：完整的缓存机制优化性能

### 字典项管理
- ✅ **查看列表**：查看选中字典的所有字典项
- ✅ **搜索**：按标签和值模糊搜索
- ✅ **新增**：为选中字典添加新字典项（包含标签、值、图标、状态、备注）
- ✅ **编辑**：修改字典项信息
- ✅ **删除**：删除字典项
- ✅ **实时同步**：删除字典时自动清除缓存

## 文件结构

```
dict/
├── index.vue                    # 主页面（左右两栏布局）
├── data.ts                      # 表单schema和表格列配置
├── README.md                    # 本文档
└── modules/
    ├── dict-list.vue            # 字典列表组件
    ├── dict-form-modal.vue      # 字典编辑表单Modal
    ├── dict-item-list.vue       # 字典项列表组件
    └── dict-item-form-modal.vue # 字典项编辑表单Modal
```

## 后端API

### 字典API
- `POST /api/core/dict` - 创建字典
- `GET /api/core/dict` - 获取字典列表（分页）
- `GET /api/core/dict/get/all` - 获取所有字典（缓存）
- `GET /api/core/dict/{dict_id}` - 获取字典详情
- `PUT /api/core/dict/{dict_id}` - 更新字典
- `DELETE /api/core/dict/{dict_id}` - 删除字典

### 字典项API
- `POST /api/core/dict_item` - 创建字典项
- `GET /api/core/dict_item` - 获取字典项列表（分页）
- `GET /api/core/dict_item/get/all` - 获取所有字典项（缓存）
- `GET /api/core/dict_item/{dict_item_id}` - 获取字典项详情
- `PUT /api/core/dict_item/{dict_item_id}` - 更新字典项
- `DELETE /api/core/dict_item/{dict_item_id}` - 删除字典项
- `GET /api/core/dict_item/by/dict_code/{code}` - 按编码获取字典项（缓存）

## 前端API（TypeScript）

### 类型定义

#### Dict
```typescript
interface Dict {
  id: string;
  name: string;
  code: string;
  status: boolean;
  remark?: string;
  sys_create_datetime?: string;
  sys_update_datetime?: string;
}
```

#### DictItem
```typescript
interface DictItem {
  id: string;
  dict_id: string;
  label?: string;
  value?: string;
  icon?: string;
  status: boolean;
  remark?: string;
  sys_create_datetime?: string;
  sys_update_datetime?: string;
}
```

### API函数

#### 字典操作
```typescript
// 创建字典
createDictApi(data: DictCreateInput): Promise<Dict>

// 获取字典列表
getDictListApi(params?: DictListParams): Promise<PaginatedResponse<Dict>>

// 获取所有字典（无分页）
getAllDictApi(): Promise<Dict[]>

// 获取字典详情
getDictDetailApi(dictId: string): Promise<Dict>

// 更新字典
updateDictApi(dictId: string, data: DictUpdateInput): Promise<Dict>

// 删除字典
deleteDictApi(dictId: string): Promise<Dict>
```

#### 字典项操作
```typescript
// 创建字典项
createDictItemApi(data: DictItemCreateInput): Promise<DictItem>

// 获取字典项列表
getDictItemListApi(params?: DictItemListParams): Promise<PaginatedResponse<DictItem>>

// 获取所有字典项（无分页）
getAllDictItemApi(): Promise<DictItem[]>

// 按编码获取字典项
getDictItemByCodeApi(code: string): Promise<DictItem[]>

// 获取字典项详情
getDictItemDetailApi(dictItemId: string): Promise<DictItem>

// 更新字典项
updateDictItemApi(dictItemId: string, data: DictItemUpdateInput): Promise<DictItem>

// 删除字典项
deleteDictItemApi(dictItemId: string): Promise<DictItem>
```

## 国际化标签

模块使用以下i18n标签（需在相应的i18n文件中添加）：

```javascript
// 字典相关
dict.name = '字典'
dict.dictName = '字典名称'
dict.dictCode = '字典编码'
dict.codeFormatError = '编码只能包含字母、数字和下划线'
dict.remark = '备注'
dict.remarkPlaceholder = '请输入备注'
dict.status = '状态'
dict.edit = '编辑'
dict.selectDictFirst = '请先选择字典'

// 字典项相关
dict.itemName = '字典项'
dict.itemLabel = '标签'
dict.itemValue = '值'
dict.itemIcon = '图标'
dict.itemLabel = '标签'
```

## 使用示例

### 在其他组件中调用字典API

```typescript
import { getDictItemByCodeApi } from '#/api/core/dict'

// 获取特定字典的所有项
const items = await getDictItemByCodeApi('user_status')
// items: [
//   { id: '1', value: '1', label: '正常', dict_id: '...' },
//   { id: '2', value: '0', label: '禁用', dict_id: '...' }
// ]
```

### 在表单中使用字典选项

可以使用 `DictSelector` 组件来选择字典，或直接调用API获取字典项作为选择器选项。

## 缓存机制

字典和字典项利用后端的缓存机制提高性能：
- 字典列表缓存时间：1小时
- 字典项列表缓存时间：1小时
- 按编码获取字典项缓存时间：1小时
- 创建/更新/删除操作会自动清除相关缓存

## 布局说明

### 两栏布局结构
```
┌─────────────────────────────────────┐
│  字典管理                           │
├────────────┬────────────────────────┤
│            │                        │
│   左侧     │      右侧              │
│  字典列表  │    字典项列表          │
│  (20%)     │      (80%)             │
│            │                        │
└────────────┴────────────────────────┘
```

### 交互流程
1. 用户在左侧选择字典 → 右侧自动加载该字典的所有项
2. 用户在右侧可对字典项进行CRUD操作
3. 删除字典时，会级联删除所有字典项

## 性能优化

1. **虚拟滚动**：字典列表在数据量大时使用虚拟滚动
2. **后端缓存**：字典和字典项数据使用后端缓存
3. **分页加载**：列表采用分页加载方式，默认每页1000条
4. **搜索优化**：搜索在客户端进行，避免频繁后端请求

## 常见问题

### Q: 删除字典后会发生什么？
A: 删除字典会级联删除所有关联的字典项，并清除所有相关缓存。

### Q: 字典编码有什么规则？
A: 字典编码只能包含字母、数字和下划线，长度2-100个字符。

### Q: 如何在其他页面中使用字典数据？
A: 使用 `getDictItemByCodeApi(code)` 根据字典编码获取字典项列表。

## 技术栈

- Vue 3 + TypeScript
- Tailwind CSS（样式）
- Element Plus（UI组件）
- Vben UI（通用UI组件）
- Vben Form（表单组件）
- Vben Modal（对话框组件）

