/**
 * 抖音登录态提取器 —— popup 逻辑 v2.2.0
 *
 * 抓取登录态（与 DyAuthReply 后台「导入登录态」对话框对应）：
 *   cookie       —— 用 chrome.cookies API 读取，能拿到 document.cookie 取不到的 HttpOnly sessionid
 *   server_data  —— 登录响应写入的 bd_ticket_guard_server_data Cookie
 *   web_protect  —— 旧版 localStorage 字段（兼容保留）
 *   keys         —— 页面 localStorage['security-sdk/s_sdk_crypt_sdk']
 *
 * v2.0.0 重大修复：账号身份信息（昵称/头像/sec_uid）改为通过 creator API 获取，
 * 不再依赖页面 DOM/JS 状态遍历（后者在私信页会抓到聊天列表用户的信息）。
 */

const LS_KEYS = 'security-sdk/s_sdk_crypt_sdk';
const LS_WEB_PROTECT = 'security-sdk/s_sdk_sign_data_key/web_protect';
const SERVER_DATA_STORAGE_PREFIX = 'ticket_guard_server_data_';
const SESSION_DTRAIT_STORAGE_PREFIX = 'session_dtrait_';

const $ = (id) => document.getElementById(id);

async function boundedRead(read, message, timeoutMs = 8000) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(read),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(message)),timeoutMs);}),
    ]);
  } catch { throw new Error(message); }
  finally { clearTimeout(timer); }
}

async function executeBounded(details, timeoutMs = 8000) {
  return boundedRead(()=>chrome.scripting.executeScript({...details,injectImmediately:true}),
    '页面读取失败或超时，请完成页面加载或验证后重试', timeoutMs);
}

function showToast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => (t.hidden = true), 1600);
}

function setBadge(el, state, text) {
  el.className = `badge ${state}`;
  el.textContent = text;
}

