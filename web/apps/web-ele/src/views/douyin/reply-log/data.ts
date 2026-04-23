import type { VxeTableGridOptions } from '@vben/plugins/vxe-table';

import type { VbenFormSchema } from '#/adapter/form';
import type { OnActionClickFn } from '#/adapter/vxe-table';
import type { DouyinReplyLog } from '#/api/core/douyin';

import { getSimpleDouyinAccountListApi } from '#/api/core/douyin';

/**
 * 回复结果枚举选项（标签 + 颜色）
 */
export function getResultOptions() {
  return [
    { label: '成功', value: 'success', type: 'success' },
    { label: '失败', value: 'failed', type: 'danger' },
    { label: '跳过', value: 'skipped', type: 'info' },
    { label: '冷却中', value: 'cooldown', type: 'warning' },
    { label: '超限', value: 'quota_exceeded', type: 'warning' },
    { label: '静默', value: 'silent', type: 'info' },
  ];
}

/**
 * 搜索表单 Schema
 */
export function useSearchFormSchema(): VbenFormSchema[] {
  return [
    {
      component: 'ApiSelect',
      fieldName: 'account_id',
      label: '账号',
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
      fieldName: 'result',
      label: '结果',
      componentProps: {
        placeholder: '请选择',
        clearable: true,
        options: getResultOptions(),
      },
    },
  ];
}

/**
 * 回复日志表格列配置
 */
export function useReplyLogTableColumns(
  onActionClick?: OnActionClickFn<DouyinReplyLog>,
): VxeTableGridOptions<DouyinReplyLog>['columns'] {
  return [
    {
      field: 'sent_at',
      title: '回复时间',
      width: 170,
      fixed: 'left',
      formatter: ({ row }) => row.sent_at || row.sys_create_datetime || '-',
    },
    {
      field: 'account_nickname',
      title: '账号',
      minWidth: 140,
    },
    {
      field: 'peer_nickname',
      title: '用户',
      minWidth: 140,
      formatter: ({ cellValue }) => cellValue || '-',
    },
    {
      field: 'rule_name',
      title: '命中规则',
      minWidth: 140,
      formatter: ({ cellValue }) => cellValue || '-',
    },
    {
      field: 'trigger_message_content',
      title: '触发消息',
      minWidth: 200,
      showOverflow: 'tooltip',
      formatter: ({ cellValue }) => cellValue || '-',
    },
    {
      field: 'reply_text',
      title: '回复内容',
      minWidth: 260,
      showOverflow: 'tooltip',
    },
    {
      field: 'result',
      title: '结果',
      width: 100,
      cellRender: {
        name: 'CellTag',
        options: getResultOptions(),
      },
    },
    {
      field: 'duration_ms',
      title: '耗时(ms)',
      width: 100,
      align: 'right',
      sortable: true,
    },
    {
      field: 'error_message',
      title: '错误信息',
      minWidth: 180,
      showOverflow: 'tooltip',
      formatter: ({ cellValue }) => cellValue || '-',
      visible: false,
    },
    {
      field: 'operation',
      title: '操作',
      width: 90,
      fixed: 'right',
      align: 'right',
      headerAlign: 'center',
      showOverflow: false,
      cellRender: {
        name: 'CellOperation',
        options: [{ code: 'detail', text: '详情', icon: 'ep:document' }],
        attrs: {
          nameField: 'reply_text',
          nameTitle: '日志',
          onClick: onActionClick,
        },
      },
    },
  ];
}
