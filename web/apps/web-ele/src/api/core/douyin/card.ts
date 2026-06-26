import { requestClient } from '#/api/request';

/** 抖音伪装卡片 */
export interface DouyinCard {
  id: string;
  title: string;
  description: string;
  cover_file_id?: null | string;
  cover_url?: null | string;
  target_url: string;
  remark?: null | string;
  status: boolean;
  is_shared: boolean;
  owner_id?: null | string;
  owner_name?: null | string;
  landing_url?: null | string;
  sort?: number;
  sys_create_datetime?: string;
}

export interface DouyinCardInput {
  title: string;
  description?: string;
  cover_file_id?: null | string;
  target_url: string;
  remark?: null | string;
  status?: boolean;
  is_shared?: boolean;
}

export interface DouyinCardSimple {
  id: string;
  title: string;
  cover_url?: null | string;
  target_url?: null | string;
}

export interface DouyinCardListParams {
  page?: number;
  page_size?: number;
  title?: string;
  remark?: string;
  status?: boolean;
}

interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getCardList(params: DouyinCardListParams = {}) {
  return requestClient.get<PaginatedResponse<DouyinCard>>('/api/core/douyin/card', { params });
}

export async function getAllCard() {
  return requestClient.get<DouyinCardSimple[]>('/api/core/douyin/card/all');
}

export async function getCard(id: string) {
  return requestClient.get<DouyinCard>(`/api/core/douyin/card/${id}`);
}

export async function createCard(data: DouyinCardInput) {
  return requestClient.post<DouyinCard>('/api/core/douyin/card', data);
}

export async function updateCard(id: string, data: DouyinCardInput) {
  return requestClient.put<DouyinCard>(`/api/core/douyin/card/${id}`, data);
}

export async function patchCard(id: string, data: Partial<DouyinCardInput>) {
  return requestClient.patch<DouyinCard>(`/api/core/douyin/card/${id}`, data);
}

export async function deleteCard(id: string) {
  return requestClient.delete(`/api/core/douyin/card/${id}`);
}

export async function batchDeleteCard(ids: string[]) {
  return requestClient.post<{ count: number }>('/api/core/douyin/card/batch/delete', { ids });
}
