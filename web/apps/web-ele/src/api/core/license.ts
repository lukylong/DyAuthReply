import { requestClient } from '#/api/request';

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page?: number;
  pageSize?: number;
}

export interface LicensePlan {
  id: string;
  code: string;
  name: string;
  description?: string | null;
  feature_flags?: Record<string, boolean>;
  max_devices: number;
  valid_days: number;
  heartbeat_interval_minutes: number;
  grace_period_minutes: number;
  is_active: boolean;
  sys_create_datetime?: string;
  sys_update_datetime?: string;
}

export interface LicenseKey {
  id: string;
  plan_id: string;
  plan_name?: string | null;
  code_hash: string;
  masked_code: string;
  status: string;
  issued_to?: string | null;
  batch_no?: string | null;
  expires_at?: string | null;
  activated_at?: string | null;
  last_check_in_at?: string | null;
  max_devices_override?: number | null;
  valid_days_override?: number | null;
  effective_max_devices?: number;
  effective_valid_days?: number;
  notes?: string | null;
  sys_create_datetime?: string;
  sys_update_datetime?: string;
}

export interface ClientDevice {
  id: string;
  device_fingerprint: string;
  device_name?: string | null;
  os_type?: string | null;
  os_version?: string | null;
  app_version?: string | null;
  machine_meta?: Record<string, unknown>;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  status: string;
}

export interface LicenseActivation {
  id: string;
  license_key_id?: string | null;
  client_device_id?: string | null;
  masked_code?: string | null;
  device_fingerprint?: string | null;
  status: string;
  activated_at: string;
  expires_at?: string | null;
  last_heartbeat_at?: string | null;
  last_valid_until?: string | null;
  revoked_at?: string | null;
  revoked_reason?: string | null;
}

export interface LicenseEvent {
  id: string;
  license_key_id?: string | null;
  client_device_id?: string | null;
  activation_id?: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  ip?: string | null;
  sys_create_datetime?: string;
}

export interface LicenseStats {
  plans_total: number;
  keys_total: number;
  keys_active: number;
  keys_pending: number;
  keys_revoked: number;
  activations_active: number;
  devices_total: number;
}

export interface LicensePlanInput {
  code: string;
  name: string;
  description?: string | null;
  feature_flags?: Record<string, boolean>;
  max_devices?: number;
  valid_days?: number;
  heartbeat_interval_minutes?: number;
  grace_period_minutes?: number;
  is_active?: boolean;
}

export interface LicenseKeyGenerateInput {
  plan_id: string;
  count?: number;
  issued_to?: string | null;
  batch_no?: string | null;
  expires_at?: string | null;
  max_devices_override?: number | null;
  valid_days_override?: number | null;
  notes?: string | null;
}

export interface GeneratedLicenseKey {
  id: string;
  code: string;
  masked_code: string;
  plan_id: string;
  expires_at?: string | null;
  max_devices: number;
}

export interface LicenseKeyGenerateResult {
  count: number;
  items: GeneratedLicenseKey[];
}

export interface LicensePlanListParams {
  page?: number;
  pageSize?: number;
  code?: string;
  name?: string;
  is_active?: boolean;
}

export interface LicenseKeyListParams {
  page?: number;
  pageSize?: number;
  plan_id?: string;
  status?: string;
  batch_no?: string;
  masked_code?: string;
}

export interface ClientDeviceListParams {
  page?: number;
  pageSize?: number;
  device_fingerprint?: string;
  os_type?: string;
  status?: string;
}

export interface LicenseActivationListParams {
  page?: number;
  pageSize?: number;
  license_key_id?: string;
  client_device_id?: string;
  status?: string;
}

export function getLicensePlanListApi(params?: LicensePlanListParams) {
  return requestClient.get<PaginatedResponse<LicensePlan>>('/api/core/license/plan', { params });
}

export function createLicensePlanApi(data: LicensePlanInput) {
  return requestClient.post<LicensePlan>('/api/core/license/plan', data);
}

export function updateLicensePlanApi(planId: string, data: Partial<LicensePlanInput>) {
  return requestClient.patch<LicensePlan>(`/api/core/license/plan/${planId}`, data);
}

export function deleteLicensePlanApi(planId: string) {
  return requestClient.delete<{ deleted_keys: number; revoked_activations: number; success: boolean }>(
    `/api/core/license/plan/${planId}`,
  );
}

export function getLicenseKeyListApi(params?: LicenseKeyListParams) {
  return requestClient.get<PaginatedResponse<LicenseKey>>('/api/core/license/key', { params });
}

export function generateLicenseKeysApi(data: LicenseKeyGenerateInput) {
  return requestClient.post<LicenseKeyGenerateResult>('/api/core/license/key/generate', data);
}

export function revokeLicenseKeyApi(licenseKeyId: string, reason: string) {
  return requestClient.post<LicenseKey>(`/api/core/license/key/${licenseKeyId}/revoke`, { reason });
}

export function deleteLicenseKeyApi(licenseKeyId: string) {
  return requestClient.delete<{ success: boolean }>(`/api/core/license/key/${licenseKeyId}`);
}

export function getClientDeviceListApi(params?: ClientDeviceListParams) {
  return requestClient.get<PaginatedResponse<ClientDevice>>('/api/core/license/device', { params });
}

export function getLicenseActivationListApi(params?: LicenseActivationListParams) {
  return requestClient.get<PaginatedResponse<LicenseActivation>>('/api/core/license/activation', { params });
}

export function unbindLicenseDeviceApi(licenseKeyId: string, clientDeviceId: string, reason: string) {
  return requestClient.post<LicenseActivation>(`/api/core/license/key/${licenseKeyId}/unbind-device`, {
    client_device_id: clientDeviceId,
    reason,
  });
}

export function getLicenseStatsApi() {
  return requestClient.get<LicenseStats>('/api/core/license/stats');
}

export function getLicenseEventListApi(params?: { page?: number; pageSize?: number }) {
  return requestClient.get<PaginatedResponse<LicenseEvent>>('/api/core/license/event', { params });
}
