import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
const vue = require('vue');
const source = readFileSync(new URL('../../dyauthreply-client/client-ui/src/views/CardsView.vue', import.meta.url), 'utf8');
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const ast = ts.createSourceFile('CardsView.ts', script, ts.ScriptTarget.Latest, true);
const body = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join('\n');
const output = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const card = { id: 'fixture-a', title: '测试卡片 A', status: true };
const other = { id: 'fixture-b', title: '测试卡片 B', status: true };

function harness() {
  const calls = [];
  const license = vue.ref({ can_use_business: true, state_label: '已激活' });
  const api = { deleteCard: async () => ({ success: true }) };
  const context = vm.createContext({
    ref: vue.ref, onMounted: () => {},
    useClientLicense: () => ({ licenseStatus: license, ensureStatus: async () => license.value }),
    listCards: async () => ({ items: [card, other] }),
    deleteCard: async id => { calls.push(id); return api.deleteCard(id); },
    // Reproduce a webview with no native confirmation UI. No real API or user data.
    confirm: () => false,
  });
  const state = vm.runInContext(`${output}; ({ cards, error, removeCard,
    deleteTarget: typeof deleteTarget === 'undefined' ? undefined : deleteTarget,
    deleting: typeof deleting === 'undefined' ? undefined : deleting,
    deleteError: typeof deleteError === 'undefined' ? undefined : deleteError,
    notice: typeof notice === 'undefined' ? undefined : notice,
    closeDelete: typeof closeDelete === 'undefined' ? undefined : closeDelete,
    confirmDelete: typeof confirmDelete === 'undefined' ? undefined : confirmDelete,
  })`, context);
  state.cards.value = [card, other];
  return { ...state, license, calls, api };
}

test('delete click opens an in-app confirmation even without native confirm support', async () => {
  const h = harness(); await h.removeCard(card);
  assert.equal(h.deleteTarget?.value?.id, card.id);
  assert.deepEqual(h.calls, []);
  assert.equal(h.cards.value.length, 2);
});

test('cancel leaves both cards untouched and sends no delete request', () => {
  const h = harness(); h.removeCard(card); h.closeDelete();
  assert.equal(h.deleteTarget.value, null);
  assert.deepEqual(h.calls, []); assert.equal(h.cards.value.length, 2);
});

test('explicit confirmation removes only the selected card after acknowledged success', async () => {
  const h = harness(); h.removeCard(card); await h.confirmDelete();
  assert.deepEqual(h.calls, [card.id]);
  assert.deepEqual(h.cards.value.map(c => c.id), [other.id]);
  assert.equal(h.deleteTarget.value, null); assert.equal(h.deleting.value, false);
  assert.match(h.notice.value, /已删除卡片/); assert.match(h.notice.value, /测试卡片 A/);
});

test('pending deletion blocks repeated requests, target changes and closing', async () => {
  const h = harness(); let finish;
  h.api.deleteCard = () => new Promise(resolve => { finish = resolve; });
  h.removeCard(card); const pending = h.confirmDelete();
  assert.equal(h.deleting.value, true); assert.equal(h.cards.value.length, 2);
  h.closeDelete(); h.removeCard(other); await h.confirmDelete();
  assert.equal(h.deleteTarget.value.id, card.id); assert.deepEqual(h.calls, [card.id]);
  finish({ success: true }); await pending;
  assert.deepEqual(h.cards.value.map(c => c.id), [other.id]);
});

test('rule binding and sync/network errors remain visible in the modal, preserving rows for retry', async () => {
  for (const message of ['卡片被回复规则引用，请先解除关联', '公网卡片删除尚未同步，请重试', '网络连接失败']) {
    const h = harness(); h.api.deleteCard = async () => { throw new Error(message); };
    h.removeCard(card); await h.confirmDelete();
    assert.match(h.deleteError.value, new RegExp(message));
    assert.equal(h.error.value, ''); assert.equal(h.notice.value, '');
    assert.equal(h.deleteTarget.value.id, card.id); assert.equal(h.cards.value.length, 2);
    assert.equal(h.deleting.value, false);
    h.api.deleteCard = async () => ({ success: true }); await h.confirmDelete();
    assert.equal(h.deleteError.value, ''); assert.equal(h.cards.value.length, 1);
  }
});

test('unacknowledged deletion never reports success or removes a card', async () => {
  const h = harness(); h.api.deleteCard = async () => ({ success: false });
  h.removeCard(card); await h.confirmDelete();
  assert.equal(h.cards.value.length, 2); assert.equal(h.notice.value, '');
  assert.match(h.deleteError.value, /未确认/);
});

test('authorization is checked both before opening and again before deletion', async () => {
  const h = harness(); h.license.value.can_use_business = false;
  h.removeCard(card); assert.equal(h.deleteTarget.value, null); assert.match(h.error.value, /授权/);
  h.license.value.can_use_business = true; h.removeCard(card);
  h.license.value.can_use_business = false; await h.confirmDelete();
  assert.deepEqual(h.calls, []); assert.equal(h.cards.value.length, 2);
  assert.match(h.deleteError.value, /授权/); assert.equal(h.deleting.value, false);
});

test('no selected card is a no-op and a new dialog clears the previous error', async () => {
  const h = harness(); await h.confirmDelete(); assert.deepEqual(h.calls, []);
  h.removeCard(card); h.deleteError.value = 'old failure'; h.closeDelete(); h.removeCard(other);
  assert.equal(h.deleteError.value, ''); assert.equal(h.deleteTarget.value.id, other.id);
});

test('template wires app modal, cancel, explicit confirmation, pending and error feedback', () => {
  assert.doesNotMatch(script, /\bconfirm\s*\(/);
  assert.match(source, /<AppModal[\s\S]*?title="删除卡片"[\s\S]*?dialog-role="alertdialog"/);
  assert.match(source, /@close="closeDelete"/);
  assert.match(source, /@click="confirmDelete"/);
  assert.match(source, /v-if="deleteError"[^>]*role="alert"/);
  assert.match(source, /deleting \? '删除中…' : '确认删除'/);
});
