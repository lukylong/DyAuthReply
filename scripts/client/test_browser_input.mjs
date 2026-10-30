import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from '../../dyauthreply-client/client-ui/node_modules/typescript/lib/typescript.js';
const root = process.env.TEST_REPO_ROOT ? new URL(`file://${process.env.TEST_REPO_ROOT}/`) : new URL('../../', import.meta.url);
const read = (path) => readFileSync(new URL(path, root), 'utf8');

test('input preparation is advisory while origin validation remains mandatory', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/native_commands.rs');
  const block = source.slice(source.indexOf('pub async fn native_request('), source.indexOf('pub async fn native_request_image('));
  assert(block.indexOf('require_local(&window)?') < block.indexOf('needs_english_input'));
  assert(block.includes('require_local(&window)?'));
  assert(block.includes('crate::input_policy::start_after_attempt('));
  assert(block.indexOf('prepare(&window)') < block.indexOf('|| host.request('));
  assert(!block.includes('prepare(&window).await?'));
  const guard = read('dyauthreply-client/desktop/src-tauri/src/browser_input.rs');
  assert(guard.includes('method == "POST" && path == "/api/client/v1/douyin/quick-auth/start"'));
  assert(guard.includes('.run_on_main_thread('));
  assert(guard.includes('windows_input::confirmed(&pending)?'));
  assert(guard.includes('for _ in 0..20'));
  assert(guard.includes('send.is_closed()'));
  assert(guard.includes('from_secs(2), receive'));
  assert(guard.includes('#[cfg(not(target_os = "windows"))]'));
});

test('manual English is respected without exact KLID name validation', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/windows_input.rs');
  assert(!source.includes('GetKeyboardLayoutNameW'));
  assert(source.indexOf('if english_keyboard(GetKeyboardLayout(focus_thread))') < source.indexOf('let layout ='));
  assert(!source.includes('fn ImmIsIME('));
  assert(source.includes('english_language(layout as usize)'));
  assert(source.includes('GetForegroundWindow() != hwnd'));
  assert(source.includes('window_thread != GetCurrentThreadId()'));
  assert(source.includes('GetKeyboardLayout(pending.focus_thread)'));
  assert(source.includes('PostMessageW(focus, WM_INPUTLANGCHANGEREQUEST'));
  assert(!source.includes('SystemParametersInfoW'));
  assert(!source.includes('RegSetValue'));
  assert(!source.includes('SendInput'));
});

test('advisory policy always calls start and preserves actual launch failures', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/input_policy.rs');
  const policy = source.slice(0, source.indexOf('#[cfg(test)]'));
  assert(policy.includes('if let Err(warning) = prepare.await'));
  assert(policy.includes('warn(warning);'));
  assert(policy.includes('start().await'));
  assert(!policy.includes('prepare.await?'));
});

test('failed preflight can be retried without an existing session and explains input change', () => {
  const source = read('dyauthreply-client/client-ui/src/views/AccountsView.vue');
  assert(source.includes('!quickAuth && quickAuthError && quickComponent?.state === \'ready\''));
  assert(source.includes('英文（美国）键盘'));
});

function quickStart(isDesktop, request, loadTauri) {
  const source = read('dyauthreply-client/client-ui/src/api/client.ts');
  const fn = source.slice(source.indexOf('export async function startQuickAuth('), source.indexOf('export function getQuickAuthSession('))
    .replace('export async function', 'async function')
    .replace("await import('@tauri-apps/api/core')", 'await loadTauri()');
  const js = ts.transpileModule(fn, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function('isTauriRuntime', 'request', 'loadTauri', `${js}; return startQuickAuth;`)(() => isDesktop, request, loadTauri);
}

test('manual launch never calls keyboard APIs and explicitly bypasses native preparation', async () => {
  let count = 0;
  const start = quickStart(true, async (path, init, admin, skip) => {
    count++;
    assert.equal(path, '/douyin/quick-auth/start');
    assert.equal(skip, true);
    assert.equal(admin, false);
    assert.equal(init.method, 'POST');
    return { session_id: 'manual-session' };
  }, () => { throw new Error('Manual mode must not load input commands'); });
  const result = await start('create', undefined, false);
  assert.equal(count, 1);
  assert.equal(result.input_preparation.state, 'manual');
});

test('automatic preparation is reported and never repeated in native_request', async () => {
  const order = [];
  const start = quickStart(true, async (_path, _init, _admin, skip) => {
    order.push('start'); assert.equal(skip, true); return { session_id: 'auto-session' };
  }, async () => ({ invoke: async (command) => {
    assert.equal(command, 'prepare_quick_auth_input'); order.push('prepare');
    return { state: 'ready', message: 'English ready' };
  } }));
  const result = await start('create');
  assert.deepEqual(order, ['prepare', 'start']);
  assert.equal(result.input_preparation.state, 'ready');
});

test('preparation invocation errors remain advisory and real launch errors propagate', async () => {
  let starts = 0;
  const load = async () => ({ invoke: async () => { throw new Error('os error 0'); } });
  const start = quickStart(true, async () => { starts++; return { session_id: 'fallback' }; }, load);
  assert.equal((await start('create')).input_preparation.state, 'unconfirmed');
  assert.equal(starts, 1);
  const failing = quickStart(true, async () => { throw new Error('browser exit 3'); }, load);
  await assert.rejects(failing('create'), /browser exit 3/);
});

test('input controls and native command remain scoped to trusted interactive windows', () => {
  const commands = read('dyauthreply-client/desktop/src-tauri/src/native_commands.rs');
  const report = commands.slice(commands.indexOf('pub async fn prepare_quick_auth_input('), commands.indexOf('pub async fn native_request_image('));
  assert(report.includes('require_local(&window)?'));
  assert(commands.includes('!skip_browser_input.unwrap_or(false)'));
  const view = read('dyauthreply-client/client-ui/src/views/AccountsView.vue');
  assert(view.includes('quickAuthAutomaticInput.value'));
  assert(view.includes('quickAuthInputStatus.value = quickAuth.value.input_preparation'));
  assert(view.includes('role="status"'));
});
