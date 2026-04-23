import { requestClient } from '#/api/request';

export interface DouyinSession {
  id: string;
  account_id: string;
  account_nickname?: null | string;
  worker_id: string;
  context_id: string;
  status: 'error' | 'idle' | 'paused' | 'running' | 'stopped';
  started_at?: null | string;
  last_heartbeat?: null | string;
  last_message_at?: null | string;
  messages_today: number;
  replies_today: number;
  errors_today: number;
  cpu_percent: number;
  memory_mb: number;
  proxy_url?: null | string;
  error_message?: null | string;
  is_alive: boolean;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getSessionList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<DouyinSession>>('/api/core/douyin/session', { params });
}

export async function getLiveSessions() {
  return requestClient.get<DouyinSession[]>('/api/core/douyin/session/live');
}

export async function getSession(id: string) {
  return requestClient.get<DouyinSession>(`/api/core/douyin/session/${id}`);
}

export async function controlSession(
  id: string,
  action: 'pause' | 'restart' | 'resume' | 'stop',
) {
  return requestClient.post<{ message: string; success: boolean }>(
    `/api/core/douyin/session/${id}/control`,
    { action },
  );
}

export async function batchStopSession(ids: string[]) {
  return requestClient.post('/api/core/douyin/session/batch/stop', { ids });
}
