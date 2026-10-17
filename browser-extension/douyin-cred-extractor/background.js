/* Per-tab, per-store and per-origin observations; never export stale cross-account data. */
importScripts('capture-core.js');
const CAPTURE_PREFIX='dy_capture_v2_';
async function storeObservation(details, kind, value) {
  if (details.tabId < 0 || !value) return;
  const tab=await chrome.tabs.get(details.tabId).catch(()=>null);if(!tab)return;
  const stores=await chrome.cookies.getAllCookieStores().catch(()=>[]);
  const store=tab.cookieStoreId || stores.find(s=>s.tabIds?.includes(tab.id))?.id;
  if (!store) return;
  const u=new URL(details.url);
  if (u.protocol!=='https:' || !['www.douyin.com','creator.douyin.com','imapi.douyin.com'].includes(u.hostname)) return;
  const key=`${CAPTURE_PREFIX}${store}_${details.tabId}_${u.hostname}_${kind}`;
  await chrome.storage.session.set({[key]:{value,capturedAt:Date.now(),sourceHost:u.hostname,tabId:details.tabId,storeId:store,path:u.pathname}});
}
chrome.webRequest.onBeforeRequest.addListener(details=>{
  const value=DyCapture.tokenFromUrl(details.url);
  if(value)void storeObservation(details,'token',value);
},{urls:['https://*.douyin.com/*']});
chrome.webRequest.onHeadersReceived.addListener(details=>{
  for(const h of details.responseHeaders || []){
    const name=String(h.name || '').toLowerCase();const value=String(h.value || '').trim();
    if (/[\r\n]/.test(value)) continue;
    if(name==='bd-ticket-guard-server-data' && value.length<=65536)void storeObservation(details,'ticket',value);
    if(name==='x-ms-token' && DyCapture.tokenOK(value))void storeObservation(details,'token',value);
  }
},{urls:['https://*.douyin.com/*']},['responseHeaders','extraHeaders']);
chrome.webRequest.onBeforeSendHeaders.addListener(details=>{
  const h=(details.requestHeaders || []).find(h=>String(h.name || '').toLowerCase()==='x-tt-session-dtrait');
  const value=String(h?.value || '');
  if(value && value.length<=65536 && !/[\r\n]/.test(value))void storeObservation(details,'dtrait',value);
},{urls:['https://*.douyin.com/*']},['requestHeaders','extraHeaders']);
chrome.tabs.onRemoved.addListener(async tabId=>{
  const values=await chrome.storage.session.get(null);
  const keys=Object.keys(values).filter(k=>k.startsWith(CAPTURE_PREFIX)&&values[k]?.tabId===tabId);
  if(keys.length)await chrome.storage.session.remove(keys);
});
