import type { VxeTableGridOptions } from '@vben/plugins/vxe-table';

import type { VbenFormSchema } from '#/adapter/form';
import type { OnActionClickFn } from '#/adapter/vxe-table';
import type { DouyinRule } from '#/api/core/douyin';

import { z } from '#/adapter/form';
import { getAllTemplate, getSimpleDouyinAccountListApi } from '#/api/core/douyin';

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

export function getChannelOptions() {
  return [
    { label: '私信', value: 'dm' },
    { label: '评论', value: 'comment' },
    { label: '全部渠道', value: 'all' },
  ];
}

export function getWeekdayOptions() {
  return [
    { label: '周一', value: '1' },
    { label: '周二', value: '2' },
    { label: '周三', value: '3' },
    { label: '周四', value: '4' },
    { label: '周五', value: '5' },
    { label: '周六', value: '6' },
    { label: '周日', value: '7' },
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
        placeholder: '包含关键词模式下生效；每行一个，或用中/英文逗号分隔',
      },
    },
    {
      component: 'Input',
      fieldName: 'regex_pattern',
      label: '正则表达式',
      componentProps: {
        placeholder: '正则匹配模式下生效，例如 (发货|物流|什么时候)',
      },
    },
    {
      component: 'Textarea',
      fieldName: 'reply_text',
      label: '回复文案',
      componentProps: {
        rows: 4,
        placeholder: '未引用模板时使用；支持 {{nickname}} / {{time_greeting}} 等变量占位',
      },
      dependencies: {
        show: (v: any) => !v.template_id,
        triggerFields: ['template_id'],
      },
    },
    {
      component: 'ApiSelect',
      fieldName: 'template_id',
      label: '引用模板',
      componentProps: {
        placeholder: '可选：优先使用模板内容',
        clearable: true,
        api: getAllTemplate,
        labelField: 'name',
        valueField: 'id',
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
      dependencies: {
        show: (v: any) => !v.template_id,
        triggerFields: ['template_id'],
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
      dependencies: {
        show: (v: any) => !v.template_id,
        triggerFields: ['template_id'],
      },
    },
    {
      component: 'Select',
      fieldName: 'channel',
      label: '生效渠道',
      defaultValue: 'dm',
      componentProps: {
        options: getChannelOptions(),
      },
    },
    {
      component: 'CheckboxGroup',
      fieldName: 'weekday_values',
      label: '生效星期',
      defaultValue: ['1', '2', '3', '4', '5', '6', '7'],
      componentProps: {
        options: getWeekdayOptions(),
      },
    },
    {
      component: 'TimePicker',
      fieldName: 'time_window_start',
      label: '开始时间',
      componentProps: {
        clearable: true,
        format: 'HH:mm',
        valueFormat: 'HH:mm:ss',
        placeholder: '为空表示全天',
      },
    },
    {
      component: 'TimePicker',
      fieldName: 'time_window_end',
      label: '结束时间',
      componentProps: {
        clearable: true,
        format: 'HH:mm',
        valueFormat: 'HH:mm:ss',
        placeholder: '为空表示全天',
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
      field: 'template_name',
      title: '引用模板',
      minWidth: 140,
      formatter: ({ row }) => row.template_name || '-',
    },
    {
      field: 'send_mode',
      title: '发送模式',
      width: 110,
      cellRender: { name: 'CellTag', options: getSendModeOptions() },
    },
    {
      field: 'channel',
      title: '渠道',
      width: 100,
      formatter: ({ row }) => {
        if (row.channel === 'all') return '全部';
        if (row.channel === 'comment') return '评论';
        return '私信';
      },
    },
    {
      field: 'time_window_start',
      title: '生效时段',
      width: 160,
      formatter: ({ row }) => {
        if (!row.time_window_start || !row.time_window_end) return '全天';
        return `${row.time_window_start}~${row.time_window_end}`;
      },
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