/** 从 cookie 串读取单个字段 */
function extractCookieField(cookie, name) {
  const m = new RegExp(`(?:^|;\\s*)${escapeRegExp(name)}=([^;]+)`).exec(cookie || '');
  if (!m) return '';
  const raw = m[1].trim();
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 同名字段多条时，优先 path 更长、与当前页 host 更匹配的那条 */
function pickBetterCookie(a, b, tabUrl) {
  if (!a) return b;
  if (!b) return a;
  const pathA = a.path || '/';
  const pathB = b.path || '/';
  if (pathB.length !== pathA.length) return pathB.length > pathA.length ? b : a;
  try {
    const host = new URL(tabUrl).hostname;
    const hostMatch = (c) => {
      const d = (c.domain || '').replace(/^\./, '');
      return host === d || host.endsWith(`.${d}`);
    };
    if (hostMatch(b) && !hostMatch(a)) return b;
    if (hostMatch(a) && !hostMatch(b)) return a;
  } catch {
    /* ignore */
  }
  return b;
}

/** 解析 tab 所属 cookie 存储（query 结果在部分 Chrome/Edge 版本不带 cookieStoreId） */
async function resolveCookieStoreId(tab) {
  if (tab.cookieStoreId) return tab.cookieStoreId;

  if (tab.id != null) {
    const full = await boundedRead(() => chrome.tabs.get(tab.id), '读取当前标签资料失败或超时，请重新打开扩展');
    if (full?.cookieStoreId) return full.cookieStoreId;
  }

  const stores = await boundedRead(() => chrome.cookies.getAllCookieStores(), '读取 Cookie 存储失败或超时，请重新打开扩展');
  if (tab.id != null) {
    for (const store of stores) {
      if (store.tabIds?.includes(tab.id)) return store.id;
    }
  }

  // A default or single store is not proof that it owns this tab.
  return '';
}

/** 只读指定 cookie 存储（普通 / 无痕隔离），禁止跨 store 合并。 */
async function collectCookies(tabUrl, cookieStoreId) {
  const storeId = cookieStoreId || '';
  if (!storeId) {
    throw new Error(
      '无法识别当前窗口的 cookie 存储。请确认扩展已启用 cookies 权限，并在 chrome://extensions 重新加载后再试。',
    );
  }
  const [cookies, domainCookies] = await Promise.all([
    chrome.cookies.getAll({ url: tabUrl, storeId }).catch(() => []),
    chrome.cookies.getAll({ domain: 'douyin.com', storeId }).catch(() => []),
  ]);
  const byName = new Map();
  for (const c of cookies) {
    if (!c || !c.name) continue;
    byName.set(c.name, pickBetterCookie(byName.get(c.name), c, tabUrl));
  }
  // server_data 可能只写在 passport/login 子域，不会出现在当前页 URL 查询结果。
  // 只跨子域补这一个精确字段，避免把其他账号 Cookie 意外混入。
  for (const c of domainCookies) {
    if (c?.name !== 'bd_ticket_guard_server_data' || !c.value) continue;
    byName.set(c.name, pickBetterCookie(byName.get(c.name), c, tabUrl));
  }
  return [...byName.values()].map((c) => `${c.name}=${c.value}`).join('; ');
}

async function cookieHeaderForUrl(url, cookieStoreId) {
  const cookies = await boundedRead(()=>chrome.cookies.getAll({url,storeId:cookieStoreId}),
    '读取浏览器 Cookie 失败或超时，请检查扩展权限后重试');
  if(!Array.isArray(cookies)||cookies.length>512)throw new Error('浏览器 Cookie 响应无效或数量超限');
  return cookies.filter(cookie=>{
    if(typeof cookie?.name!=='string'||typeof cookie?.value!=='string')throw new Error('浏览器 Cookie 字段类型无效');
    if(!cookie.name)return false;
    if(cookie.name.length>256||cookie.value.length>32768||/[\u0000-\u001f\u007f-\u009f]/.test(cookie.name+cookie.value))throw new Error('浏览器 Cookie 字段格式无效');
    return true;
  }).map(cookie=>`${cookie.name}=${cookie.value}`).join('; ');
}

async function collectScopedCookieHeaders(cookieStoreId) {
  const targets = {
    'creator.douyin.com': 'https://creator.douyin.com/creator-micro/data/following/chat',
    'imapi.douyin.com': 'https://imapi.douyin.com/',
    'www.douyin.com': 'https://www.douyin.com/',
  };
  const pairs = await Promise.all(
    Object.entries(targets).map(async ([host, url]) => [
      host,
      await cookieHeaderForUrl(url, cookieStoreId),
    ]),
  );
  return Object.fromEntries(pairs.filter(([, value]) => value));
}

function decodeTicketGuardServerData(raw, source = '') {
  if (!raw) return { raw: '', data: {}, source };
  try {
    const normalized = raw.replaceAll('-', '+').replaceAll('_', '/');
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
    const data = JSON.parse(decodeURIComponent(escape(atob(padded))));
    return { raw, data: data && typeof data === 'object' ? data : {}, source };
  } catch {
    return { raw, data: {}, source };
  }
}

function ticketGuardServerDataFromCookie(cookie) {
  return decodeTicketGuardServerData(
    extractCookieField(cookie, 'bd_ticket_guard_server_data'),
    'cookie',
  );
}

async function loadCapturedServerData(cookieStoreId) {
  const key = `${SERVER_DATA_STORAGE_PREFIX}${cookieStoreId || '0'}`;
  const stored = await chrome.storage.session.get(key).catch(() => ({}));
  const record = stored[key] || {};
  return decodeTicketGuardServerData(String(record.value || ''), 'response_header');
}

async function loadCapturedSessionDtrait(cookieStoreId) {
  const key = `${SESSION_DTRAIT_STORAGE_PREFIX}${cookieStoreId || '0'}`;
  const stored = await chrome.storage.session.get(key).catch(() => ({}));
  const record = stored[key] || {};
  return {
    value: String(record.value || ''),
    path: String(record.path || ''),
    capturedAt: Number(record.capturedAt || 0),
  };
}

function chooseNewestServerData(cookieData, headerData) {
  const valid = (item) => Boolean(
    item?.data?.ticket && item.data.ts_sign && item.data.client_cert,
  );
  if (!valid(headerData)) return cookieData;
  if (!valid(cookieData)) return headerData;
  const cookieTime = Number(cookieData.data.create_time || 0);
  const headerTime = Number(headerData.data.create_time || 0);
  return headerTime >= cookieTime ? headerData : cookieData;
}

/** 列出各 cookie 存储里的 sessionid，用于诊断「两号抓成一样」 */
async function diagnoseSessionIds(tabUrl, currentStoreId) {
  const stores = await chrome.cookies.getAllCookieStores().catch(() => []);
  const rows = [];
  for (const store of stores) {
    const cookies = await chrome.cookies.getAll({ url: tabUrl, storeId: store.id }).catch(() => []);
    const sid =
      cookies.find((c) => c.name === 'sessionid')?.value ||
      cookies.find((c) => c.name === 'sessionid_ss')?.value ||
      '';
    rows.push({
      storeId: store.id,
      isCurrent: store.id === currentStoreId,
      sessionPrefix: sid ? sid.slice(0, 16) : '',
      hasSession: Boolean(sid),
    });
  }
  const withSid = rows.filter((r) => r.hasSession);
  const prefixes = withSid.map((r) => r.sessionPrefix);
  const sameAcrossStores =
    withSid.length >= 2 && new Set(prefixes).size === 1;
  return { rows, sameAcrossStores };
}

/** sessionid 指纹：前缀 + 后缀，便于与后台重复拦截提示对照。 */
function accountFingerprint(cookie) {
  const m = /(?:^|;\s*)sessionid(?:_ss)?=([^;]+)/.exec(cookie || '');
  if (!m) return '';
  const v = m[1].trim();
  if (v.length <= 16) return v;
  return `${v.slice(0, 12)}…${v.slice(-6)}`;
}

/** 在页面上下文读取：两个 localStorage 键 + UA + best-effort 当前登录账号信息。 */
async function collectPageInfo(tabId) {
  const [{ result } = {}] = await executeBounded({
    target: { tabId },
    // MAIN 世界：可读取页面 window.__INITIAL_STATE__ 等全局状态（ISOLATED 世界读不到）；
    // localStorage / DOM 在两个世界都可读，故切到 MAIN 不影响 keys/web_protect 抓取。
    world: 'MAIN',
    func: (kKeys, kWp) => {
      const out = {
        keys: localStorage.getItem(kKeys) || '',
        web_protect: localStorage.getItem(kWp) || '',
        ua: navigator.userAgent || '',
        dtrait_blob:
          String(window.__dyauthreplyLatestDtraitBlob || '')
          || document.documentElement?.getAttribute('data-dyauthreply-dtrait-blob')
          || '',
        dtrait_path:
          String(window.__dyauthreplyLatestDtraitPath || '')
          || document.documentElement?.getAttribute('data-dyauthreply-dtrait-path')
          || '',
        account: {},
      };

      const acc = {};
      // 从一个「用户对象」（含 nickname）里抽取昵称/抖音号/sec_uid/uid/头像
      const fromUserObj = (o) => {
        if (!o || typeof o !== 'object') return;
        const nk = o.nickname || o.nick_name;
        if (!nk) return;
        if (!acc.nickname) acc.nickname = String(nk);
        const uniq =
          o.unique_id || o.uniqueId || o.unique_id_str || o.short_id || o.douyin_id || o.dyId;
        if (uniq && !acc.unique_id) acc.unique_id = String(uniq);
        const sec = o.sec_uid || o.sec_user_id || o.secUid;
        if (sec && !acc.sec_uid) acc.sec_uid = String(sec);
        const id = o.uid || o.user_id || o.userId || o.uid_str;
        if (id && !acc.uid) acc.uid = String(id);
        let av =
          o.avatar_url || o.avatar_thumb || o.avatar_larger || o.avatar_168x168 ||
          o.avatar_medium || o.avatar;
        if (av && typeof av === 'object') av = (av.url_list && av.url_list[0]) || av.uri || '';
        if (av && !acc.avatar) acc.avatar = String(av);
      };
      const enough = () => acc.nickname && acc.unique_id;
      const walk = (node, depth) => {
        if (enough() || !node || typeof node !== 'object' || depth > 8) return;
        if (Array.isArray(node)) {
          for (const x of node) {
            if (enough()) return;
            walk(x, depth + 1);
          }
          return;
        }
        fromUserObj(node);
        for (const v of Object.values(node)) {
          if (enough()) return;
          if (v && typeof v === 'object') walk(v, depth + 1);
        }
      };
      const tryParse = (txt) => {
        if (!txt) return null;
        try { return JSON.parse(txt); } catch { /* try decode */ }
        try { return JSON.parse(decodeURIComponent(txt)); } catch { return null; }
      };

      // ① 页面内嵌状态（最可靠）：RENDER_DATA / __INITIAL_STATE__ / _ROUTER_DATA / __NEXT_DATA__
      const globals = [
        window.__INITIAL_STATE__, window._ROUTER_DATA, window.__NEXT_DATA__,
        window.RENDER_DATA, window.__INIT_PROPS__,
      ];
      for (const g of globals) {
        if (enough()) break;
        try { walk(g, 0); } catch { /* ignore */ }
      }
      if (!enough()) {
        const scripts = document.querySelectorAll(
          'script#RENDER_DATA, script#__NEXT_DATA__, script[type="application/json"]',
        );
        for (const s of scripts) {
          if (enough()) break;
          const parsed = tryParse(s.textContent || '');
          if (parsed) { try { walk(parsed, 0); } catch { /* ignore */ } }
        }
      }

      // ② localStorage / sessionStorage 缓存的用户对象
      const scan = (store) => {
        for (let i = 0; i < store.length; i++) {
          if (enough()) return;
          const raw = store.getItem(store.key(i));
          if (!raw || (raw[0] !== '{' && raw[0] !== '[')) continue;
          const parsed = tryParse(raw);
          if (parsed) { try { walk(parsed, 0); } catch { /* ignore */ } }
        }
      };
      if (!enough()) { try { scan(localStorage); } catch { /* ignore */ } }
      if (!enough()) { try { scan(sessionStorage); } catch { /* ignore */ } }

      // ③ DOM 文本兜底：抖音号 / 昵称（页面可见时一定能取到）
      const bodyText = (document.body && document.body.innerText) || '';
      if (!acc.unique_id) {
        const m = /抖音号[:：]\s*([A-Za-z0-9._-]+)/.exec(bodyText);
        if (m) acc.unique_id = m[1];
      }
      if (!acc.nickname) {
        // 优先从个人中心页面获取（creator.douyin.com 顶部导航栏）
        const profileName = document.querySelector('.semi-navigation-header-title, .header-account-name, [class*="header"][class*="name"]');
        if (profileName && profileName.textContent && profileName.textContent.trim()) {
          acc.nickname = profileName.textContent.trim().slice(0, 64);
        } else {
          // 兜底：抖音号通常紧跟在昵称之后；取页面里 "抖音号" 之前最近的一段非空文本作为昵称
          const h1 = document.querySelector('h1, [data-e2e="user-title"], .user-title');
          if (h1 && h1.textContent && h1.textContent.trim()) {
            acc.nickname = h1.textContent.trim().slice(0, 64);
          }
        }
      }

      // ④ DOM 兜底头像（优先从导航栏获取，避免抓到聊天列表中的头像）
      if (!acc.avatar) {
        // 优先级 1: 导航栏头像（creator.douyin.com 右上角）
        const navAvatar = document.querySelector('.semi-navigation-header-avatar img, .header-account-avatar img, [class*="header-account"] img');
        if (navAvatar && navAvatar.src && navAvatar.src.includes('avatar')) {
          acc.avatar = navAvatar.src;
        } else {
          // 优先级 2: 个人中心头像（更大更清晰）
          const profileAvatar = document.querySelector('.account-avatar img, [class*="profile-avatar"] img, [data-e2e="user-avatar"] img');
          if (profileAvatar && profileAvatar.src) {
            acc.avatar = profileAvatar.src;
          } else {
            // 优先级 3: 任何包含 aweme-avatar 的图片（抖音专用类名）
            const awemeAvatar = document.querySelector('img[src*="aweme-avatar"]');
            if (awemeAvatar && awemeAvatar.src) {
              acc.avatar = awemeAvatar.src;
            }
          }
        }
      }

      out.account = acc;
      return out;
    },
    args: [LS_KEYS, LS_WEB_PROTECT],
  });
  return result || { keys: '', web_protect: '', ua: '', account: {} };
}

/**
 * 通过 creator API 获取当前登录账号的真实信息（最可靠方式）。
 * 后端也用同一接口：creator.douyin.com/web/api/media/user/info/
 * 只在 creator.douyin.com 域名下可用（同域请求，无 CORS 限制）。
 */
async function fetchLoggedInAccount(tabId) {
  try {
    const [{ result } = {}] = await executeBounded({
      target: { tabId },
      world: 'MAIN', // MAIN 世界才能用页面的 cookie 发同域请求
      func: async () => {
        try {
          const resp = await fetch(
            'https://creator.douyin.com/web/api/media/user/info/',
            // Cold creator documents may still be initializing the site SDK.
            // Keep a finite request budget without aborting every five seconds.
            { credentials: 'include', signal: AbortSignal.timeout(15000) },
          );
          if (!resp.ok) return { error: 'http', http_status: resp.status };
          const data = await resp.json();
          if (data.status_code !== 0) {
            return {
              error: 'api', api_code: Number.isSafeInteger(data.status_code) ? data.status_code : null,
            };
          }
          const user = data.user || {};
          const avatar =
            (user.avatar_thumb &&
              user.avatar_thumb.url_list &&
              user.avatar_thumb.url_list[0]) ||
            (user.avatar_larger &&
              user.avatar_larger.url_list &&
              user.avatar_larger.url_list[0]) ||
            (user.avatar_medium &&
              user.avatar_medium.url_list &&
              user.avatar_medium.url_list[0]) ||
            '';
          return {
            nickname: user.nickname || '',
            sec_uid: user.sec_uid || '',
            unique_id: user.unique_id || user.short_id || '',
            uid: String(user.uid || ''),
            avatar,
          };
        } catch (e) {
          return { error: ['TimeoutError','AbortError'].includes(e.name) ? 'timeout' : 'network' };
        }
      },
    }, 18000);
    return result || { error: 'empty' };
  } catch (e) {
    return { error: 'page_read' };
  }
}

function accountReadFailure(account) {
  const reason = account?.error === 'timeout' ? '资料接口响应超时'
    : account?.error === 'network' ? '资料接口网络或响应解析异常'
    : account?.error === 'page_read' ? '资料页面读取失败或超时'
    : account?.error === 'http' && Number.isInteger(account.http_status) ? `资料接口 HTTP ${account.http_status}`
    : account?.error === 'api' && Number.isSafeInteger(account.api_code) ? `资料接口状态码 ${account.api_code}`
    : '资料接口未返回完整的当前账号身份';
  return `创作者中心账号资料校验未完成：${reason}；本次未生成凭证。请检查创作者页面加载或验证后重试`;
}

function captureProgress(message) {
  $('tabHint').textContent = message;
  $('tabHint').hidden = false;
}

/** 把凭证打包成单行「一键导入串」：DYCRED1.<base64url(JSON)>。 */
function buildBundle({
  cookie,
  cookie_headers,
  ticket_guard_server_data,
  web_protect,
  keys,
  ua,
  sec_uid,
  nickname,
  unique_id,
  dtrait_blob,
  session_dtrait,
  session_dtrait_path,
}) {
  const json = JSON.stringify({
    cookie,
    cookie_headers: cookie_headers || {},
    ticket_guard_server_data: ticket_guard_server_data || '',
    web_protect,
    keys,
    ua,
    sec_uid: sec_uid || '',         // 新增：用于后端跳过 profile 拉取
    nickname: nickname || '',       // 新增：前端已识别的昵称
    unique_id: unique_id || '',     // 新增：抖音号
    dtrait_blob: dtrait_blob || '',
    session_dtrait: session_dtrait || '',
    session_dtrait_path: session_dtrait_path || '',
  });
  // UTF-8 安全的 base64url
  const b64 = btoa(unescape(encodeURIComponent(json)))
    .replaceAll('+', '-')
    .replaceAll('/', '_')
    .replace(/=+$/, '');
  return `DYCRED1.${b64}`;
}

function validateCookie(cookie) {
  if (!cookie) return ['bad', '空'];
  if (/(^|;\s*)sessionid(_ss)?=/.test(cookie)) return ['ok', '含 sessionid ✓'];
  return ['warn', '缺 sessionid'];
}

function validateJsonField(raw, needKey) {
  if (!raw) return ['warn', '未取到'];
  try {
    let obj = JSON.parse(raw);
    if (obj && typeof obj.data === 'string') obj = JSON.parse(obj.data);
    if (needKey && !obj[needKey]) return ['warn', `缺 ${needKey}`];
    return ['ok', '已取到 ✓'];
  } catch {
    return ['bad', 'JSON 解析失败'];
  }
}

function validateTicketSource(webProtect, serverData) {
  if (serverData.raw) {
    if (serverData.data.ticket && serverData.data.ts_sign && serverData.data.client_cert) {
      const source = serverData.source === 'response_header' ? '响应头' : 'Cookie';
      return ['ok', `server_data ${source} ✓`];
    }
    return ['bad', 'server_data 解析失败'];
  }
  return validateJsonField(webProtect, 'ticket');
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]),
  );
}

