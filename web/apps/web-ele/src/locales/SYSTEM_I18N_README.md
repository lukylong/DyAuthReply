# System 国际化配置文档

## 📁 文件位置

```
src/locales/langs/
├── zh-CN/system.json          # 中文翻译
└── en-US/system.json          # 英文翻译
```

## 📋 国际化键值说明

### 菜单管理 (system.menu)

| 键值 | 中文 | 英文 | 用途 |
|------|------|------|------|
| `name` | 菜单管理 | Menu Management | 菜单名称 |
| `title` | 菜单管理 | Menu Management | 页面标题 |
| `menuName` | 菜单名称 | Menu Name | 表单字段标签 |
| `menuTitle` | 菜单标题 | Menu Title | 表单字段标签 |
| `parent` | 父级菜单 | Parent Menu | 表单字段标签 |
| `path` | 菜单路径 | Menu Path | 表单字段标签 |
| `activePath` | 活跃路径 | Active Path | 表单字段标签 |
| `activePathHelp` | 高亮菜单的路径，用于解决路由路径和菜单高亮不一致的问题 | ... | 帮助文本 |
| `activePathMustExist` | 活跃路径必须是一个存在的菜单路径 | ... | 验证错误信息 |
| `type` | 菜单类型 | Menu Type | 表单字段标签 |
| `typeCatalog` | 目录 | Catalog | 菜单类型选项 |
| `typeMenu` | 菜单 | Menu | 菜单类型选项 |
| `typeButton` | 按钮 | Button | 菜单类型选项 |
| `typeEmbedded` | 内嵌 | Embedded | 菜单类型选项 |
| `typeLink` | 外链 | Link | 菜单类型选项 |
| `component` | 组件 | Component | 表单字段标签 |
| `icon` | 菜单图标 | Menu Icon | 表单字段标签 |
| `activeIcon` | 活跃图标 | Active Icon | 表单字段标签 |
| `status` | 状态 | Status | 表单字段标签 |
| `authCode` | 权限编码 | Permission Code | 表单字段标签 |
| `linkSrc` | 链接地址 | Link URL | 表单字段标签 |
| `operation` | 操作 | Operation | 表格列标题 |
| `advancedSettings` | 高级设置 | Advanced Settings | 表单分组标题 |
| `keepAlive` | KeepAlive 缓存 | KeepAlive Cache | 表单字段标签 |
| `affixTab` | 固定标签页 | Affix Tab | 表单字段标签 |
| `hideInMenu` | 隐藏菜单 | Hide in Menu | 表单字段标签 |
| `hideChildrenInMenu` | 隐藏子菜单 | Hide Children in Menu | 表单字段标签 |
| `hideInBreadcrumb` | 隐藏面包屑 | Hide in Breadcrumb | 表单字段标签 |
| `hideInTab` | 隐藏标签页 | Hide in Tab | 表单字段标签 |
| `badgeType.title` | Badge 类型 | Badge Type | 表单字段标签 |
| `badgeType.dot` | 点 | Dot | Badge 类型选项 |
| `badgeType.normal` | 数字 | Number | Badge 类型选项 |
| `badge` | Badge 内容 | Badge Content | 表单字段标签 |
| `badgeVariants` | Badge 样式 | Badge Variant | 表单字段标签 |
| `order` | 排序 | Order | 表单字段标签 |

### 按钮管理 (system.button)

