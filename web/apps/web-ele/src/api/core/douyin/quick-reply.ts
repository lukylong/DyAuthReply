import { requestClient } from '#/api/request';

export interface DouyinQuickReply {
  id: string;
  shortcut: string;
  title: string;
  content: string;
  category_id?: null | string;
  is_shared: boolean;
  owner_id?: null | string;
  owner_name?: null | string;
  use_count: number;
  status: boolean;
  sort?: number;
}

export interface DouyinQuickReplyInput {
  shortcut: string;
  title: string;
  content: string;
  category_id?: null | string;
  is_shared?: boolean;
  status?: boolean;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getQuickReplyList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<DouyinQuickReply>>('/api/core/douyin/quick-reply', { params });
}

export async function getAllQuickReply() {
  return requestClient.get<DouyinQuickReply[]>('/api/core/douyin/quick-reply/all');
}

export async function createQuickReply(data: DouyinQuickReplyInput) {
  return requestClient.post<DouyinQuickReply>('/api/core/douyin/quick-reply', data);
}

export async function updateQuickReply(id: string, data: DouyinQuickReplyInput) {
  return requestClient.put<DouyinQuickReply>(`/api/core/douyin/quick-reply/${id}`, data);
}

export async function deleteQuickReply(id: string) {
  return requestClient.delete(`/api/core/douyin/quick-reply/${id}`);
}

export async function batchDeleteQuickReply(ids: string[]) {
  return requestClient.post('/api/core/douyin/quick-reply/batch/delete', { ids });
}