function renderAccount(account, fingerprint, uidTt, isSwitched) {
  const acc = account || {};
  const card = $('accountCard');
  const hasInfo = acc.nickname || acc.unique_id || acc.avatar;
  const isApi = acc._source === 'api';

  const avatarEl = $('acctAvatar');
  // 如果检测到账号切换且信息来源不是 API，隐藏头像（可能是缓存的旧头像，不可信）
  if (isSwitched && !isApi) {
    avatarEl.hidden = true;
    console.log('[扩展v2.0] 检测到账号切换且无 API 验证，已隐藏头像');
  } else if (acc.avatar) {
    avatarEl.onerror = () => {
      avatarEl.hidden = true;
    };
    // 添加时间戳破坏缓存，确保显示最新头像
    const avatarUrl = acc.avatar.includes('?')
      ? `${acc.avatar}&_t=${Date.now()}`
      : `${acc.avatar}?_t=${Date.now()}`;
    avatarEl.src = avatarUrl;
    avatarEl.hidden = false;
    console.log('[扩展v2.0] 显示头像，source=' + (isApi ? 'API' : '页面推断'));
  } else {
    avatarEl.hidden = true;
  }

  const nameText = acc.nickname || '（未识别到昵称）';
  // 来源标记：API ✓ 或 页面推断 ⚠
  const sourceTag = isApi
    ? ' <span style="color:#2ecc71;font-size:11px;font-weight:400;" title="通过 creator API 验证，信息可靠">API ✓</span>'
    : ' <span style="color:var(--warn);font-size:11px;font-weight:400;" title="从页面推断，可能不准确（建议在 creator.douyin.com 抓取）">页面推断 ⚠</span>';
  // 如果是切换账号（且没有 API 验证），在昵称后添加警告标记
  if (isSwitched && !isApi) {
    $('acctName').innerHTML = `${esc(nameText)} <span style="color:var(--warn);font-size:16px;" title="检测到账号切换，头像已隐藏（可能被浏览器缓存）">⚠️</span>${sourceTag}`;
  } else {
    $('acctName').innerHTML = esc(nameText) + sourceTag;
  }

  const subParts = [];
  // 优先显示抖音号（unique_id）
  if (acc.unique_id) {
    subParts.push(`<b>抖音号</b> ${esc(acc.unique_id)}`);
  }
  if (uidTt) {
    subParts.push(`uid_tt <span class="fp">${esc(uidTt.slice(0, 12))}…</span>`);
  }
  subParts.push(
    fingerprint
      ? `sessionid <span class="fp">${esc(fingerprint)}</span>`
      : '<span class="fp" style="color:var(--bad)">未取到 sessionid</span>',
  );
  $('acctSub').innerHTML = subParts.join(' · ');

  card.classList.toggle('acct-weak', !hasInfo);
  card.hidden = false;
}

