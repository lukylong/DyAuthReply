import { requestClient } from '#/api/request';

export interface KuaishouDashboardOverview {
  total_accounts: number;
  online_accounts: number;
  total_sessions: number;
  running_sessions: number;
  messages_today: number;
  replies_today: number;
  success_today: number;
  failed_today: number;
  unread_events: number;
}

export async function getKuaishouDashboardOverview() {
  return requestClient.get<KuaishouDashboardOverview>(
    '/api/core/kuaishou/dashboard/overview',
  );
}
