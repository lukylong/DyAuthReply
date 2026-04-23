import { requestClient } from '#/api/request';

export interface DouyinEvent {
  id: string;
  account_id?: null | string;
  account_nickname?: null | string;
  session?: null | string;
  event_type: string;
  level: 'critical' | 'error' | 'info' | 'warning';
  title: string;
  detail: string;
  payload: Record<string, unknown>;
  worker_id?: null | string;
  occurred_at: string;
  is_read: boolean;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getEventList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<DouyinEvent>>('/api/core/douyin/event', { params });
}

export async function getEventUnreadCount() {
  return requestClient.get<{ count: number }>('/api/core/douyin/event/unread-count');
}

export async function batchReadEvent(ids: string[]) {
  return requestClient.post('/api/core/douyin/event/batch/read', { ids });
}

export async function readAllEvent() {
  return requestClient.post('/api/core/douyin/event/read-all');
}