function renderStatus({ host, incognito, cookieStoreId, diagRows }) {
  const time = new Date().toLocaleTimeString();
  const storeLabel = incognito ? '无痕窗口' : '普通窗口';
  let diagHtml = '';
  if (diagRows && diagRows.length) {
    const lines = diagRows.map((r) => {
      const label = r.isCurrent ? `${storeLabel}（当前）` : `存储 ${r.storeId}`;
      const sid = r.sessionPrefix ? `${r.sessionPrefix}…` : '（无 sessionid）';
      return `<div class="row sub"><span class="k">${esc(label)}</span><span class="v mono">${esc(sid)}</span></div>`;
    });
    diagHtml = `<div class="row block"><span class="k">各窗口 sessionid</span></div>${lines.join('')}`;
  }
  $('statusLine').innerHTML = `
    <div class="row"><span class="k">当前页面</span><span class="v">${esc(host)}</span></div>
    <div class="row"><span class="k">浏览器环境</span><span class="v">${esc(storeLabel)} · store ${esc(cookieStoreId || '?')}</span></div>
    ${diagHtml}
    <div class="row"><span class="k">抓取时间</span><span class="v">${time}</span></div>
  `;
  $('statusLine').hidden = false;
}

/** 清空上一次抓取的所有展示，避免切换账号后旧数据残留误导。 */
function clearResult() {
  for (const id of ['bundle', 'cookie', 'web_protect', 'keys']) $(id).value = '';
  for (const id of ['cookieStatus', 'wpStatus', 'keysStatus']) {
    const e = $(id);
    e.className = 'badge';
    e.textContent = '';
  }
  $('result').hidden = true;
  $('accountCard').hidden = true;
  $('statusLine').hidden = true;
  $('okBanner').hidden = true;
}

