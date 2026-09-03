/**
 * 捕获登录响应里的 bd-ticket-guard-server-data。
 *
 * 该字段并不保证写入 Cookie；MV3 service worker 因此从响应头读取，并按普通/无痕
 * Cookie store 隔离保存到 storage.session。只保留浏览器会话内最后一次值。
 */
const SERVER_DATA_HEADER = 'bd-ticket-guard-server-data';
const MAX_SERVER_DATA_LENGTH = 64 * 1024;
const DTRAIT_HEADER = 'x-tt-session-dtrait';
const MAX_DTRAIT_LENGTH = 16 * 1024;

function storageKey(storeId) {
  return `ticket_guard_server_data_${storeId || '0'}`;
}

function dtraitStorageKey(storeId) {
  return `session_dtrait_${storeId || '0'}`;
}

async function resolveStoreId(tab, tabId) {
  if (tab.cookieStoreId) return tab.cookieStoreId;
  const stores = await chrome.cookies.getAllCookieStores().catch(() => []);
  const owningStore = stores.find((store) => store.tabIds?.includes(tabId));
  if (owningStore) return owningStore.id;
  if (tab.incognito) return stores.find((store) => store.id !== '0')?.id || 'incognito';
  return stores.find((store) => store.id === '0')?.id || '0';
}

async function captureServerData(details, value) {
  const tab = await chrome.tabs.get(details.tabId).catch(() => null);
  if (!tab) return;
  const storeId = await resolveStoreId(tab, details.tabId);
  await chrome.storage.session.set({
    [storageKey(storeId)]: {
      value,
      capturedAt: Date.now(),
      sourceHost: (() => {
        try { return new URL(details.url).hostname; } catch { return ''; }
      })(),
    },
  });
}

async function captureSessionDtrait(details, value) {
  const tab = await chrome.tabs.get(details.tabId).catch(() => null);
  if (!tab) return;
  const storeId = await resolveStoreId(tab, details.tabId);
  let path = '';
  try { path = new URL(details.url).pathname; } catch { /* ignore */ }
  await chrome.storage.session.set({
    [dtraitStorageKey(storeId)]: {
      value,
      path,
      capturedAt: Date.now(),
    },
  });
}

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    const header = (details.responseHeaders || []).find(
      (item) => String(item.name || '').toLowerCase() === SERVER_DATA_HEADER,
    );
    const value = String(header?.value || '').trim();
    if (!value || value.length > MAX_SERVER_DATA_LENGTH || details.tabId < 0) return;

    void captureServerData(details, value);
  },
  { urls: ['*://*.douyin.com/*'] },
  ['responseHeaders', 'extraHeaders'],
);

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    const header = (details.requestHeaders || []).find(
      (item) => String(item.name || '').toLowerCase() === DTRAIT_HEADER,
    );
    const value = String(header?.value || '').trim();
    if (!value || value.length > MAX_DTRAIT_LENGTH || details.tabId < 0) return;
    void captureSessionDtrait(details, value);
  },
  { urls: ['*://*.douyin.com/*'] },
  ['requestHeaders', 'extraHeaders'],
);
