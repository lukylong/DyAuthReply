import { requestClient } from '#/api/request';

/**
 * 抖音账号
 */
export interface DouyinAccount {
  id: string;
  nickname: string;
  sec_uid?: null | string;
  avatar?: null | string;
  status: number;
  auto_reply_enabled: boolean;
  status_display?: string;
  storage_state_path?: string;
  owner_id?: null | string;
  owner_name?: null | string;
  daily_reply_quota: number;
  min_interval_seconds: number;
  max_interval_seconds: number;
  silent_start: string;
  silent_end: string;
  last_heartbeat?: null | string;
  last_login_at?: null | string;
  pending_verification_type?: null | string;
  pending_verification_at?: null | string;
  pending_verification_until?: null | string;
  remark?: null | string;
  sort?: number;
  sys_create_datetime?: string;
  sys_update_datetime?: string;
}

export interface DouyinAccountCreateInput {
  nickname: string;
  sec_uid?: null | string;
  avatar?: null | string;
  status?: number;
  auto_reply_enabled?: boolean;
  owner_id?: null | string;
  daily_reply_quota?: number;
  min_interval_seconds?: number;
  max_interval_seconds?: number;
  silent_start?: string;
  silent_end?: string;
  remark?: null | string;
  sort?: number;
}

export type DouyinAccountUpdateInput = Partial<DouyinAccountCreateInput>;

export interface DouyinAccountListParams {
  page?: number;
  pageSize?: number;
  nickname?: string;
  status?: number;
  owner_id?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page?: number;
  pageSize?: number;
}

export interface DouyinAccountActionResponse {
  success: boolean;
  message: string;
}

/**
 * 获取抖音账号列表（分页）
 */
export async function getDouyinAccountListApi(params?: DouyinAccountListParams) {
  return requestClient.get<PaginatedResponse<DouyinAccount>>(
    '/api/core/douyin/account',
    { params },
  );
}

/**
 * 获取所有抖音账号（简化版，用于下拉选择）
 */
export async function getSimpleDouyinAccountListApi() {
  return requestClient.get<
    {
      id: string;
      nickname: string;
      status: number;
      status_display: string;
    }[]
  >('/api/core/douyin/account/all');
}

/**
 * 获取抖音账号详情
 */
export async function getDouyinAccountDetailApi(accountId: string) {
  return requestClient.get<DouyinAccount>(
    `/api/core/douyin/account/${accountId}`,
  );
}

/**
 * 创建抖音账号
 */
export async function createDouyinAccountApi(data: DouyinAccountCreateInput) {
  return requestClient.post<DouyinAccount>('/api/core/douyin/account', data);
}

/**
 * 更新抖音账号（完全替换）
 */
export async function updateDouyinAccountApi(
  accountId: string,
  data: DouyinAccountCreateInput,
) {
  return requestClient.put<DouyinAccount>(
    `/api/core/douyin/account/${accountId}`,
    data,
  );
}

/**
 * 部分更新抖音账号
 */
export async function patchDouyinAccountApi(
  accountId: string,
  data: DouyinAccountUpdateInput,
) {
  return requestClient.patch<DouyinAccount>(
    `/api/core/douyin/account/${accountId}`,
    data,
  );
}

/**
 * 删除抖音账号
 */
export async function deleteDouyinAccountApi(accountId: string) {
  return requestClient.delete<DouyinAccount>(
    `/api/core/douyin/account/${accountId}`,
  );
}

/**
 * 批量删除抖音账号
 */
export async function batchDeleteDouyinAccountApi(ids: string[]) {
  return requestClient.post<{ count: number; failed_ids: string[] }>(
    '/api/core/douyin/account/batch/delete',
    { ids },
  );
}

/**
 * 触发登出
 */
export async function triggerDouyinLogoutApi(accountId: string) {
  return requestClient.post<DouyinAccountActionResponse>(
    `/api/core/douyin/account/${accountId}/logout`,
  );
}

export interface DouyinCredentialImportInput {
  /** 浏览器复制的 Cookie 整行（首次导入必填，须含 sessionid；补凭据时可留空复用已导入 Cookie） */
  cookie?: string;
  /** bd-ticket-guard 的 web_protect JSON（发送私信才需要） */
  web_protect?: string;
  /** 含 ec_privateKey 的 keys JSON（发送私信才需要） */
  keys?: string;
  /** 可选，覆盖账号昵称 */
  nickname?: string;
  /** 可选，与导出 Cookie 的浏览器一致的 UA */
  user_agent?: string;
}

/**
 * 导入登录态（粘贴 Cookie，替代扫码登录）
 */
export async function importDouyinCredentialApi(
  accountId: string,
  data: DouyinCredentialImportInput,
) {
  return requestClient.post<DouyinAccountActionResponse>(
    `/api/core/douyin/account/${accountId}/import-credential`,
    data,
  );
}