// 记录上一次抓取的 sessionid 指纹和昵称，用于检测账号切换
// 使用 chrome.storage.local 持久化，避免 popup 关闭后丢失
let lastFingerprint = null;
let lastNickname = null;

// 按 cookie 存储分别记录指纹（普通 / 无痕互不覆盖）
async function loadLastFingerprint(storeId) {
  try {
    const key = storeId ? `fp_${storeId}` : 'lastFingerprint';
    const nickKey = storeId ? `nick_${storeId}` : 'lastNickname';
    const result = await chrome.storage.local.get([key, nickKey]);
    lastFingerprint = result[key] || null;
    lastNickname = result[nickKey] || null;
  } catch (e) {
    console.warn('[扩展] 加载历史指纹失败:', e);
  }
}

async function saveFingerprint(fp, nickname, storeId) {
  try {
    const key = storeId ? `fp_${storeId}` : 'lastFingerprint';
    const nickKey = storeId ? `nick_${storeId}` : 'lastNickname';
    await chrome.storage.local.set({ [key]: fp, [nickKey]: nickname });
  } catch (e) {
    console.warn('[扩展] 保存指纹失败:', e);
  }
}

/** 成功横幅：每次成功都重放一次高亮动画，即便数据没变也能看到「确实抓了」。 */
function flashOk(msg) {
  const el = $('okBanner');
  el.textContent = `✓ ${msg} · ${new Date().toLocaleTimeString()}`;
  el.hidden = false;
  el.classList.remove('flash');
  void el.offsetWidth; // 触发重排以重启动画
  el.classList.add('flash');
}

