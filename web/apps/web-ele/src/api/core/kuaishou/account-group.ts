import { requestClient } from '#/api/request';

/** 快手账号分组 */
export interface KuaishouAccountGroup {
  id: string;
  name: string;
  color: string;
  owner_id?: null | string;
  default_daily_reply_quota: number;
  default_min_interval: number;
  default_max_interval: number;
  status: boolean;
  account_count?: number;
  remark?: null | string;
  sort?: number;
  sys_create_datetime?: string;
}

export interface KuaishouAccountGroupInput {
  name: string;
  color?: string;
  owner_id?: null | string;
  default_daily_reply_quota?: number;
  default_min_interval?: number;
  default_max_interval?: number;
  status?: boolean;
  remark?: null | string;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getKuaishouAccountGroupList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<KuaishouAccountGroup>>(
    '/api/core/kuaishou/account-group',
    { params },
  );
}

export async function getAllKuaishouAccountGroup() {
  return requestClient.get<KuaishouAccountGroup[]>(
    '/api/core/kuaishou/account-group/all',
  );
}

export async function createKuaishouAccountGroup(data: KuaishouAccountGroupInput) {
  return requestClient.post<KuaishouAccountGroup>(
    '/api/core/kuaishou/account-group',
    data,
  );
}

export async function updateKuaishouAccountGroup(
  id: string,
  data: KuaishouAccountGroupInput,
) {
  return requestClient.put<KuaishouAccountGroup>(
    `/api/core/kuaishou/account-group/${id}`,
    data,
  );
}

export async function deleteKuaishouAccountGroup(id: string) {
  return requestClient.delete(`/api/core/kuaishou/account-group/${id}`);
}

export async function batchDeleteKuaishouAccountGroup(ids: string[]) {
  return requestClient.post('/api/core/kuaishou/account-group/batch/delete', { ids });
}
