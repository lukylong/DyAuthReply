import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../dyauthreply-client/client-ui/src/composables/browserComponentManager.ts', import.meta.url), 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const view = (state = 'ready', installed = true) => ({ state, source: 'managed', version: 'fixture-1', managed_installed: installed, can_rollback: false, message: '', downloaded_bytes: 0, total_bytes: 0 });
function harness(component = view()) {
  const timers = new Map(); let next = 0;
  const exports = {};
  vm.runInNewContext(output, { exports, require, setTimeout: fn => { timers.set(++next, fn); return next; }, clearTimeout: id => timers.delete(id) });
  const calls = [];
  const api = { status: async () => component, current: async () => null };
  for (const action of ['install', 'repair', 'rollback', 'uninstall']) api[action] = async () => { calls.push(action); return action === 'uninstall' ? view('missing', false) : view('installing'); };
  const manager = exports.createBrowserComponentManager(api);
  const tick = async () => { const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); await new Promise(setImmediate); };
  return { manager, api, timers, calls, tick };
}
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

test('browser management installs missing component and completes without opening a login window', async () => {
  const h = harness(view('missing', false)); await h.manager.refresh();
  assert(h.manager.can('install')); assert(!h.manager.can('uninstall'));
  await h.manager.act('install'); assert.deepEqual(h.calls, ['install']);
  assert(!h.manager.can('repair')); assert.equal(h.timers.size, 1);
  h.api.status = async () => view(); await h.tick();
  assert.equal(h.manager.state.component.state, 'ready'); assert.equal(h.timers.size, 0);
  assert(h.manager.state.notice.includes('安装完成'));
  h.manager.dispose();
});
test('same-version repair uses repair API, never install short circuit', async () => {
  const h = harness(); await h.manager.refresh();
  assert(!h.manager.can('install')); assert(h.manager.can('repair'));
  await h.manager.act('repair'); assert.deepEqual(h.calls, ['repair']); h.manager.dispose();
});
test('repair keeps the backend system recommendation and switches to the returned installed version', async () => {
  const recommendation = { recommended_version: '152.0.7977.169', selection_reason: '检测到 Windows build 19045，自动选择兼容版浏览器' };
  const h = harness({ ...view(), version: '155.0.8059.79', ...recommendation });
  await h.manager.refresh();
  assert.equal(h.manager.state.component.recommended_version, recommendation.recommended_version);
  h.api.repair = async () => { h.calls.push('repair'); return { ...view('installing'), ...recommendation }; };
  await h.manager.act('repair');
  assert.deepEqual(h.calls, ['repair']);
  assert.equal(h.manager.state.component.selection_reason, recommendation.selection_reason);
  h.api.status = async () => ({ ...view(), version: recommendation.recommended_version, ...recommendation });
  await h.tick();
  assert.equal(h.manager.state.component.version, recommendation.recommended_version);
  assert.equal(h.manager.state.component.state, 'ready');
  h.manager.dispose();
});
test('active login and in-progress install block all destructive actions', async () => {
  for (const mode of ['login', 'install']) {
    const h = harness(view(mode === 'install' ? 'installing' : 'ready'));
    if (mode === 'login') h.api.current = async () => ({ state: 'collecting' });
    await h.manager.refresh();
    for (const action of ['install', 'repair', 'rollback', 'uninstall']) { assert(!h.manager.can(action)); await h.manager.act(action); }
    assert.equal(h.calls.length, 0); h.manager.dispose(); assert.equal(h.timers.size, 0);
  }
});
test('uninstall only calls component endpoint and accurately shows preserved-data feedback', async () => {
  const h = harness(); await h.manager.refresh(); await h.manager.act('uninstall');
  assert.deepEqual(h.calls, ['uninstall']); assert.equal(h.manager.installed(), false);
  assert(h.manager.state.notice.includes('登录资料、账号和回复规则均已保留'));
});
test('failed reads disable actions and failed operations expose error without success feedback', async () => {
  const h = harness(); h.api.current = async () => { throw Error('unreachable'); }; await h.manager.refresh();
  assert(!h.manager.can('repair')); assert(h.manager.state.error.includes('unreachable'));
  h.api.current = async () => null; await h.manager.refresh();
  h.api.repair = async () => { throw Error('component busy'); }; await h.manager.act('repair');
  assert(h.manager.state.error.includes('component busy')); assert.equal(h.manager.state.notice, '');
});
test('stale reads and unmounted operations never replace newer state or restart polling', async () => {
  const h = harness(); const old = deferred(), latest = deferred(); let calls = 0;
  h.api.status = () => (++calls === 1 ? old.promise : latest.promise);
  const first = h.manager.refresh(), second = h.manager.refresh();
  latest.resolve(view('corrupt')); await second;
  old.resolve(view()); await first; assert.equal(h.manager.state.component.state, 'corrupt');
  const action = deferred(); h.api.repair = () => action.promise;
  const pending = h.manager.act('repair'); h.manager.dispose(); action.resolve(view('installing')); await pending;
  assert.equal(h.manager.state.component.state, 'corrupt'); assert.equal(h.timers.size, 0);
});
test('system browser is never offered for uninstall; previous component can be rolled back', async () => {
  const h = harness({ ...view(), managed_installed: false, source: 'development_browser' }); await h.manager.refresh();
  assert(!h.manager.can('uninstall')); assert(!h.manager.can('repair')); assert(h.manager.can('install'));
  h.api.status = async () => ({ ...view(), can_rollback: true }); await h.manager.refresh(); assert(h.manager.can('rollback'));
});
test('settings and failed quick login expose management entry, uninstall requires explicit modal', () => {
  const read = path => readFileSync(new URL(`../../dyauthreply-client/client-ui/src/${path}`, import.meta.url), 'utf8');
  assert(read('views/SettingsView.vue').includes('<BrowserComponentPanel'));
  assert(read('views/AccountsView.vue').includes('to="/settings?tab=browser"'));
  const panel = read('components/BrowserComponentPanel.vue');
  assert(panel.includes('title="卸载内置浏览器？"')); assert(panel.includes('确认卸载')); assert(panel.includes('confirmUninstall = true'));
  assert(!panel.includes('startQuickAuth'));
  assert(panel.includes('state.component.recommended_version'));
  assert(panel.includes('state.component.selection_reason'));
  assert(panel.includes('点击“修复重装”会自动换成适配版本'));
  const api = read('api/client.ts');
  for (const action of ['repair', 'uninstall']) assert(api.includes(`/douyin/quick-auth/component/${action}`));
});