let grabbing = false;

function captureTabScope() {
  const port = chrome.runtime.connect({name:'dy-capture-owned-v1'});
  const pending = new Map();
  let next = 0, disconnected = false;
  const heartbeat = setInterval(() => {
    try { port.postMessage({op:'ping'}); } catch { /* onDisconnect rejects pending calls. */ }
  }, 5000);
  port.onMessage.addListener(message => {
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    if (message.ok) waiter.resolve(message.value);
    else waiter.reject(new Error('临时采集标签创建或清理失败，请重新打开扩展后重试'));
  });
  port.onDisconnect.addListener(() => {
    disconnected = true;
    clearInterval(heartbeat);
    for (const waiter of pending.values()) waiter.reject(new Error('采集窗口已关闭'));
    pending.clear();
  });
  const request = async (op, options={}) => {
    if (disconnected) throw new Error('采集窗口已关闭');
    const id = ++next;
    try {
      return await boundedRead(() => new Promise((resolve,reject) => {
        pending.set(id,{resolve,reject});port.postMessage({id,op,...options});
      }), '临时采集标签操作超时，请重新打开扩展后重试');
    } finally { pending.delete(id); }
  };
  return {
    create: options => request('create',options),
    async close() { try { if (!disconnected) await request('finish'); }
      finally { clearInterval(heartbeat);port.disconnect(); } },
  };
}

function navigateToRecommendation() {
  if (location.origin !== 'https://www.douyin.com') return 'wrong_origin';
  if (!location.pathname.startsWith('/jingxuan')) return 'already';
  const link = Array.from(document.querySelectorAll('a[href]')).find(a => {
    try {
      const u = new URL(a.href, location.href);
      return u.origin === 'https://www.douyin.com' && u.pathname === '/'
        && u.searchParams.get('recommend') === '1' && a.getClientRects().length > 0;
    } catch (_) { return false; }
  });
  if (!link) return 'not_ready';
  link.click();
  return 'clicked';
}

async function signingPageInfo(tabId) {
  const [{result} = {}] = await executeBounded({target:{tabId},world:'MAIN',func:()=>{
    const read=names=>names.map(n=>localStorage.getItem(n)||'').filter(Boolean);
    return {origin:location.hostname,pathname:location.pathname,ua:navigator.userAgent,
      keys:read(['security-sdk/s_sdk_crypt_sdk_new','security-sdk/s_sdk_crypt_sdk']),
      tickets:read(['security-sdk/s_sdk_sign_data_key/web_protect_new','security-sdk/s_sdk_sign_data_key_new/web_protect','security-sdk/s_sdk_sign_data_key/web_protect']),
      dtrait_blob:String(window.__dyauthreplyLatestDtraitBlob || ''),
      dtrait_path:String(window.__dyauthreplyLatestDtraitPath || ''),
      browser_profile:{platform:navigator.platform,width:screen.width,height:screen.height,language:navigator.language,cpu_core_num:navigator.hardwareConcurrency,device_memory:navigator.deviceMemory,timezone_name:Intl.DateTimeFormat().resolvedOptions().timeZone,referer:'https://www.douyin.com/jingxuan',geo:[innerWidth,innerHeight,outerWidth,outerHeight,screen.availWidth,screen.availHeight,screen.width,screen.height]}};
  }});
  return result || {};
}

