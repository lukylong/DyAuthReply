import type { PaginatedResponse } from './license';

import { requestClient } from '#/api/request';

/** 客户端运行诊断（默认开启，可在客户端设置中关闭；数据已脱敏）。 */
export interface DiagnosticAccount {
  account: string;
  label?: string;
  lifecycle?: string;
  inbound?: string;
  send?: string;
  ownership?: string;
  identity_verified?: boolean;
}

export interface DiagnosticDevice {
  device_id: string;
  device_name: null | string;
  device_fingerprint: string;
  app_version: string;
  os: string;
  arch: string;
  last_report_at: null | string;
  report_count: number;
  warn_24h: number;
  error_24h: number;
  runtime_phase: null | string;
  accounts: DiagnosticAccount[];
  runtime?: Record<string, unknown>;
  storage_pressure?: null | string;
  protocol_parity?: boolean | null;
}

export interface DiagnosticDeviceDetail extends DiagnosticDevice {
  snapshot: Record<string, unknown>;
}

export interface DiagnosticEvent {
  id: number;
  occurred_at: string;
  received_at: string;
  level: 'ERROR' | 'INFO' | 'WARN';
  target: string;
  message: string;
  fields: Record<string, unknown>;
  app_version: string;
}

export function getDiagnosticDevicesApi(params: {
  app_version?: string;
  os?: string;
  page?: number;
  pageSize?: number;
}) {
  return requestClient.get<PaginatedResponse<DiagnosticDevice>>(
    '/api/core/license/diagnostics/device',
    { params },
  );
}

export function getDiagnosticDeviceApi(deviceId: string) {
  return requestClient.get<DiagnosticDeviceDetail>(
    `/api/core/license/diagnostics/device/${deviceId}`,
  );
}

export function getDiagnosticEventsApi(
  deviceId: string,
  params: { level?: string; page?: number; pageSize?: number },
) {
  return requestClient.get<PaginatedResponse<DiagnosticEvent>>(
    `/api/core/license/diagnostics/device/${deviceId}/events`,
    { params },
  );
}
