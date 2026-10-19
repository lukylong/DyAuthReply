import { test as nodeTest } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
const vue = require('vue');
const test = (name, fn) => nodeTest(name, { timeout: 5000 }, fn);
const base = new URL('../../dyauthreply-client/client-ui/src/', import.meta.url);
const read = path => readFileSync(new URL(path, base), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = () => new Promise(setImmediate);

// Execute the real setup script. Network, lifecycle and timers are test-owned;
// no user profile, browser, service, or business data is touched.
function view(name, api = {}) {
  const script = read(`views/${name}.vue`).match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile('view.ts', script, ts.ScriptTarget.Latest, true);
  const globals = {};
  for (const node of ast.statements.filter(ts.isImportDeclaration)) {
    if (node.moduleSpecifier.text.endsWith('/api/client')) {
      for (const entry of node.importClause.namedBindings.elements) {
        if (!entry.isTypeOnly) globals[entry.name.text] = (...args) => api[entry.name.text]?.(...args) ?? Promise.resolve({});
      }
    }
  }
  const mounted = [], unmounted = [], watchers = [], timers = new Map(); let timerId = 0;
  const license = vue.ref({ can_use_business: true, state_label: '已激活' });
  const settings = vue.ref({ runtime: { auto_start: true }, version_update: { enabled: true, check_frequency: 'manual', update_mirrors: ['custom'] } });
  let resets = 0;
  const context = {
    ...globals, ref: vue.ref, computed: vue.computed, nextTick: vue.nextTick, Error,
    onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn), onBeforeRouteLeave: () => {},
    watch: (source, fn, options) => { watchers.push({ source, fn, options }); return () => {}; },
    useRouter: () => ({ replace() {}, back() {} }), useRoute: () => ({ query: {} }),
    useClientLicense: () => ({ licenseStatus: license, ensureStatus: async () => license.value }),
    useClientRealtime: () => ({ connected: vue.ref(false), setScope() {}, start() {}, stop() {}, subscribe: () => () => {} }),
    useAnnouncementListener() {}, useHiddenAdminEntry: () => ({ onHiddenAdminClick() {} }), APP_VERSION: 'fixture', getUpdateMirrors: () => [],
    useClientSettings: () => ({ settings, resetSettings: () => { resets++; settings.value = { runtime: { auto_start: false }, version_update: { update_mirrors: [] } }; } }),
    useVersionUpdate: () => ({ checkUpdate() {}, isChecking: vue.ref(false), updateInfo: vue.ref(null), checkError: vue.ref('') }),
    DEFAULT_UPDATE_MIRRORS: ['https://example.com/'],
    window: { confirm: () => false }, confirm: () => false,
    document: { addEventListener() {}, removeEventListener() {}, hidden: false },
    setTimeout: fn => { const id = ++timerId; timers.set(id, () => { timers.delete(id); return fn(); }); return id; }, clearTimeout: id => timers.delete(id),
    setInterval: fn => { timers.set(++timerId, fn); return timerId; }, clearInterval: id => timers.delete(id),
    console, requestAnimationFrame: fn => fn(),
  };
  context.window.setTimeout = context.setTimeout;
  context.window.clearTimeout = context.clearTimeout;
  const statements = ast.statements.filter(n => !ts.isImportDeclaration(n));
  const names = statements.flatMap(n => ts.isFunctionDeclaration(n) ? [n.name.text] : ts.isVariableStatement(n) ? n.declarationList.declarations.flatMap(d => ts.isIdentifier(d.name) ? [d.name.text] : []) : []);
  const code = ts.transpileModule(statements.map(n => n.getText(ast)).join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const state = vm.runInNewContext(`${code}; ({${names.join(',')}})`, context);
  return { ...state, api, license, settings, mounted, unmounted, watchers, timers, resets: () => resets };
}

test('native confirm unavailable: emergency stop opens app modal and requires explicit confirmation', async () => {
  let calls = 0; const pending = deferred();
  const h = view('AdminConsoleView', { isAdminLoggedIn: () => true, adminEmergencyStop: () => { calls++; return pending.promise; }, getAdminDashboard: async () => ({}) });
  await h.onEmergencyStop(); assert.equal(h.showStopConfirm?.value, true); assert.equal(calls, 0);
  h.closeStopConfirm(); assert.equal(h.showStopConfirm.value, false); assert.equal(calls, 0);
  h.onEmergencyStop(); const task = h.confirmEmergencyStop();
  h.closeStopConfirm(); await h.confirmEmergencyStop();
  assert.equal(calls, 1); assert.equal(h.showStopConfirm.value, true); assert.equal(h.stopping.value, true);
  pending.resolve({ accounts_stopped: 2, commands_cleared: 3, messages_marked_processed: 4 }); await task;
  assert.equal(h.showStopConfirm.value, false); assert.match(h.stopResult.value, /已急停/);
});

test('failed emergency stop keeps confirmation open with a retryable error', async () => {
  const h = view('AdminConsoleView', { adminEmergencyStop: async () => { throw Error('模拟急停失败'); } });
  h.onEmergencyStop(); await h.confirmEmergencyStop();
  assert.equal(h.showStopConfirm.value, true); assert.equal(h.stopping.value, false);
  assert.match(h.stopError.value, /模拟急停失败/); assert.equal(h.stopResult.value, '');
});

test('reset preferences requires app confirmation and preserves system autostart', () => {
  const h = view('SettingsView'); h.handleReset();
  assert.equal(h.showResetConfirm?.value, true); assert.equal(h.resets(), 0);
  h.closeResetConfirm(); assert.equal(h.resets(), 0);
  h.handleReset(); h.confirmReset(); h.confirmReset();
  assert.equal(h.resets(), 1); assert.equal(h.settings.value.runtime.auto_start, true);
  assert.equal(h.mirrorsText.value, 'https://example.com/'); assert.equal(h.showResetConfirm.value, false);
});

for (const [name, apiName, rowsName] of [['RulesView', 'patchRule', 'rules'], ['TemplatesView', 'patchTemplate', 'templates']]) {
  test(`${name}: double toggle sends one captured state, while independent rows remain operable`, async () => {
    const pending = deferred(); const writes = [];
    const h = view(name, { [apiName]: (id, body) => { writes.push({ id, status: body.status }); return id === 'a' ? pending.promise : Promise.resolve({ status: false }); } });
    const a = { id: 'a', status: true }, b = { id: 'b', status: true }; h[rowsName].value = [a, b];
    const first = h.toggleStatus(a), second = h.toggleStatus(a); await h.toggleStatus(b);
    assert.equal(writes.length, 2); assert.equal(b.status, false);
    const refreshed = { id: 'a', status: true }; h[rowsName].value = [refreshed];
    pending.resolve({ status: false }); await Promise.all([first, second]);
    assert.equal(a.status, false); assert.equal(refreshed.status, false);
    assert.equal(h.togglingIds.value.size, 0);
  });
  test(`${name}: failed toggle restores retry availability without changing displayed state`, async () => {
    const h = view(name, { [apiName]: async () => { throw Error('模拟写入失败'); } });
    const a = { id: 'a', status: true }; await h.toggleStatus(a);
    assert.equal(a.status, true); assert.match(h.error.value, /模拟写入失败/); assert.equal(h.togglingIds.value.size, 0);
    h.api[apiName] = async () => ({ status: false }); await h.toggleStatus(a); assert.equal(a.status, false);
    h.license.value.can_use_business = false; await h.toggleStatus(a); assert.equal(a.status, false);
  });
  test(`${name}: malformed acknowledgement never changes displayed state`, async () => {
    const h = view(name, { [apiName]: async () => ({ success: true }) });
    const row = { id: 'a', status: true }; await h.toggleStatus(row);
    assert.equal(row.status, true); assert.match(h.error.value, /未确认/); assert.equal(h.togglingIds.value.size, 0);
  });
}

test('template deletion uses app confirmation, rejects duplicates and retains server errors', async () => {
  let calls = 0; const pending = deferred(); const h = view('TemplatesView', { deleteTemplate: () => { calls++; return pending.promise; } });
  const row = { id: 'a', name: '模拟模板' }; h.templates.value = [row];
  h.removeTemplate(row); assert.equal(h.deleteTarget?.value.id, 'a'); assert.equal(calls, 0);
  h.closeDelete(); assert.equal(calls, 0); h.removeTemplate(row);
  const task = h.confirmDelete(); await h.confirmDelete(); h.closeDelete();
  assert.equal(calls, 1); assert.equal(h.deleteTarget.value.id, 'a');
  pending.reject(Error('模板正被使用')); await task;
  assert.equal(h.templates.value.length, 1); assert.match(h.deleteError.value, /模板正被使用/);
  h.api.deleteTemplate = async () => ({ success: true }); await h.confirmDelete();
  assert.equal(h.templates.value.length, 0); assert.equal(h.deleteTarget.value, null);
});

test('regex edit/save round trip preserves expression instead of reading keywords', async () => {
  let saved; const h = view('RulesView', { patchRule: async (_id, payload) => { saved = payload; }, listRules: async () => ({ items: [] }) });
  h.openEdit({ id: 'regex', name: '正则规则', match_type: 'regex', keywords: [], regex_pattern: '^价格$', reply_text: '模拟回复', status: true });
  assert.equal(h.form.value.keywordsText, '^价格$'); await h.submitForm();
  assert.equal(saved.regex_pattern, '^价格$'); assert.equal(saved.keywords.length, 0);
  h.openEdit({ id: 'keyword', name: '关键词', match_type: 'contains', keywords: ['价格'], regex_pattern: 'unused', reply_text: 'reply', status: true });
  assert.equal(h.form.value.keywordsText, '价格');
});

for (const failure of [false, true]) {
  test(`rule filter discards old ${failure ? 'failure' : 'success'} and never clears newer loading`, async () => {
    const a = deferred(), b = deferred(); const h = view('RulesView', { listRulesByAccount: id => id === 'A' ? a.promise : b.promise });
    h.filterAccountId.value = 'A'; const first = h.loadRules(); h.filterAccountId.value = 'B'; const second = h.loadRules();
    if (failure) a.reject(Error('old error')); else a.resolve([{ id: 'rule-A' }]); await first;
    assert.equal(h.loading.value, true); assert.equal(h.error.value, '');
    b.resolve([{ id: 'rule-B' }]); await second; assert.equal(h.rules.value[0].id, 'rule-B'); assert.equal(h.loading.value, false);
  });
}
test('rule same-filter requests use latest response; unmounted reads are discarded', async () => {
  const a = deferred(), b = deferred(); let count = 0;
  const h = view('RulesView', { listRules: () => (++count === 1 ? a.promise : b.promise) });
  const first = h.loadRules(), second = h.loadRules(); b.resolve({ items: [{ id: 'new' }] }); await second;
  a.resolve({ items: [{ id: 'old' }] }); await first; assert.equal(h.rules.value[0].id, 'new');
  const late = deferred(); h.api.listRules = () => late.promise; const task = h.loadRules();
  for (const cleanup of h.unmounted) cleanup(); late.resolve({ items: [{ id: 'unmounted' }] }); await task;
  assert.equal(h.rules.value[0].id, 'new');
});

const conv = id => ({ id, peer_nickname: id, last_message_time: '2026-10-19T00:00:00Z', unread_count: 0 });
const page = id => ({ items: [conv(id)], total: 1, has_more: false });
for (const mode of ['account', 'search', 'same-scope', 'append']) {
  for (const failure of [false, true]) {
    test(`conversation ${mode}: obsolete ${failure ? 'failure' : 'success'} cannot change rows, selection or loading`, async () => {
      const a = deferred(), b = deferred(); let count = 0;
      const h = view('ChatView', { listConversations: () => (++count === 1 ? a.promise : b.promise) });
      h.activeAccountId.value = 'A'; h.convHasMore.value = true;
      const first = h.loadConversations(false, mode === 'append');
      if (mode === 'account' || mode === 'append') h.selectAccount('B');
      if (mode === 'search') { h.convKeyword.value = 'new'; h.onConvSearchInput(); }
      const second = h.loadConversations();
      b.resolve(page('new')); await second;
      if (failure) a.reject(Error('obsolete error')); else a.resolve(page('old')); await first;
      assert.equal(h.conversations.value.length, 1); assert.equal(h.conversations.value[0].id, 'new');
      assert.equal(h.activeConversationId.value, 'new'); assert.equal(h.toast.value, '');
      assert.equal(h.loadingConversations.value, false); assert.equal(h.loadingMoreConvs.value, false);
    });
  }
}
test('conversation scope reset clears old visible rows immediately; A-B-A cannot revive an old read', async () => {
  const pending = deferred(); const h = view('ChatView', { listConversations: () => pending.promise });
  h.activeAccountId.value = 'A'; h.conversations.value = [conv('old')]; const task = h.loadConversations();
  h.selectAccount('B'); assert.equal(h.conversations.value.length, 0); h.selectAccount('A');
  pending.resolve(page('obsolete')); await task; assert.equal(h.conversations.value.length, 0);
});
test('actual synchronous Vue scope watcher invalidates A-B-A and search ABA before a new fetch', async () => {
  const pending = deferred(); const h = view('ChatView', { listConversations: () => pending.promise });
  const watcher = h.watchers.find(w => Array.isArray(w.source) && w.source.includes(h.activeAccountId));
  assert.equal(watcher?.options?.flush, 'sync');
  const stop = vue.watch(watcher.source, watcher.fn, watcher.options);
  try {
    h.activeAccountId.value = 'A'; const task = h.loadConversations();
    h.activeAccountId.value = 'B'; h.activeAccountId.value = 'A';
    h.convKeyword.value = 'old'; h.convKeyword.value = 'new'; h.convKeyword.value = '';
    pending.resolve(page('old')); await task; assert.equal(h.conversations.value.length, 0);
  } finally { stop(); }
});
test('actual rule filter watcher invalidates A-B-A without waiting for Vue render', async () => {
  const requests = []; const h = view('RulesView', { listRulesByAccount: () => { const p = deferred(); requests.push(p); return p.promise; } });
  const watcher = h.watchers.find(w => w.source === h.filterAccountId);
  assert.equal(watcher.options?.flush, 'sync');
  const stop = vue.watch(watcher.source, watcher.fn, watcher.options);
  try {
    h.filterAccountId.value = 'A'; h.filterAccountId.value = 'B'; h.filterAccountId.value = 'A';
    assert.equal(requests.length, 3);
    requests[2].resolve([{ id: 'latest-A' }]); await flush();
    requests[0].resolve([{ id: 'obsolete-A' }]); requests[1].reject(Error('obsolete-B')); await flush();
    assert.equal(h.rules.value[0].id, 'latest-A'); assert.equal(h.error.value, '');
  } finally { stop(); }
});
test('conversation current-page append merges unique IDs; older completion does not clear new spinner', async () => {
  const a = deferred(), b = deferred(); let count = 0;
  const h = view('ChatView', { listConversations: () => (++count === 1 ? a.promise : b.promise) });
  h.activeAccountId.value = 'A'; const first = h.loadConversations(), second = h.loadConversations();
  a.resolve(page('old')); await first; assert.equal(h.loadingConversations.value, true);
  b.resolve(page('new')); await second;
  h.convHasMore.value = true;
  h.api.listConversations = async () => ({ items: [conv('new'), conv('next'), conv('next')], total: 2, has_more: false });
  await h.loadConversations(false, true);
  assert.equal(h.conversations.value.length, 2); assert.equal(h.convPage.value, 2);
});
test('unmounted chat ignores pending reads and cannot start more conversation work', async () => {
  const pending = deferred(); let calls = 0;
  const h = view('ChatView', { listConversations: () => { calls++; return pending.promise; } });
  h.activeAccountId.value = 'A'; const task = h.loadConversations();
  for (const cleanup of h.unmounted) cleanup(); pending.resolve(page('old')); await task; await h.loadConversations();
  assert.equal(h.conversations.value.length, 0); assert.equal(calls, 1);
});

test('diagnostics rejected write keeps error and restores checkbox to reread actual state', async () => {
  const h = view('SettingsView', { setDiagnostics: async () => { throw Error('保存失败'); }, getDiagnostics: async () => ({ enabled: true }) });
  h.diagnostics.value = { enabled: true }; const target = { checked: false };
  await h.onDiagnosticsChange({ target });
  assert.equal(h.diagnostics.value.enabled, true); assert.equal(target.checked, true);
  assert.match(h.diagnosticsError.value, /保存失败/); assert.equal(h.diagnosticsBusy.value, false);
});
test('diagnostics failed reread exposes both errors; subsequent success clears the error', async () => {
  const h = view('SettingsView', { setDiagnostics: async () => { throw Error('写入失败'); }, getDiagnostics: async () => { throw Error('读取失败'); } });
  h.diagnostics.value = { enabled: true }; const target = { checked: false };
  await h.onDiagnosticsChange({ target }); assert.match(h.diagnosticsError.value, /写入失败/); assert.match(h.diagnosticsError.value, /读取失败/);
  assert.equal(h.diagnostics.value, null); assert.equal(target.checked, true);
  h.api.setDiagnostics = async () => ({ enabled: false }); await h.onDiagnosticsChange({ target: { checked: false } });
  assert.equal(h.diagnosticsError.value, ''); assert.equal(h.diagnostics.value.enabled, false);
});

test('dangerous actions use visible app dialogs, not native confirm, and busy toggles are disabled', () => {
  for (const name of ['SettingsView', 'AdminConsoleView', 'TemplatesView']) {
    const source = read(`views/${name}.vue`);
    assert.doesNotMatch(source, /\bconfirm\s*\(/); assert.match(source, /<AppModal/); assert.match(source, /dialog-role="alertdialog"/);
  }
  for (const name of ['RulesView', 'TemplatesView']) assert.match(read(`views/${name}.vue`), /:disabled="togglingIds\.has\(/);
});

function updates(enabled, frequency) {
  const raw = read('composables/useVersionUpdate.ts'); const ast = ts.createSourceFile('updates.ts', raw, ts.ScriptTarget.Latest, true);
  const body = ast.statements.filter(n => !ts.isImportDeclaration(n)).map(n => n.getText(ast)).join('\n');
  const output = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const settings = vue.ref({ version_update: { enabled, check_frequency: frequency } });
  const mounts = [], intervals = new Map(); let timer = 0, calls = 0; const exports = {};
  vm.runInNewContext(output, { exports, ref: vue.ref, onMounted: fn => mounts.push(fn), onUnmounted() {}, watchEffect: fn => fn(() => {}),
    useClientSettings: () => ({ settings }), checkAppUpdate: async () => { calls++; return { has_update: false }; },
    setInterval: fn => { intervals.set(++timer, fn); return timer; }, clearInterval: id => intervals.delete(id), console });
  const state = exports.useVersionUpdate(); return { state, settings, mounts, intervals, calls: () => calls };
}
test('manual and disabled updates do not check on mount; explicit check always works', async () => {
  for (const [enabled, frequency, expected] of [[true, 'manual', 0], [false, 'daily', 0], [true, 'startup', 1], [true, 'daily', 1]]) {
    const h = updates(enabled, frequency); h.mounts.forEach(fn => fn()); await flush(); assert.equal(h.calls(), expected);
    await h.state.checkUpdate(true); assert.equal(h.calls(), expected + 1);
  }
});
test('already queued automatic checks respect a new manual-only preference', async () => {
  const h = updates(true, 'daily'); h.settings.value.version_update.check_frequency = 'manual';
  for (const tick of h.intervals.values()) tick(); await flush(); assert.equal(h.calls(), 0);
  await h.state.checkUpdate(true); assert.equal(h.calls(), 1);
});

test('desktop shell startup check also respects manual-only and disabled settings, while manual click works', async () => {
  for (const [enabled, frequency, expected] of [[true, 'manual', 0], [false, 'startup', 0], [true, 'startup', 1]]) {
    let calls = 0; const h = view('../layouts/AppShell', { checkAppUpdate: async () => { calls++; return { has_update: false }; }, inTauriRuntime: () => false });
    Object.assign(h.settings.value.version_update, { enabled, check_frequency: frequency });
    h.mounted.forEach(fn => fn());
    for (const fn of [...h.timers.values()]) fn(); await flush(); assert.equal(calls, expected);
    await h.runUpdateCheck(true); assert.equal(calls, expected + 1);
  }
});
test('desktop shell delayed startup check is cancelled on unmount and rechecks changed preferences', async () => {
  let calls = 0; const h = view('../layouts/AppShell', { checkAppUpdate: async () => { calls++; return { has_update: false }; }, inTauriRuntime: () => false });
  h.settings.value.version_update.check_frequency = 'startup'; h.mounted.forEach(fn => fn());
  h.settings.value.version_update.check_frequency = 'manual';
  for (const fn of [...h.timers.values()]) fn(); await flush(); assert.equal(calls, 0);
  for (const fn of h.unmounted) fn(); assert.equal(h.timers.size, 0);
  await h.runUpdateCheck(false); assert.equal(calls, 0);
});
test('switching to manual during an automatic shell check never opens update UI or starts installation', async () => {
  for (const stage of ['server', 'updater']) {
    const pending = deferred(); let installs = 0, tauriChecks = 0;
    const info = { has_update: true, latest_version: 'future' };
    const h = view('../layouts/AppShell', {
      checkAppUpdate: () => stage === 'server' ? pending.promise : Promise.resolve(info),
      inTauriRuntime: () => true,
      checkUpdateViaTauri: () => { tauriChecks++; return stage === 'updater' ? pending.promise : Promise.resolve({ available: true }); },
      runUpdateViaTauri: async () => { installs++; },
    });
    Object.assign(h.settings.value.version_update, { check_frequency: 'startup', auto_download: true });
    const task = h.runUpdateCheck(false); await flush();
    h.settings.value.version_update.check_frequency = 'manual';
    pending.resolve(stage === 'server' ? info : { available: true, version: 'future' }); await task;
    assert.equal(h.updateModalOpen.value, false); assert.equal(installs, 0); assert.equal(h.checkingUpdate.value, false);
    if (stage === 'server') assert.equal(tauriChecks, 0);
  }
});