| 键值 | 中文 | 英文 | 用途 |
|------|------|------|------|
| `name` | 按钮 | Button | 按钮名称 |
| `buttonName` | 按钮名称 | Button Name | 表单字段标签 |
| `buttonCode` | 按钮编码 | Button Code | 表单字段标签 |
| `method` | 请求方式 | HTTP Method | 表单字段标签 |
| `api` | API 路径 | API Path | 表单字段标签 |
| `sort` | 排序 | Sort | 表单字段标签 |
| `remark` | 备注 | Remark | 表单字段标签 |
| `createTime` | 创建时间 | Create Time | 表格列标题 |
| `manageButtons` | 管理【{0}】的按钮 | Manage 【{0}】 Buttons | 抽屉标题 (参数化) |
| `buttonList` | 按钮列表 | Button List | 表格标题 |
| `batchDelete` | 批量删除 | Batch Delete | 按钮标签 |
| `batchDeleteTitle` | 批量删除按钮 | Batch Delete Buttons | 确认框标题 |
| `batchDeleteConfirm` | 确定要删除选中的 {0} 个按钮吗？{1} | Are you sure... | 确认框信息 |
| `deletingButtons` | 正在删除 {0} 个按钮... | Deleting {0} buttons... | 加载提示 |
| `deleteSuccess` | 成功删除 {0} 个按钮 | Successfully deleted... | 成功提示 |
| `deleteError` | 删除按钮失败 | Failed to delete buttons | 错误提示 |
| `selectButtonsToDelete` | 请先选择要删除的按钮 | Please select buttons... | 警告提示 |
| `quickAdd` | 快速添加 | Quick Add | 按钮标签 |
| `quickAddTitle` | 快速添加按钮 | Quick Add Buttons | 模态框标题 |
| `quickAddApi` | API 路径 | API Path | 表单字段标签 |
| `quickAddApiPlaceholder` | 例如: /api/system/menu | e.g. /api/system/menu | 输入框占位符 |
| `invalidApi` | 无效的 API 路径 | Invalid API path | 错误提示 |

## 💡 使用方法

### 在 Vue 组件中使用

```typescript
import { $t } from '#/locales';

// 基础使用
const title = $t('system.menu.title');

// 带参数的使用
const message = $t('system.button.manageButtons', ['菜单名称']);

// 在模板中使用
{{ $t('system.menu.menuName') }}
```

### 在 TypeScript 中使用

```typescript
import { $t } from '#/locales';

const schema: VbenFormSchema[] = [
  {
    component: 'Input',
    fieldName: 'name',
    label: $t('system.menu.menuName'),  // 使用国际化文本
  },
];
```

## 📝 国际化文件结构

### zh-CN/system.json 结构

```json
{
  "menu": {
    "name": "...",
    "title": "...",
    // ... 菜单相关翻译
  },
  "button": {
    "name": "...",
    "buttonName": "...",
    // ... 按钮相关翻译
  }
}
```

### en-US/system.json 结构

完全相同的结构，但内容为英文翻译。

## 🔄 自动加载机制

国际化文件通过 `import.meta.glob` 自动加载：

```typescript
const modules = import.meta.glob('./langs/**/*.json');

const localesMap = loadLocalesMapFromDir(
  /\.\/langs\/([^/]+)\/(.*)\.json$/,
  modules,
);
```

这意味着：
- 新增的 system.json 会自动被加载
- 无需手动注册
- 支持动态切换语言

## 🔧 扩展指南

### 添加新的国际化字段

1. 在 `zh-CN/system.json` 中添加中文翻译：
   ```json
   {
     "menu": {
       "newField": "新字段"
     }
   }
   ```

2. 在 `en-US/system.json` 中添加英文翻译：
   ```json
   {
     "menu": {
       "newField": "New Field"
     }
   }
   ```

3. 在代码中使用：
   ```typescript
   $t('system.menu.newField')
   ```

### 参数化翻译

使用 `{0}`, `{1}` 等占位符：

```json
{
  "button": {
    "manageButtons": "管理【{0}】的按钮"
  }
}
```

在代码中使用：

```typescript
$t('system.button.manageButtons', ['菜单名称'])
// 结果：管理【菜单名称】的按钮
```

## ✨ 特点

- 完整覆盖菜单管理的所有文本
- 支持中英文双语
- 自动加载机制
- 参数化翻译支持
- 清晰的组织结构

## 📌 注意事项

1. **保持一致性**：确保中文和英文版本有相同的结构
2. **命名规范**：使用小驼峰命名法（camelCase）
3. **分组管理**：按功能模块分组（menu、button）
4. **参数化字符串**：使用 `{0}`, `{1}` 表示参数位置

---

**创建时间**：2025年1月
**维护状态**：活跃
**语言支持**：中文、英文
