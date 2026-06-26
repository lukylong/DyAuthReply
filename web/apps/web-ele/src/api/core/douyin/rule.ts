import { requestClient } from '#/api/request';

import type { PaginatedResponse } from './account';

export type DouyinRuleMatchType = 'contains' | 'default' | 'regex';
export type DouyinRuleSendMode = 'card_fallback' | 'merged' | 'multi_message';
export type DouyinRuleChannel = 'all' | 'comment' | 'dm';

/**
 * 抖音回复规则
 *
 * 注意：account_id 可为 null —— null 表示全局规则，对所有账号生效（兜底/默认规则）。
 * 后端在 account_id 为空时会把 account_nickname 返回为 "全部账号（默认规则）"。
 */
export interface DouyinRule {
  id: string;
  account_id: null | string;
  account_nickname?: null | string;
  name: string;
  match_type: DouyinRuleMatchType;
  match_type_display?: string;
  keywords: string[];
  regex_pattern?: null | string;
  reply_text: string;
  links: string[];
  template_id?: null | string;
  template_name?: null | string;
  card_ids?: string[];
  cards?: Array<{
    id: string;
    title: string;
    cover_url?: null | string;
    target_url?: null | string;
    status?: boolean;
  }>;
  send_mode: DouyinRuleSendMode;
  send_mode_display?: string;
  channel?: DouyinRuleChannel;
  time_window_start?: null | string;
  time_window_end?: null | string;
  weekday_mask?: string;
  priority: number;
  status: boolean;
  cooldown_seconds: number;
  hit_count: number;
  remark?: null | string;
  sort?: number;
  sys_create_datetime?: string;
  sys_update_datetime?: string;
}

export interface DouyinRuleCreateInput {
  /** 留空（null）表示全局规则，对所有账号生效 */
  account_id: null | string;
  name: string;
  match_type?: DouyinRuleMatchType;
  keywords?: string[];
  regex_pattern?: null | string;
  reply_text?: string;
  links?: string[];
  template_id?: null | string;
  send_mode?: DouyinRuleSendMode;
  channel?: DouyinRuleChannel;
  time_window_start?: null | string;
  time_window_end?: null | string;
  weekday_mask?: string;
  priority?: number;
  status?: boolean;
  cooldown_seconds?: number;
  remark?: null | string;
  sort?: number;
  card_ids?: string[];
}

export type DouyinRuleUpdateInput = Partial<DouyinRuleCreateInput>;

export interface DouyinRuleListParams {
  page?: number;
  pageSize?: number;
  account_id?: string;
  name?: string;
  match_type?: DouyinRuleMatchType;
  status?: boolean;
}

export interface DouyinRuleQuickEnableInput {
  account_id: string;
  reply_text: string;
  keywords?: string[];
  cooldown_seconds?: number;
  send_mode?: DouyinRuleSendMode;
}

export interface DouyinRuleQuickEnableResult {
  created: boolean;
  message: string;
  rule_id: string;
}

/**
 * 获取规则列表（分页）
 */
export async function getDouyinRuleListApi(params?: DouyinRuleListParams) {
  return requestClient.get<PaginatedResponse<DouyinRule>>(
    '/api/core/douyin/rule',
    { params },
  );
}

/**
 * 按账号获取全部规则（不分页）
 */
export async function getDouyinRulesByAccountApi(accountId: string) {
  return requestClient.get<DouyinRule[]>(
    `/api/core/douyin/rule/by/account/${accountId}`,
  );
}

/**
 * 获取规则详情
 */
export async function getDouyinRuleDetailApi(ruleId: string) {
  return requestClient.get<DouyinRule>(`/api/core/douyin/rule/${ruleId}`);
}

/**
 * 创建规则
 */
export async function createDouyinRuleApi(data: DouyinRuleCreateInput) {
  return requestClient.post<DouyinRule>('/api/core/douyin/rule', data);
}

/**
 * 更新规则
 */
export async function updateDouyinRuleApi(
  ruleId: string,
  data: DouyinRuleCreateInput,
) {
  return requestClient.put<DouyinRule>(
    `/api/core/douyin/rule/${ruleId}`,
    data,
  );
}

/**
 * 部分更新规则
 */
export async function patchDouyinRuleApi(
  ruleId: string,
  data: DouyinRuleUpdateInput,
) {
  return requestClient.patch<DouyinRule>(
    `/api/core/douyin/rule/${ruleId}`,
    data,
  );
}

/**
 * 删除规则
 */
export async function deleteDouyinRuleApi(ruleId: string) {
  return requestClient.delete<DouyinRule>(`/api/core/douyin/rule/${ruleId}`);
}

/**
 * 批量删除规则
 */
export async function batchDeleteDouyinRuleApi(ids: string[]) {
  return requestClient.post<{ count: number }>(
    '/api/core/douyin/rule/batch/delete',
    { ids },
  );
}

/**
 * 一键开启陌生人自动回复（创建/更新快捷兜底规则）
 */
export async function quickEnableDouyinRuleApi(data: DouyinRuleQuickEnableInput) {
  return requestClient.post<DouyinRuleQuickEnableResult>(
    '/api/core/douyin/rule/quick-enable',
    data,
  );
}
