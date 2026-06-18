import { computed, readonly, ref } from 'vue';

import {
  activateLicense,
  deactivateLicense,
  getLicenseStatus,
  refreshLicenseStatus,
  type ClientLicenseStatus,
} from '../api/client';

const licenseStatus = ref<ClientLicenseStatus | null>(null);
const loading = ref(false);
const loaded = ref(false);

let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollStarted = false;

function clearPollTimer() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function msUntilNextCheck(status: ClientLicenseStatus | null): number {
  const next = status?.next_check_in_at ? new Date(status.next_check_in_at).getTime() : 0;
  const now = Date.now();
  if (!next || Number.isNaN(next)) return 60_000;
  return Math.max(15_000, Math.min(next - now, 5 * 60_000));
}

async function loadStatus(force = false) {
  loading.value = true;
  try {
    licenseStatus.value = force ? await refreshLicenseStatus() : await getLicenseStatus();
    loaded.value = true;
    return licenseStatus.value;
  } finally {
    loading.value = false;
  }
}

async function ensureStatus() {
  if (loaded.value && licenseStatus.value) return licenseStatus.value;
  return loadStatus(false);
}

function scheduleNextPoll() {
  clearPollTimer();
  pollTimer = setTimeout(async () => {
    try {
      await loadStatus(false);
    } finally {
      scheduleNextPoll();
    }
  }, msUntilNextCheck(licenseStatus.value));
}

function startPolling() {
  if (pollStarted) return;
  pollStarted = true;
  void ensureStatus().finally(() => {
    scheduleNextPoll();
  });
}

async function activate(data: { license_code: string }) {
  const next = await activateLicense(data);
  licenseStatus.value = next;
  loaded.value = true;
  scheduleNextPoll();
  return next;
}

async function refreshNow() {
  const next = await refreshLicenseStatus();
  licenseStatus.value = next;
  loaded.value = true;
  scheduleNextPoll();
  return next;
}

async function deactivate(reason = '客户端主动解绑') {
  const next = await deactivateLicense(reason);
  licenseStatus.value = next;
  loaded.value = true;
  scheduleNextPoll();
  return next;
}

export function useClientLicense() {
  return {
    licenseStatus: readonly(licenseStatus),
    loading: readonly(loading),
    loaded: readonly(loaded),
    businessEnabled: computed(() => Boolean(licenseStatus.value?.can_use_business)),
    ensureStatus,
    loadStatus,
    startPolling,
    activate,
    refreshNow,
    deactivate,
  };
}
