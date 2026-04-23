import { requestClient } from '#/api/request';

/** 抖音账号分组 */
export interface DouyinAccountGroup {
  id: string;
  name: string;
  color: string;
  owner_id?: null | string;
  owner_name?: null | string;
  default_daily_reply_quota: number;
  default_min_interval: number;
  default_max_interval: number;
  status: boolean;
  account_count?: number;
  remark?: null | string;
  sort?: number;
  sys_create_datetime?: string;
}

export interface DouyinAccountGroupInput {
  name: string;
  color?: string;
  owner_id?: null | string;
  default_daily_reply_quota?: number;
  default_min_interval?: number;
  default_max_interval?: number;
  status?: boolean;
  remark?: null | string;
}

export interface DouyinAccountGroupListParams {
  page?: number;
  page_size?: number;
  name?: string;
  status?: boolean;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getDouyinAccountGroupList(params: DouyinAccountGroupListParams = {}) {
  return requestClient.get<PaginatedResponse<DouyinAccountGroup>>(
    '/api/core/douyin/account-group',
    { params },
  );
}

export async function getAllDouyinAccountGroup() {
  return requestClient.get<DouyinAccountGroup[]>('/api/core/douyin/account-group/all');
}

export async function getDouyinAccountGroup(id: string) {
  return requestClient.get<DouyinAccountGroup>(`/api/core/douyin/account-group/${id}`);
}

export async function createDouyinAccountGroup(data: DouyinAccountGroupInput) {
  return requestClient.post<DouyinAccountGroup>('/api/core/douyin/account-group', data);
}

export async function updateDouyinAccountGroup(id: string, data: DouyinAccountGroupInput) {
  return requestClient.put<DouyinAccountGroup>(`/api/core/douyin/account-group/${id}`, data);
}

export async function deleteDouyinAccountGroup(id: string) {
  return requestClient.delete(`/api/core/douyin/account-group/${id}`);
}

export async function batchDeleteDouyinAccountGroup(ids: string[]) {
  return requestClient.post('/api/core/douyin/account-group/batch/delete', { ids });
}
