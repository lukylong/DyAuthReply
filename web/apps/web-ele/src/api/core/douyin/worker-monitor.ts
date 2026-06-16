import { requestClient } from '#/api/request';

export namespace DouyinWorkerMonitorApi {
  export interface AccountBrief {
    account_id: string;
    nickname: string;
    status: number;
    shard_index: number;
  }

  export interface LeaseItem {
    account_id: string;
    nickname: string;
    holder_worker_id: string;
    ttl_seconds: number;
    session_worker_id?: null | string;
    consistent: boolean;
  }

  export interface ProcessItem {
    worker_id: string;
    hostname: string;
    inferred_shard_index?: null | number;
    account_count: number;
    accounts: AccountBrief[];
    status: 'alive' | 'dead' | 'stale';
    last_heartbeat?: null | string;
    cpu_percent: number;
    memory_mb: number;
    lease_count: number;
  }

  export interface ShardItem {
    shard_index: number;
    expected_account_count: number;
    active_account_count: number;
    accounts: AccountBrief[];
    worker_ids: string[];
    status: 'idle' | 'missing_worker' | 'ok' | 'partial';
  }

  export interface IssueItem {
    level: 'error' | 'info' | 'warning';
    code: string;
    title: string;
    detail: string;
  }

  export interface Overview {
    timestamp: string;
    shard_count: number;
    shard_index_hint: string;
    lease_enabled: boolean;
    lease_ttl: number;
    redis_ok: boolean;
    managed_account_total: number;
    active_session_total: number;
    lease_total: number;
    worker_process_total: number;
    shards: ShardItem[];
    workers: ProcessItem[];
    leases: LeaseItem[];
    issues: IssueItem[];
  }

  export function getOverview() {
    return requestClient.get<Overview>('/api/core/douyin/worker-monitor/overview');
  }
}
