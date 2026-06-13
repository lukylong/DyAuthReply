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

/** 读取当前 douyin 标签页能用到的全部 cookie（含 HttpOnly），拼成 Cookie 头。
 *  以「该 URL 会发送的 cookie」(byUrl) 为准——切换账号后它就是该页当前生效的那一套，
 *  避免被其它子域的旧 sessionid 串掉。 */
async function collectCookies(tabUrl) {
  const [byUrl, byDomain] = await Promise.all([
    chrome.cookies.getAll({ url: tabUrl }).catch(() => []),
    chrome.cookies.getAll({ domain: 'douyin.com' }).catch(() => []),
  ]);
  const map = new Map();
  // 先放域级兜底，再用 URL 级覆盖（URL 级 = 当前页当前账号真正会带的 cookie，优先级更高）
  for (const c of byDomain) if (c && c.name) map.set(c.name, c.value);
  for (const c of byUrl) if (c && c.name) map.set(c.name, c.value);
  return Array.from(map.entries())
    .map(([name, value]) => `${name}=${value}`)
    .join('; ');
}

/** 从 cookie 串里取 sessionid 末尾几位作为账号指纹（切换账号会变，便于肉眼核对）。 */
function accountFingerprint(cookie) {
  const m = /(?:^|;\s*)sessionid(?:_ss)?=([^;]+)/.exec(cookie || '');
  if (!m) return '';
  const v = m[1].trim();
  return v.length <= 8 ? v : `…${v.slice(-8)}`;
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

function renderAccount(account, fingerprint) {
  const acc = account || {};
  const card = $('accountCard');
  const hasInfo = acc.nickname || acc.unique_id || acc.avatar;

  const avatarEl = $('acctAvatar');
  if (acc.avatar) {
    avatarEl.onerror = () => {
      avatarEl.hidden = true;
    };
    avatarEl.src = acc.avatar;
    avatarEl.hidden = false;
  } else {
    avatarEl.hidden = true;
  }

  $('acctName').textContent = acc.nickname || '（未识别到昵称）';

  const subParts = [];
  if (acc.unique_id) subParts.push(`抖音号 ${esc(acc.unique_id)}`);
  subParts.push(
    fingerprint
      ? `指纹 <span class="fp">${esc(fingerprint)}</span>`
      : '<span class="fp" style="color:var(--bad)">未取到 sessionid</span>',
  );
  $('acctSub').innerHTML = subParts.join(' · ');

  card.classList.toggle('acct-weak', !hasInfo);
  card.hidden = false;
}

function renderStatus({ host }) {
  const time = new Date().toLocaleTimeString();
  $('statusLine').innerHTML = `
    <div class="row"><span class="k">当前页面</span><span class="v">${esc(host)}</span></div>
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
    // （放在 try 内：任何异常都能被 catch，finally 一定复位按钮，避免卡死“点了没反应”）
    clearResult();
    $('tabHint').hidden = true;

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !/:\/\/[^/]*douyin\.com/.test(tab.url)) {
      $('tabHint').textContent =
        '当前标签页不是抖音页面。请切换到已登录的 douyin.com 标签页（creator.douyin.com 或 www.douyin.com）再抓取。';
      $('tabHint').hidden = false;
      return;
    }
    $('tabHint').hidden = true;

    const [cookie, ls] = await Promise.all([
      collectCookies(tab.url),
      collectPageInfo(tab.id),
    ]);

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

    const fp = accountFingerprint(cookie);
    let host = tab.url;
    try {
      host = new URL(tab.url).host;
    } catch {
      /* ignore */
    }
    renderAccount(ls.account, fp);
    renderStatus({ host });
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

// 打开 popup 即自动尝试抓取一次，省一次点击
grab();
