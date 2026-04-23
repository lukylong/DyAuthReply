import { requestClient } from '#/api/request';

export interface DouyinBlacklist {
  id: string;
  blacklist_type: 'content_keyword' | 'nickname_keyword' | 'user';
  value: string;
  scope: 'account' | 'global' | 'group';
  account_id?: null | string;
  group_id?: null | string;
  reason?: null | string;
  hit_count: number;
  status: boolean;
  sys_create_datetime?: string;
}

export interface DouyinBlacklistInput {
  blacklist_type: 'content_keyword' | 'nickname_keyword' | 'user';
  value: string;
  scope?: 'account' | 'global' | 'group';
  account_id?: null | string;
  group_id?: null | string;
  reason?: null | string;
  status?: boolean;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getBlacklistList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<DouyinBlacklist>>('/api/core/douyin/blacklist', { params });
}

export async function createBlacklist(data: DouyinBlacklistInput) {
  return requestClient.post<DouyinBlacklist>('/api/core/douyin/blacklist', data);
}

export async function updateBlacklist(id: string, data: DouyinBlacklistInput) {
  return requestClient.put<DouyinBlacklist>(`/api/core/douyin/blacklist/${id}`, data);
}

export async function deleteBlacklist(id: string) {
  return requestClient.delete(`/api/core/douyin/blacklist/${id}`);
}

export async function batchDeleteBlacklist(ids: string[]) {
  return requestClient.post('/api/core/douyin/blacklist/batch/delete', { ids });
}
