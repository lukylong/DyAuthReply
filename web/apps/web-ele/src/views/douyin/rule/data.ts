import type { VxeTableGridOptions } from '@vben/plugins/vxe-table';

import type { VbenFormSchema } from '#/adapter/form';
import type { OnActionClickFn } from '#/adapter/vxe-table';
import type { DouyinRule } from '#/api/core/douyin';

import { z } from '#/adapter/form';
import { getSimpleDouyinAccountListApi } from '#/api/core/douyin';

/**
 * 匹配方式选项
 */
export function getMatchTypeOptions() {
  return [
    { label: '包含关键词', value: 'contains', type: 'primary' },
    { label: '正则匹配', value: 'regex', type: 'warning' },
    { label: '默认兜底', value: 'default', type: 'info' },
  ];
}

/**
 * 发送模式选项
 */
export function getSendModeOptions() {
  return [
    { label: '合并发送', value: 'merged' },
    { label: '多条发送', value: 'multi_message' },
    { label: '卡片兜底', value: 'card_fallback' },
  ];
}

/**
 * 启用/停用状态选项
 */
export function getStatusOptions() {
  return [
    { label: '启用', value: true, type: 'success' },
    { label: '停用', value: false, type: 'info' },
  ];
}

/**
 * 搜索表单 Schema
 */
export function useSearchFormSchema(): VbenFormSchema[] {
  return [
    {
      component: 'Input',
      fieldName: 'name',
      label: '规则名称',
      componentProps: {
        placeholder: '输入规则名称搜索',
        clearable: true,
      },
    },
    {
      component: 'ApiSelect',
      fieldName: 'account_id',
      label: '所属账号',
      componentProps: {
        placeholder: '请选择账号',
        clearable: true,
        api: getSimpleDouyinAccountListApi,
        labelField: 'nickname',
        valueField: 'id',
      },
    },
    {
      component: 'Select',
      fieldName: 'match_type',
      label: '匹配方式',
      componentProps: {
        placeholder: '请选择',
        clearable: true,
        options: getMatchTypeOptions(),
      },
    },
    {
      component: 'Select',
      fieldName: 'status',
      label: '状态',
      componentProps: {
        placeholder: '请选择',
        clearable: true,
        options: getStatusOptions(),
      },
    },
  ];
}

/**
 * 新增/编辑规则表单 Schema
 */
export function useRuleFormSchema(): VbenFormSchema[] {
  return [
    {
      component: 'ApiSelect',
      fieldName: 'account_id',
      label: '所属账号',
      componentProps: {
        placeholder: '请选择账号',
        api: getSimpleDouyinAccountListApi,
        labelField: 'nickname',
        valueField: 'id',
      },
      rules: z.string().min(1, '请选择所属账号'),
    },
    {
      component: 'Input',
      fieldName: 'name',
      label: '规则名称',
      rules: z
        .string()
        .min(1, '请输入规则名称')
        .max(64, '不超过 64 字符'),
    },
    {
      component: 'Select',
      fieldName: 'match_type',
      label: '匹配方式',
      defaultValue: 'contains',
      componentProps: {
        options: getMatchTypeOptions(),
      },
      rules: z.enum(['contains', 'regex', 'default']),
    },
    {
      component: 'Textarea',
      fieldName: 'keywords_text',
      label: '关键词',
      componentProps: {
        rows: 3,
        placeholder: '每行一个，或用中/英文逗号分隔',
      },
      dependencies: {
        show: (v: any) => v.match_type === 'contains',
        triggerFields: ['match_type'],
      },
    },
    {
      component: 'Input',
      fieldName: 'regex_pattern',
      label: '正则表达式',
      componentProps: {
        placeholder: '例如 (发货|物流|什么时候)',
      },
      dependencies: {
        show: (v: any) => v.match_type === 'regex',
        triggerFields: ['match_type'],
      },
    },
    {
      component: 'Textarea',
      fieldName: 'reply_text',
      label: '回复文案',
      componentProps: {
        rows: 4,
        placeholder: '支持 {nickname} / {time_greeting} 等变量占位',
      },
    },
    {
      component: 'Textarea',
      fieldName: 'links_text',
      label: '附带链接',
      componentProps: {
        rows: 2,
        placeholder: '每行一个 URL（可选）',
      },
    },
    {
      component: 'Select',
      fieldName: 'send_mode',
      label: '发送模式',
      defaultValue: 'merged',
      componentProps: {
        options: getSendModeOptions(),
      },
    },
    {
      component: 'InputNumber',
      fieldName: 'priority',
      label: '优先级',
      defaultValue: 0,
      componentProps: {
        min: 0,
        max: 9999,
        placeholder: '数值越大越优先',
      },
    },
    {
      component: 'InputNumber',
      fieldName: 'cooldown_seconds',
      label: '冷却秒数',
      defaultValue: 0,
      componentProps: {
        min: 0,
        placeholder: '同一用户触发后的冷却时间',
      },
    },
    {
      component: 'Input',
      fieldName: 'remark',
      label: '备注',
      componentProps: {
        placeholder: '可选，便于记忆规则用途',
      },
    },
    {
      component: 'Switch',
      fieldName: 'status',
      label: '启用',
      defaultValue: true,
    },
  ];
}

/**
 * 规则列表表格列配置
 */
export function useRuleTableColumns(
  onActionClick?: OnActionClickFn<DouyinRule>,
): VxeTableGridOptions<DouyinRule>['columns'] {
  return [
    { type: 'checkbox', width: 60, align: 'center', fixed: 'left' },
    {
      field: 'name',
      title: '规则名称',
      minWidth: 160,
      fixed: 'left',
    },
    {
      field: 'account_nickname',
      title: '账号',
      minWidth: 140,
    },
    {
      field: 'match_type',
      title: '匹配方式',
      width: 110,
      cellRender: { name: 'CellTag', options: getMatchTypeOptions() },
    },
    {
      field: 'keywords',
      title: '关键词',
      minWidth: 200,
      formatter: ({ row }) => {
        const arr = row.keywords || [];
        if (arr.length === 0) return '-';
        const visible = arr.slice(0, 3).join('、');
        return arr.length > 3 ? `${visible} …` : visible;
      },
    },
    {
      field: 'send_mode',
      title: '发送模式',
      width: 110,
      cellRender: { name: 'CellTag', options: getSendModeOptions() },
    },
    {
      field: 'priority',
      title: '优先级',
      width: 80,
      align: 'center',
      sortable: true,
    },
    {
      field: 'cooldown_seconds',
      title: '冷却(s)',
      width: 90,
      align: 'center',
    },
    {
      field: 'hit_count',
      title: '命中次数',
      width: 90,
      align: 'center',
      sortable: true,
    },
    {
      field: 'status',
      title: '状态',
      width: 80,
      cellRender: { name: 'CellTag', options: getStatusOptions() },
    },
    {
      field: 'sys_update_datetime',
      title: '更新时间',
      width: 170,
    },
    {
      field: 'operation',
      title: '操作',
      width: 140,
      fixed: 'right',
      align: 'right',
      headerAlign: 'center',
      showOverflow: false,
      cellRender: {
        name: 'CellOperation',
        options: ['edit', 'delete'],
        attrs: {
          nameField: 'name',
          nameTitle: '规则',
          onClick: onActionClick,
        },
      },
    },
  ];
}
