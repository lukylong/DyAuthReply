import { requestClient } from '#/api/request';

import type { PaginatedResponse } from './account';

export type DouyinReplyResult =
  | 'cooldown'
  | 'failed'
  | 'quota_exceeded'
  | 'silent'
  | 'skipped'
  | 'success';

/**
 * 抖音回复日志
 */
export interface DouyinReplyLog {
  id: string;
  account_id: string;
  conversation_id?: null | string;
  trigger_message_id?: null | string;
  matched_rule_id?: null | string;
  rule_name?: null | string;
  peer_nickname?: null | string;
  reply_text: string;
  reply_links: string[];
  result: DouyinReplyResult;
  result_display?: string;
  error_message?: null | string;
  duration_ms: number;
  sent_at?: null | string;
  sys_create_datetime?: string;
}

export interface DouyinReplyLogListParams {
  page?: number;
  pageSize?: number;
  account_id?: string;
  conversation_id?: string;
  matched_rule_id?: string;
  result?: DouyinReplyResult;
}

export interface DouyinReplyLogStat {
  total: number;
  success: number;
  failed: number;
  skipped: number;
  cooldown: number;
  quota_exceeded: number;
  silent: number;
  avg_duration_ms: number;
}

/**
 * 获取回复日志列表（分页）
 */
export async function getDouyinReplyLogListApi(
  params?: DouyinReplyLogListParams,
) {
  return requestClient.get<PaginatedResponse<DouyinReplyLog>>(
    '/api/core/douyin/reply-log',
    { params },
  );
}

/**
 * 获取回复日志详情
 */
export async function getDouyinReplyLogDetailApi(logId: string) {
  return requestClient.get<DouyinReplyLog>(
    `/api/core/douyin/reply-log/${logId}`,
  );
}

/**
 * 获取回复日志统计
 */
export async function getDouyinReplyLogStatApi(accountId?: string) {
  return requestClient.get<DouyinReplyLogStat>(
    '/api/core/douyin/reply-log/stat/summary',
    {
      params: accountId ? { account_id: accountId } : undefined,
    },
  );
}
