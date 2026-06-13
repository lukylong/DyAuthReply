/**
 * 抖音登录态提取器 —— popup 逻辑
 *
 * 抓取三件套（与 DyAuthReply 后台「导入登录态」对话框字段一一对应）：
 *   cookie       —— 用 chrome.cookies API 读取，能拿到 document.cookie 取不到的 HttpOnly sessionid
 *   web_protect  —— 页面 localStorage['security-sdk/s_sdk_sign_data_key/web_protect']
 *   keys         —— 页面 localStorage['security-sdk/s_sdk_crypt_sdk']
 */

const LS_KEYS = 'security-sdk/s_sdk_crypt_sdk';
const LS_WEB_PROTECT = 'security-sdk/s_sdk_sign_data_key/web_protect';

const $ = (id) => document.getElementById(id);

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
    try {
      const full = await chrome.tabs.get(tab.id);
      if (full?.cookieStoreId) return full.cookieStoreId;
    } catch {
      /* ignore */
    }
  }

  const stores = await chrome.cookies.getAllCookieStores().catch(() => []);
  if (tab.id != null) {
    for (const store of stores) {
      if (store.tabIds?.includes(tab.id)) return store.id;
    }
  }

  if (tab.incognito) {
    const alt = stores.find((s) => s.id !== '0');
    if (alt) return alt.id;
  }
  if (stores.some((s) => s.id === '0')) return '0';
  if (stores.length === 1) return stores[0].id;
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
  const cookies = await chrome.cookies.getAll({ url: tabUrl, storeId }).catch(() => []);
  const byName = new Map();
  for (const c of cookies) {
    if (!c || !c.name) continue;
    byName.set(c.name, pickBetterCookie(byName.get(c.name), c, tabUrl));
  }
  return [...byName.values()].map((c) => `${c.name}=${c.value}`).join('; ');
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
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    // MAIN 世界：可读取页面 window.__INITIAL_STATE__ 等全局状态（ISOLATED 世界读不到）；
    // localStorage / DOM 在两个世界都可读，故切到 MAIN 不影响 keys/web_protect 抓取。
    world: 'MAIN',
    func: (kKeys, kWp) => {
      const out = {
        keys: localStorage.getItem(kKeys) || '',
        web_protect: localStorage.getItem(kWp) || '',
        ua: navigator.userAgent || '',
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
        // 抖音号通常紧跟在昵称之后；取页面里 "抖音号" 之前最近的一段非空文本作为昵称兜底
        const h1 = document.querySelector('h1, [data-e2e="user-title"], .user-title');
        if (h1 && h1.textContent && h1.textContent.trim()) {
          acc.nickname = h1.textContent.trim().slice(0, 64);
        }
      }

      // ④ DOM 兜底头像
      if (!acc.avatar) {
        const img = document.querySelector(
          'img[src*="aweme-avatar"], img[src*="/avatar"], img[class*="avatar" i]',
        );
        if (img && img.src) acc.avatar = img.src;
      }

      out.account = acc;
      return out;
    },
    args: [LS_KEYS, LS_WEB_PROTECT],
  });
  return result || { keys: '', web_protect: '', ua: '', account: {} };
}

/** 把三件套打包成单行「一键导入串」：DYCRED1.<base64url(JSON)>。 */
function buildBundle({ cookie, web_protect, keys, ua }) {
  const json = JSON.stringify({ cookie, web_protect, keys, ua });
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

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]),
  );
}

