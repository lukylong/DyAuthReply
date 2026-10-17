/* Shared, pure capture contract. Values remain in browser memory/session storage. */
(() => {
  const tokenOK = v => typeof v === 'string' && /^[A-Za-z0-9_+/=-]{32,8192}$/.test(v);
  const cookieValues = (raw, name) => String(raw || '').split(';').map(p => p.trim().split(/=(.*)/s))
    .filter(p => p[0] === name && p[1]).map(p => p[1]);
  const cookie = (raw, name) => cookieValues(raw, name).at(-1) || '';
  const session = raw => cookie(raw, 'sessionid') || cookie(raw, 'sessionid_ss');
  function object(raw) {
    let v = raw;
    for (let i = 0; i < 6; i++) {
      if (v && typeof v === 'object') {
        if (typeof v.data === 'string') { v = v.data; continue; }
        return v;
      }
      if (typeof v !== 'string' || v.length > 262144) return {};
      try { v = JSON.parse(v); continue; } catch { /* Try URL or base64 wrappers. */ }
      try { const d = decodeURIComponent(v); if (d !== v) { v = d; continue; } } catch {}
      try { v = new TextDecoder().decode(Uint8Array.from(atob(v.replaceAll('-', '+').replaceAll('_', '/')), c => c.charCodeAt(0))); }
      catch { return {}; }
    }
    return {};
  }
  function tokenFromUrl(raw) {
    try {
      const u = new URL(raw);
      if (u.protocol !== 'https:' || !['www.douyin.com','imapi.douyin.com'].includes(u.hostname)) return '';
      const token = u.searchParams.get('msToken'); return tokenOK(token) ? token : '';
    } catch { return ''; }
  }
  function missing(headers, token, expectedSession) {
    const main = headers?.['www.douyin.com'] || '';
    const sid = session(main); const result = [];
    if (!sid) result.push('主站登录会话');
    if (!cookie(main,'UIFID')) result.push('主站 UIFID');
    if (!cookie(main,'s_v_web_id')) result.push('主站 s_v_web_id');
    if (!tokenOK(token)) result.push('浏览器 SDK Token');
    if (expectedSession && sid !== expectedSession) result.push('主站与创作者账号不一致');
    for (const host of ['creator.douyin.com','imapi.douyin.com']) {
      if (!sid || session(headers?.[host]) !== sid) result.push(`${host} 会话不一致`);
    }
    const sessions = Object.values(headers || {}).flatMap(raw => ['sessionid','sessionid_ss','sid_tt'].flatMap(k => cookieValues(raw,k)));
    if (new Set(sessions).size > 1) result.push('登录 Cookie 冲突');
    return result;
  }
  function ticketMatchesCookie(ticket, raw) {
    const modern = cookieValues(raw,'bd_ticket_guard_tssign');
    if (modern.length) return modern.every(v => {
      try { v=decodeURIComponent(v); } catch { return false; }
      if (v.startsWith('ts.')) return v===ticket.ts_sign;
      try { const o=JSON.parse(v);return (typeof o==='string'?o:o.value)===ticket.ts_sign; } catch { return false; }
    });
    const legacy=cookie(raw,'bd_ticket_guard_ts_sign_id');return !legacy || String(ticket.ts_sign || '').startsWith(legacy);
  }
  async function selectMaterial(keys, tickets, rawCookie) {
    for (const rawTicket of tickets.filter(Boolean)) {
      const t=object(rawTicket); const cert=t.client_cert || t.sdk_cert || ''; const ticket=t.ticket || t.token;
      if (!ticket || !t.ts_sign || !cert.startsWith('pub.') || !ticketMatchesCookie(t,rawCookie)) continue;
      for (const rawKey of keys.filter(Boolean)) {
        const k=object(rawKey);const pem=k.ec_privateKey || k.private_key || '';
        if (typeof pem!=='string' || pem.length>16384 || !pem.includes('BEGIN PRIVATE KEY')) continue;
        try {
          const bytes=Uint8Array.from(atob(pem.replace(/-----[^-]+-----|\s/g,'')), c=>c.charCodeAt(0));
          const key=await crypto.subtle.importKey('pkcs8',bytes,{name:'ECDSA',namedCurve:'P-256'},true,['sign']);
          const jwk=await crypto.subtle.exportKey('jwk',key);
          const fromUrl=v=>Uint8Array.from(atob(v.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
          const point=new Uint8Array([4,...fromUrl(jwk.x),...fromUrl(jwk.y)]);
          if (btoa(String.fromCharCode(...point)) !== cert.slice(4)) continue;
          return {keys:rawKey,serverData:JSON.stringify({...t,ticket,client_cert:cert})};
        } catch { /* Never guess or export a mismatched signing pair. */ }
      }
    }
    return null;
  }
  globalThis.DyCapture={tokenOK,tokenFromUrl,cookie,session,missing,object,selectMaterial};
})();
