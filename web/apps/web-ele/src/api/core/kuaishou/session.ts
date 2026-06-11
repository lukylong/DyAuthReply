import { requestClient } from '#/api/request';

export interface KuaishouSession {
  id: string;
  account_id: string;
  account_nickname?: null | string;
  worker_id: string;
  context_id: string;
  status: 'error' | 'idle' | 'paused' | 'running' | 'stopped';
  status_display?: string;
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
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getKuaishouSessionList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<KuaishouSession>>(
    '/api/core/kuaishou/session',
    { params },
  );
}

export async function getKuaishouSession(id: string) {
  return requestClient.get<KuaishouSession>(`/api/core/kuaishou/session/${id}`);
}

export async function controlKuaishouSession(
  id: string,
  action: 'pause' | 'restart' | 'resume' | 'stop',
) {
  return requestClient.post<{ action: string; status: string; success: boolean }>(
    `/api/core/kuaishou/session/${id}/control`,
    { action },
  );
}
