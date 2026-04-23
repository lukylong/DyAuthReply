import { requestClient } from '#/api/request';

export interface DashboardOverview {
  accounts_total: number;
  accounts_online: number;
  accounts_offline: number;
  sessions_running: number;
  messages_today: number;
  replies_today: number;
  replies_failed_today: number;
  events_error_today: number;
  avg_response_ms: number;
}

export interface DashboardTrendPoint {
  stat_date: string;
  messages_received: number;
  replies_sent: number;
  replies_failed: number;
}

export interface DashboardAccountRankItem {
  account_id: string;
  nickname: string;
  replies: number;
  messages: number;
  response_ms: number;
}

export interface DashboardRuleHitItem {
  rule_id: string;
  rule_name: null | string;
  hit_count: number;
}

export interface StatQuery {
  start_date?: string;
  end_date?: string;
  account_id?: string;
  group_id?: string;
}

export async function getDashboardOverview() {
  return requestClient.get<DashboardOverview>('/api/core/douyin/dashboard/overview');
}

export async function getDashboardTrend(query: StatQuery = {}) {
  return requestClient.get<{ points: DashboardTrendPoint[] }>(
    '/api/core/douyin/dashboard/trend',
    { params: query },
  );
}

export async function getDashboardAccountRank(query: StatQuery = {}) {
  return requestClient.get<{ items: DashboardAccountRankItem[] }>(
    '/api/core/douyin/dashboard/account-rank',
    { params: query },
  );
}

export async function getDashboardRuleHits() {
  return requestClient.get<{ items: DashboardRuleHitItem[] }>(
    '/api/core/douyin/dashboard/rule-hits',
  );
}

// 批量导入
export interface AccountImportItem {
  nickname: string;
  group_id?: null | string;
  remark?: string;
  daily_reply_quota?: number;
  min_interval_seconds?: number;
  max_interval_seconds?: number;
  work_mode?: 'auto' | 'hybrid' | 'manual';
  priority?: number;
  tags?: string[];
  proxy_url?: null | string;
}

export async function batchImportAccount(
  items: AccountImportItem[],
  skip_duplicate = true,
) {
  return requestClient.post<{
    created_count: number;
    failed_items: { error: string; index: number; nickname: string }[];
    skipped_count: number;
  }>('/api/core/douyin/account/batch/import', { items, skip_duplicate });
}