async function waitLoaded(tabId, expectedHost) {
  // Video/stream requests can keep tab.status="loading" after the SDK is usable.
  // Wait for an initialized document, not every media resource to finish.
  const deadline=Date.now()+20000;
  while(Date.now()<deadline){
    const tab=await boundedRead(()=>chrome.tabs.get(tabId),'登录标签页已关闭或读取超时');
    if(tab.status==='complete' && tab.url && new URL(tab.url).hostname===expectedHost)return;
    try {
      const [{result}={}]=await executeBounded({target:{tabId},func:()=>({ready:document.readyState,host:location.hostname})});
      if(['interactive','complete'].includes(result?.ready)&&result?.host===expectedHost)return;
    } catch { /* A just-created document may not be available yet. */ }
    await new Promise(r=>setTimeout(r,500));
  }
  throw new Error('页面初始化超时，请检查主站是否需要验证');
}

async function observations(storeId, tabId, since) {
  const keys=['ticket','token','dtrait'].map(kind=>`dy_capture_v2_${storeId}_${tabId}_www.douyin.com_${kind}`);
  const values=await boundedRead(()=>chrome.storage.session.get(keys),'读取浏览器网络采样失败或超时');const result={};
  for(let i=0;i<keys.length;i++){
    const v=values[keys[i]];
    if(v?.storeId===storeId&&v.tabId===tabId&&v.sourceHost==='www.douyin.com'&&v.capturedAt>=since)result[['ticket','token','dtrait'][i]]=v;
  }
  return result;
}

