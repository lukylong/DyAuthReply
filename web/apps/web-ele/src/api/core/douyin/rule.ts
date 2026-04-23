import { requestClient } from '#/api/request';

import type { PaginatedResponse } from './account';

export type DouyinRuleMatchType = 'contains' | 'default' | 'regex';
export type DouyinRuleSendMode = 'card_fallback' | 'merged' | 'multi_message';

/**
 * 抖音回复规则
 */
export interface DouyinRule {
  id: string;
  account_id: string;
  account_nickname?: string;
  name: string;
  match_type: DouyinRuleMatchType;
  match_type_display?: string;
  keywords: string[];
  regex_pattern?: null | string;
  reply_text: string;
  links: string[];
  send_mode: DouyinRuleSendMode;
  send_mode_display?: string;
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
  account_id: string;
  name: string;
  match_type?: DouyinRuleMatchType;
  keywords?: string[];
  regex_pattern?: null | string;
  reply_text?: string;
  links?: string[];
  send_mode?: DouyinRuleSendMode;
  priority?: number;
  status?: boolean;
  cooldown_seconds?: number;
  remark?: null | string;
  sort?: number;
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
