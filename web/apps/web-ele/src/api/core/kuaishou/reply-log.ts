import { requestClient } from '#/api/request';

import type { PaginatedResponse } from './account';

export type KuaishouReplyResult =
  | 'cooldown'
  | 'failed'
  | 'quota_exceeded'
  | 'silent'
  | 'skipped'
  | 'success';

export interface KuaishouReplyLog {
  id: string;
  account_id: string;
  account_nickname?: null | string;
  conversation_id?: null | string;
  trigger_message_id?: null | string;
  trigger_message_content?: null | string;
  matched_rule_id?: null | string;
  rule_name?: null | string;
  peer_nickname?: null | string;
  reply_text: string;
  reply_links: string[];
  result: KuaishouReplyResult;
  result_display?: string;
  error_message?: null | string;
  duration_ms: number;
  sent_at?: null | string;
  sys_create_datetime?: string;
}

export interface KuaishouReplyLogStat {
  total: number;
  success: number;
  failed: number;
  skipped: number;
  cooldown: number;
  quota_exceeded: number;
  silent: number;
  avg_duration_ms: number;
}

export async function getKuaishouReplyLogList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<KuaishouReplyLog>>(
    '/api/core/kuaishou/reply-log',
    { params },
  );
}

export async function getKuaishouReplyLogStat(accountId?: string) {
  return requestClient.get<KuaishouReplyLogStat>(
    '/api/core/kuaishou/reply-log/stat/summary',
    { params: accountId ? { account_id: accountId } : undefined },
  );
}
