/* Own temporary tabs outside the popup, whose JS context disappears on close. */
(() => {
  const KEY = 'dy_capture_owned_tabs_v1';
  const URLS = new Set([
    'https://www.douyin.com/?recommend=1',
    'https://creator.douyin.com/creator-micro/data/following/chat',
  ]);
  const owned = new Map();
  let writes = Promise.resolve();
  const persist = () => {
    writes = writes.catch(() => {}).then(() => chrome.storage.session.set({[KEY]:Object.fromEntries(owned)}));
    return writes;
  };
  async function remove(id) {
    try { await chrome.tabs.remove(id); }
    catch {
      const stillOpen = await chrome.tabs.get(id).catch(() => null);
      if (stillOpen) return false;
    }
    owned.delete(id);
    return true;
  }
  // Session storage survives worker suspension, but not a browser session reset.
  const recovered = (async () => {
    const previous = (await chrome.storage.session.get(KEY))[KEY] || {};
    for (const [rawId, url] of Object.entries(previous).slice(0, 30)) {
      const id = Number(rawId);
      if (!Number.isSafeInteger(id) || id < 0 || !URLS.has(url)) continue;
      owned.set(id, url);
      await remove(id);
    }
    await persist();
  })();
  chrome.runtime.onConnect.addListener(port => {
    if (port.name !== 'dy-capture-owned-v1') return;
    if (port.sender?.id !== chrome.runtime.id || port.sender?.url !== chrome.runtime.getURL('popup.html')) {
      port.disconnect();
      return;
    }
    let closed = false, created = 0;
    const tabs = new Set();
    async function cleanup() {
      closed = true;
      await recovered;
      for (const id of [...tabs]) if (await remove(id)) tabs.delete(id);
      await persist();
      if (tabs.size) throw new Error('temporary tab cleanup incomplete');
    }
    const reply = value => { try { port.postMessage(value); } catch { void cleanup().catch(() => {}); } };
    port.onDisconnect.addListener(() => { void cleanup().catch(() => {}); });
    port.onMessage.addListener(message => {
      if (message?.op === 'ping') return;
      if (!Number.isSafeInteger(message?.id) || message.id < 1) return;
      void (async () => {
        await recovered;
        if (message.op === 'finish') { await cleanup(); return null; }
        if (closed || message.op !== 'create' || !URLS.has(message.url)
            || !Number.isSafeInteger(message.windowId) || message.windowId < 0 || created >= 3) {
          throw new Error('invalid capture tab request');
        }
        created++;
        const tab = await chrome.tabs.create({windowId:message.windowId,url:message.url,active:false});
        if (!Number.isSafeInteger(tab.id)) throw new Error('missing created tab identity');
        owned.set(tab.id, message.url);
        if (closed) { await remove(tab.id); await persist(); throw new Error('popup closed'); }
        tabs.add(tab.id);
        try { await persist(); }
        catch { await remove(tab.id); tabs.delete(tab.id); throw new Error('ownership record failed'); }
        return {id:tab.id,url:tab.url,windowId:tab.windowId};
      })().then(value => reply({id:message.id,ok:true,value}),
        () => reply({id:message.id,ok:false,error:'临时采集标签创建或清理失败，请重新打开扩展后重试'}));
    });
  });
})();
