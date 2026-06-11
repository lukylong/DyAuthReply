import { requestClient } from '#/api/request';

export interface KuaishouEvent {
  id: string;
  account_id?: null | string;
  event_type: string;
  event_type_display?: string;
  level: 'critical' | 'error' | 'info' | 'warning';
  level_display?: string;
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

export async function getKuaishouEventList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<KuaishouEvent>>(
    '/api/core/kuaishou/event',
    { params },
  );
}

export async function markReadKuaishouEvent(ids: string[]) {
  return requestClient.post('/api/core/kuaishou/event/mark-read', { ids });
}
