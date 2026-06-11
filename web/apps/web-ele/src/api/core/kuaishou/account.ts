import { requestClient } from '#/api/request';

/** 快手账号 */
export interface KuaishouAccount {
  id: string;
  nickname: string;
  ks_user_id?: null | string;
  avatar?: null | string;
  status: number;
  status_display?: string;
  owner_id?: null | string;
  group_id?: null | string;
  group_name?: null | string;
  tags: string[];
  work_mode: 'auto' | 'hybrid' | 'manual';
  work_mode_display?: string;
  priority: number;
  daily_reply_quota: number;
  auto_reply_enabled: boolean;
  min_interval_seconds: number;
  max_interval_seconds: number;
  proxy_url?: null | string;
  user_agent?: null | string;
  reply_today: number;
  last_heartbeat?: null | string;
  last_login_at?: null | string;
  remark?: null | string;
  sort?: number;
  sys_create_datetime?: string;
}

export interface KuaishouAccountInput {
  nickname: string;
  avatar?: null | string;
  group_id?: null | string;
  tags?: string[];
  work_mode?: string;
  priority?: number;
  daily_reply_quota?: number;
  auto_reply_enabled?: boolean;
  min_interval_seconds?: number;
  max_interval_seconds?: number;
  silent_start?: string;
  silent_end?: string;
  proxy_url?: null | string;
  user_agent?: null | string;
  remark?: null | string;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getKuaishouAccountList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<KuaishouAccount>>(
    '/api/core/kuaishou/account',
    { params },
  );
}

export async function getAllKuaishouAccount() {
  return requestClient.get<KuaishouAccount[]>('/api/core/kuaishou/account/all');
}

export async function getKuaishouAccount(id: string) {
  return requestClient.get<KuaishouAccount>(`/api/core/kuaishou/account/${id}`);
}

export async function createKuaishouAccount(data: KuaishouAccountInput) {
  return requestClient.post<KuaishouAccount>('/api/core/kuaishou/account', data);
}

export async function updateKuaishouAccount(id: string, data: KuaishouAccountInput) {
  return requestClient.put<KuaishouAccount>(`/api/core/kuaishou/account/${id}`, data);
}

export async function patchKuaishouAccount(
  id: string,
  data: Partial<KuaishouAccountInput>,
) {
  return requestClient.patch<KuaishouAccount>(`/api/core/kuaishou/account/${id}`, data);
}

export async function deleteKuaishouAccount(id: string) {
  return requestClient.delete(`/api/core/kuaishou/account/${id}`);
}

export async function batchDeleteKuaishouAccount(ids: string[]) {
  return requestClient.post('/api/core/kuaishou/account/batch/delete', { ids });
}