function renderAccount(account, fingerprint, uidTt, isSwitched) {
  const acc = account || {};
  const card = $('accountCard');
  const hasInfo = acc.nickname || acc.unique_id || acc.avatar;

  const avatarEl = $('acctAvatar');
  // 如果检测到账号切换，隐藏头像（可能是缓存的旧头像，不可信）
  if (isSwitched) {
    avatarEl.hidden = true;
    console.log('[扩展v1.3] 检测到账号切换，已隐藏头像');
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
    console.log('[扩展v1.3] 显示头像，isSwitched=false');
  } else {
    avatarEl.hidden = true;
  }

  const nameText = acc.nickname || '（未识别到昵称）';
  // 如果是切换账号，在昵称后添加警告标记
  $('acctName').innerHTML = isSwitched
    ? `${esc(nameText)} <span style="color:var(--warn);font-size:16px;" title="检测到账号切换，头像已隐藏（可能被浏览器缓存）">⚠️</span>`
    : esc(nameText);

  const subParts = [];
  if (acc.unique_id) subParts.push(`抖音号 ${esc(acc.unique_id)}`);
  if (uidTt) subParts.push(`uid_tt <span class="fp">${esc(uidTt.slice(0, 12))}…</span>`);
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

async function grab() {
  if (grabbing) return;
  grabbing = true;
  const btn = $('grabBtn');
  btn.disabled = true;
  btn.classList.add('loading');
  btn.textContent = '抓取中…';
  $('okBanner').hidden = true;

  try {
    // 先清空旧账号残留，再抓——切号时不会出现新账号卡片配旧 cookie 的误导画面
    // （放在 try 内：任何异常都能被 catch，finally 一定复位按钮，避免卡死”点了没反应”）
    clearResult();
    $('tabHint').hidden = true;

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !/:\/\/[^/]*douyin\.com/.test(tab.url)) {
      $('tabHint').textContent =
        '当前标签页不是抖音页面。请切换到已登录的 douyin.com 标签页（creator.douyin.com 或 www.douyin.com）再抓取。';
      $('tabHint').hidden = false;
      return;
    }

    // 强烈建议在 creator.douyin.com 抓取（必需获取发送凭证）
    const url = new URL(tab.url);
    if (url.hostname !== 'creator.douyin.com') {
      $('tabHint').innerHTML =
        '<b>⚠️ 建议在 creator.douyin.com 抓取</b><br>' +
        `当前页面：<b>${url.hostname}</b><br><br>` +
        '<b style="color:#ff6b00;">强烈建议</b>切换到 <b>creator.douyin.com</b> 再抓取，原因：<br>' +
        '1. <b>web_protect / keys</b> 只在 creator 站点有（发送私信必需）<br>' +
        '2. 避免多域名 Cookie 混乱<br>' +
        '3. 后台对接的是 creator 站点<br><br>' +
        '<b>推荐页面</b>：<a href="https://creator.douyin.com/creator-micro/data/following/chat" target="_blank">creator.douyin.com/creator-micro/data/following/chat</a>（私信管理）<br>' +
        '<br>如果仅做「仅接收」导入（不发送私信），可继续抓取。';
      $('tabHint').hidden = false;
      // 不 return，允许继续抓取，但给出警告
    }

    $('tabHint').hidden = true;

    const cookieStoreId = await resolveCookieStoreId(tab);
    if (!cookieStoreId) {
      $('tabHint').textContent =
        '无法识别当前窗口的 cookie 存储。请到 chrome://extensions 重新加载本扩展；若仍失败，请更新 Chrome/Edge 到较新版本。';
      $('tabHint').hidden = false;
      return;
    }

    await loadLastFingerprint(cookieStoreId);

    const [cookie, ls, diag] = await Promise.all([
      collectCookies(tab.url, cookieStoreId),
      collectPageInfo(tab.id),
      diagnoseSessionIds(tab.url, cookieStoreId),
    ]);

    const fp = accountFingerprint(cookie);
    const uidTt = extractCookieField(cookie, 'uid_tt');
    const currentNickname = (ls.account && ls.account.nickname) || '';

    if (tab.incognito && !fp) {
      $('tabHint').innerHTML =
        '<b style="color:var(--bad)">无痕窗口未读到 sessionid</b><br>' +
        '请确认：① 已在无痕窗口登录 creator.douyin.com；② 在 chrome://extensions 为本扩展勾选<b>「在无痕模式下启用」</b>；③ 在<b>无痕窗口内</b>点击扩展图标（不是在普通窗口点）。';
      $('tabHint').hidden = false;
      return;
    }

    if (diag.sameAcrossStores) {
      $('tabHint').innerHTML =
        '<b style="color:var(--bad)">⚠️ 普通窗口与无痕窗口 sessionid 完全相同</b><br>' +
        '这说明浏览器里实际只有<b>一套</b>登录态，不是两个独立账号。常见原因：<br>' +
        '1. 两个窗口其实登录的是<b>同一个</b>抖音号<br>' +
        '2. 第二个窗口是<b>普通窗口</b>而不是无痕（同一 Chrome 配置只能同时保留一个 douyin sessionid）<br>' +
        '3. 扩展未在无痕启用，抓到的仍是普通窗口 cookie<br><br>' +
        '<b>正确做法</b>：账号 A 用普通窗口，账号 B 必须用<b>无痕窗口</b>（或另一个 Chrome 用户配置），并在 chrome://extensions 勾选「在无痕模式下启用」。<br>' +
        '下方「各窗口 sessionid」应显示<b>两个不同的前缀</b>，相同则请勿导入。';
      $('tabHint').hidden = false;
    }

    // 判断是否切换了账号
    const isSwitched = lastFingerprint && fp && lastFingerprint !== fp;

    console.log('[扩展v1.3] 账号切换检测:', {
      lastFingerprint,
      currentFingerprint: fp,
      isSwitched,
      nickname: currentNickname
    });

    // 检测账号切换：Cookie 指纹变了 → 提示可能需要多次刷新
    if (isSwitched) {
      // 指纹变了，说明账号切换了
      // 显示提示：页面可能需要多次刷新或等待，确保数据已更新
      $('tabHint').innerHTML =
        '<b>⚠️ 检测到账号切换</b><br>' +
        `Cookie 指纹已变化：<span class=”fp”>${esc(lastFingerprint)}</span> → <span class=”fp”>${esc(fp)}</span><br>` +
        (currentNickname ? `当前识别昵称：<b>${esc(currentNickname)}</b><br>` : '') +
        '<br><b style=”color:#ff6b00;”>注意：头像已隐藏（浏览器缓存无法清除旧头像）</b><br>' +
        '<br><b>请核对以下信息是否正确：</b><br>' +
        '1. 上方显示的<b>昵称</b>是否是新账号的？<br>' +
        '2. 上方显示的<b>抖音号</b>是否是新账号的？<br>' +
        '3. 去<b>抖音页面</b>本身查看个人中心，核对昵称<br>' +
        '<br><b>如果信息不正确，请：</b><br>' +
        '• 硬刷新（Ctrl+Shift+R / Cmd+Shift+R）<br>' +
        '• 或关闭标签页重新打开新账号<br>' +
        '<br><b style=”color:#2ecc71;”>核对无误后再复制导入串！</b>';
      $('tabHint').hidden = false;
      showToast('检测到账号切换，请核对信息');
    }

    // 记录当前指纹和昵称（持久化到 storage）
    lastFingerprint = fp;
    lastNickname = currentNickname;
    saveFingerprint(fp, currentNickname, cookieStoreId);

    $('cookie').value = cookie;
    $('web_protect').value = ls.web_protect;
    $('keys').value = ls.keys;
    $('bundle').value = buildBundle({
      cookie,
      web_protect: ls.web_protect,
      keys: ls.keys,
      ua: ls.ua,
    });

    setBadge($('cookieStatus'), ...validateCookie(cookie));
    setBadge($('wpStatus'), ...validateJsonField(ls.web_protect, 'ticket'));
    setBadge($('keysStatus'), ...validateJsonField(ls.keys, 'ec_privateKey'));

    $('result').hidden = false;

    let host = tab.url;
    try {
      host = new URL(tab.url).host;
    } catch {
      /* ignore */
    }
    renderAccount(ls.account, fp, uidTt, isSwitched);
    renderStatus({
      host,
      incognito: tab.incognito,
      cookieStoreId,
      diagRows: diag.rows,
    });
    const who = ls.account && ls.account.nickname ? ls.account.nickname : (fp || '');
    flashOk(who ? `抓取成功：${who}` : '抓取成功（未读到 sessionid）');
    showToast(who ? `已抓取：${who}` : '已抓取，但未读到 sessionid');

    // 发送凭证（web_protect/keys）按 origin 存于 localStorage，creator 与 www 各一份。
    // 当前页没取到时给出明确指引（cookie 仍可用于「仅接收」导入）。
    if (!ls.web_protect || !ls.keys) {
      $('tabHint').innerHTML =
        '当前页面未取到 <b>web_protect / keys</b>（发送私信所需）。Cookie 仍可用于「仅接收」导入；' +
        '若要发送私信，请在 <b>creator.douyin.com 的「私信」页面</b>打开后再抓取（这两项按站点单独存储）。';
      $('tabHint').hidden = false;
    }
  } catch (e) {
    $('tabHint').textContent = `抓取失败：${e && e.message ? e.message : e}`;
    $('tabHint').hidden = false;
    showToast('抓取失败');
  } finally {
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.textContent = '重新抓取';
    grabbing = false;
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