async function grab() {
  if(grabbing)return;grabbing=true;const btn=$('grabBtn');btn.disabled=true;btn.classList.add('loading');btn.textContent='初始化主站并采集中…';
  let scope;
  try {
    scope=captureTabScope();
    clearResult();$('tabHint').hidden=true;
    captureProgress('正在读取当前标签页…');
    const [tab]=await boundedRead(()=>chrome.tabs.query({active:true,currentWindow:true}),'读取当前标签页失败或超时，请重新打开扩展');
    const host=tab?.url ? new URL(tab.url).hostname : '';
    if(!tab || !['www.douyin.com','creator.douyin.com'].includes(host))throw new Error('请在已登录的抖音主站或创作者中心打开扩展');
    captureProgress('正在核对当前窗口的登录存储…');
    const storeId=await resolveCookieStoreId(tab);if(!storeId)throw new Error('未识别到当前窗口的 Cookie 存储');
    // Warm both origins while the identity API is loading. Keep the creator
    // document alive so its asynchronous device initialization is not cancelled.
    const since=Date.now();
    captureProgress('正在打开临时采集页面…');
    let creator=tab;
    if(host!=='creator.douyin.com'){
      creator=await scope.create({windowId:tab.windowId,url:'https://creator.douyin.com/creator-micro/data/following/chat'});
    }
    // Even with UIFID, an idle existing page need not emit a fresh SDK Token.
    // Always initialize an owned main page; never navigate the user's page.
    const main=await scope.create({windowId:tab.windowId,url:'https://www.douyin.com/?recommend=1'});
    captureProgress('正在等待主站与创作者页面初始化…');
    await Promise.all([waitLoaded(creator.id,'creator.douyin.com'),waitLoaded(main.id,'www.douyin.com')]);
    if(await resolveCookieStoreId(await boundedRead(()=>chrome.tabs.get(main.id),'读取主站标签资料失败或超时'))!==storeId)throw new Error('主站页面与当前窗口不在同一 Cookie 存储');
    const initialCreator=await cookieHeaderForUrl('https://creator.douyin.com/',storeId);
    const sid=DyCapture.session(initialCreator);if(!sid)throw new Error('创作者中心未取得登录会话');
    let account=null;
    for(let i=0;i<3;i++){
      captureProgress(`正在校验当前账号资料（${i+1}/3）…`);
      account=await fetchLoggedInAccount(creator.id);
      if(account?.sec_uid && account.nickname && !account.error)break;
      await new Promise(r=>setTimeout(r,750));
    }
    if(!account?.sec_uid || !account.nickname || account.error)throw new Error(accountReadFailure(account));
    if(DyCapture.session(await cookieHeaderForUrl('https://creator.douyin.com/',storeId))!==sid)throw new Error('账号资料读取期间切换了登录会话，请重新采集');
    const deadline=Date.now()+30000;let selected=null;let missing=[];let readFailures=0;let recommendationAttempts=0;let recommendationLastAttempt=0;
    while(Date.now()<deadline){
      let sample;
      try {
        sample=await Promise.all([collectScopedCookieHeaders(storeId),signingPageInfo(main.id),signingPageInfo(creator.id),observations(storeId,main.id,since)]);
        readFailures=0;
      } catch(error) {
        if(++readFailures>=3)throw error;
        $('tabHint').textContent=`页面读取暂时失败，正在重试 ${readFailures}/3`;$('tabHint').hidden=false;
        await new Promise(r=>setTimeout(r,500));continue;
      }
      const [headers,page,creatorPage,seen]=sample;
      if(page.origin!=='www.douyin.com')throw new Error('主站跳转到登录或验证页面，请完成主站登录后重试');
      if(!DyCapture.cookie(headers['www.douyin.com'],'UIFID') && page.pathname?.startsWith('/jingxuan') ){
        const now=Date.now();
        if(now-recommendationLastAttempt>=3000){
          if(recommendationAttempts>=3)throw new Error('主站推荐切换未完成（已尝试3次），请检查页面加载或验证后重试');
        const [{result}={}]=await executeBounded({target:{tabId:main.id},world:'MAIN',func:navigateToRecommendation});
        if(result==='clicked'){recommendationAttempts++;recommendationLastAttempt=now;}
        else if(!['not_ready','already'].includes(result))throw new Error('主站推荐入口未就绪，请回到主站后重试');
        }
      }
      const token=seen.token?.value || DyCapture.cookie(headers['www.douyin.com'],'msToken') || DyCapture.cookie(headers['www.douyin.com'],'msToken_ss');
      missing=DyCapture.missing(headers,token,sid);
      if(creatorPage.origin!=='creator.douyin.com')throw new Error('创作者中心页面已跳转，请重新采集');
      if(!page.dtrait_blob&&creatorPage.dtrait_blob){page.dtrait_blob=creatorPage.dtrait_blob;page.dtrait_path=creatorPage.dtrait_path;}
      if(!page.dtrait_blob)missing.push('浏览器设备采样');
      const material=await DyCapture.selectMaterial([...(page.keys || []),...(creatorPage.keys || [])],[seen.ticket?.value,...(page.tickets || []),...(creatorPage.tickets || [])],headers['www.douyin.com']);
      if(!material)missing.push('当前会话配套票据/签名密钥');
      if(!missing.length){selected={headers,page,seen,token,material};break;}
      $('tabHint').textContent=`主站已初始化 ${Math.floor((Date.now()-since)/1000)} 秒，等待浏览器生成：${missing.join('、')}。只采集真实值，不使用临时值或随机补值。`;$('tabHint').hidden=false;
      await new Promise(r=>setTimeout(r,Date.now()-since<10000?500:1000));
    }
    if(!selected)throw new Error(`采集未完成：${missing.join('、')}。请在主站完成加载/验证后重试；本次未生成导入串`);
    const {headers,page,seen,token,material}=selected;
    const finalHeaders=await collectScopedCookieHeaders(storeId);
    if(['www.douyin.com','creator.douyin.com','imapi.douyin.com'].some(host=>DyCapture.session(finalHeaders[host])!==sid))throw new Error('采集期间切换了账号，请重新抓取');
    const payload={capture_version:2,cookie:headers['www.douyin.com'],cookie_headers:headers,
      query_ms_token:token,query_ms_token_captured_at_ms:seen.token?.capturedAt || Date.now(),
      browser_profile:page.browser_profile,ticket_guard_server_data:material.serverData,web_protect:material.serverData,
      keys:material.keys,ua:page.ua,sec_uid:account.sec_uid,nickname:account.nickname,unique_id:account.unique_id,
      dtrait_blob:page.dtrait_blob,session_dtrait:seen.dtrait?.value || '',session_dtrait_path:page.dtrait_path || seen.dtrait?.path || ''};
    const packed='DYCRED1.'+btoa(unescape(encodeURIComponent(JSON.stringify(payload)))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
    if(packed.length>1048576)throw new Error('采集结果超出大小限制');
    $('bundle').value=packed;$('cookie').value=payload.cookie;$('web_protect').value=material.serverData;$('keys').value=material.keys;
    setBadge($('cookieStatus'),'ok','主站/会话/SDK 信息已核对');setBadge($('wpStatus'),'ok','与主站签名匹配');setBadge($('keysStatus'),'ok','与票据公钥匹配');
    renderAccount({...account,_source:'api'},accountFingerprint(payload.cookie),extractCookieField(payload.cookie,'uid_tt'),false);
    $('result').hidden=false;$('tabHint').hidden=true;
    flashOk('主站信息、分域会话、SDK Token 和签名材料已采集；发送能力仍以实际回执为准');
  } catch(e) {
    clearResult();$('tabHint').textContent=e instanceof Error?e.message:String(e);$('tabHint').hidden=false;
  } finally {
    try { await scope?.close(); }
    catch { $('tabHint').textContent='临时采集标签清理未完成，请重新打开扩展后重试';$('tabHint').hidden=false; }
    grabbing=false;btn.disabled=false;btn.classList.remove('loading');btn.textContent='补齐主站并抓取登录态';
  }
}

async function copyText(text, okMsg) {
  if (!text) {
    showToast('内容为空，无可复制');
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast(okMsg || '已复制');
  } catch {
    // 退化方案：用临时 textarea + execCommand
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    showToast(okMsg || '已复制');
  }
}

document.addEventListener('click', (ev) => {
  const t = ev.target;
  if (!(t instanceof HTMLElement)) return;
  const field = t.dataset.copy;
  if (field) copyText($(field).value, `已复制 ${field}`);
});

$('grabBtn').addEventListener('click', grab);

$('copyBundleBtn').addEventListener('click', () => {
  copyText($('bundle').value, '已复制一键导入串');
});

// 启动时不预加载指纹；每次抓取按当前 tab 的 cookieStoreId 加载
