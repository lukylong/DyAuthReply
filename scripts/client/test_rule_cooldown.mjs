import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
const vue = require('vue');
const { parse, compileScript } = require('@vue/compiler-sfc');
const path = process.env.COOLDOWN_SOURCE || new URL('../../dyauthreply-client/client-ui/src/views/RulesView.vue', import.meta.url);
const source = readFileSync(path, 'utf8');
const { descriptor } = parse(source);
const ast = ts.createSourceFile('RulesView.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true);
const body = ast.statements.filter(n => !ts.isImportDeclaration(n)).map(n => n.getText(ast)).join('\n');
const output = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const rule = { id: 'fixture-rule', name: '测试规则', match_type: 'default', status: true, cooldown_seconds: 1800 };

function harness(t) {
  const calls = [];
  const scope = vue.effectScope();
  t.after(() => scope.stop());
  const api = { conflict: false };
  const save = async (id, payload) => {
    calls.push({ id, ...payload });
    if (api.conflict && !payload.force_move) throw new Error('fixture conflict');
  };
  const context = vm.createContext({
    ref: vue.ref, computed: vue.computed, watch: vue.watch,
    onMounted() {}, onUnmounted() {}, onBeforeRouteLeave() {},
    document: { addEventListener() {}, removeEventListener() {} },
    useClientLicense: () => ({ licenseStatus: vue.ref({ can_use_business: true }), ensureStatus: async () => {} }),
    listRules: async () => ({ items: [] }),
    listRulesByAccount: async () => [],
    createRule: payload => save(null, payload),
    patchRule: save,
    parseAccountConflict: () => api.conflict ? [{ account_id: 'fixture-account' }] : null,
  });
  const state = scope.run(() => vm.runInContext(`${output}; ({
    form, formError, pendingConflicts, openCreate, openEdit, submitForm, confirmMoveAccounts, COOLDOWN_PRESETS,
    customCooldownHours: typeof customCooldownHours === 'undefined' ? undefined : customCooldownHours,
    selectCooldownPreset: typeof selectCooldownPreset === 'undefined' ? undefined : selectCooldownPreset,
    setCustomCooldownHours: typeof setCustomCooldownHours === 'undefined' ? undefined : setCustomCooldownHours,
  })`, context));
  state.openCreate();
  state.form.value.name = '测试规则';
  state.form.value.match_type = 'default';
  return { ...state, calls, api };
}

test('presets include 6 and 12 hours and no longer offer 30 minutes', t => {
  const h = harness(t);
  assert.deepEqual(Array.from(h.COOLDOWN_PRESETS, p => [p.label, p.value]), [
    ['不限', 0], ['5 分钟', 300], ['1 小时', 3600], ['6 小时', 21600], ['12 小时', 43200],
  ]);
});

test('custom decimal hours are converted to integer seconds in create payloads', async t => {
  for (const [hours, seconds] of [['0.5', 1800], ['1.5', 5400], ['6', 21600], ['12', 43200], ['0', 0], ['0.333', 1199]]) {
    const h = harness(t);
    h.setCustomCooldownHours(hours);
    await h.submitForm();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].cooldown_seconds, seconds);
  }
});

test('new rules keep the five-minute default without a repeating decimal in the custom field', async t => {
  const h = harness(t);
  assert.equal(h.customCooldownHours.value, '');
  await h.submitForm();
  assert.equal(h.calls[0].cooldown_seconds, 300);
});

test('editing a legacy 30-minute rule displays 0.5 hours and saves 1800 seconds', async t => {
  const h = harness(t);
  h.openEdit(rule);
  assert.equal(h.customCooldownHours.value, '0.5');
  await h.submitForm();
  assert.equal(h.calls[0].id, rule.id);
  assert.equal(h.calls[0].cooldown_seconds, 1800);
});

test('existing second-based values remain unchanged when opened and saved', async t => {
  for (const seconds of [0, 1, 60, 299, 300, 1800, 3600, 21600, 43200, 86399]) {
    const h = harness(t);
    h.openEdit({ ...rule, cooldown_seconds: seconds });
    await h.submitForm();
    assert.equal(h.calls[0].cooldown_seconds, seconds);
  }
});

test('selecting any preset clears custom input and saves the selected seconds', async t => {
  const h = harness(t);
  for (const preset of h.COOLDOWN_PRESETS) {
    h.setCustomCooldownHours('2.5');
    h.selectCooldownPreset(preset.value);
    assert.equal(h.customCooldownHours.value, '');
    await h.submitForm();
    assert.equal(h.calls.at(-1).cooldown_seconds, preset.value);
  }
});

test('editing a custom interval and then creating a new rule resets custom state', t => {
  const h = harness(t);
  h.openEdit(rule);
  h.openCreate();
  assert.equal(h.customCooldownHours.value, '');
  assert.equal(h.form.value.cooldown_seconds, 300);
});

test('blank, negative, nonfinite and sub-second custom values never submit as unlimited', async t => {
  for (const value of ['', ' ', '-1', '-0.00001', 'Infinity', 'NaN', 'abc', '1e100', '0.00001']) {
    const h = harness(t);
    h.setCustomCooldownHours(value);
    await h.submitForm();
    assert.equal(h.calls.length, 0, value);
    assert.match(h.formError.value, /小时/);
  }
});

test('selecting a preset recovers from invalid custom input', async t => {
  const h = harness(t);
  h.setCustomCooldownHours('');
  await h.submitForm();
  h.selectCooldownPreset(21600);
  await h.submitForm();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].cooldown_seconds, 21600);
  assert.equal(h.formError.value, '');
});

test('account-conflict confirmation preserves hours-to-seconds conversion', async t => {
  const h = harness(t);
  h.api.conflict = true;
  h.setCustomCooldownHours('0.5');
  await h.submitForm();
  assert.equal(h.pendingConflicts.value.length, 1);
  await h.confirmMoveAccounts();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].force_move, true);
  assert.equal(h.calls[1].cooldown_seconds, 1800);
});

test('actual Vue template compiles with hour labeling, decimal input and an accessible hint', () => {
  assert.doesNotThrow(() => compileScript(descriptor, { id: 'cooldown-test', inlineTemplate: true }));
  assert.match(source, /@click="selectCooldownPreset\(p.value\)"/);
  assert.match(source, /:value="customCooldownHours"/);
  assert.match(source, /@input="setCustomCooldownHours/);
  assert.match(source, /step="any"/);
  assert.match(source, /自定义（小时）/);
  assert.match(source, /aria-describedby="cooldown-hours-hint"/);
  assert.match(source, /0\.5 小时 = 30 分钟/);
  assert.match(source, /0 表示不限/);
  assert.doesNotMatch(source, /自定义秒数/);
});

test('preset layout wraps rather than overflowing when two hour presets are added', () => {
  assert.match(source, /\.cooldown-presets\s*\{[^}]*flex-wrap:\s*wrap/);
});

test('custom hours input shares the preset row instead of occupying a separate row', () => {
  const { baseParse } = require('@vue/compiler-dom');
  const template = baseParse(descriptor.template.content);
  const findByClass = (node, name) => {
    if (node.props?.some(p => p.name === 'class' && p.value?.content.split(' ').includes(name))) return node;
    return node.children?.map(child => findByClass(child, name)).find(Boolean);
  };
  const presets = findByClass(template, 'cooldown-presets');
  const custom = findByClass(presets, 'cooldown-custom-wrap');
  assert.ok(custom, 'custom input must be inside the wrapping preset row');
  assert.equal(custom.tag, 'label');
  const input = custom.children.find(child => child.tag === 'input');
  assert.ok(input.props.some(p => p.name === 'aria-label' && p.value?.content === '自定义（小时）'));
  assert.match(source, /class="cooldown-unit"[^>]*aria-hidden="true">小时/);
  assert.doesNotMatch(source, /cooldown-custom-row/);
});

test('compact input has a bounded width and the hint stays concise', () => {
  assert.match(source, /\.cooldown-custom-wrap\s*\{[^}]*flex:\s*0 0 108px/);
  assert.match(source, /\.cooldown-presets \.preset-btn\s*\{[^}]*height:\s*32px/);
  const hint = source.match(/<p id="cooldown-hours-hint"[^>]*>([\s\S]*?)<\/p>/)[1].trim();
  assert.ok(hint.length <= 40, `hint should be short, got ${hint.length} characters`);
  assert.match(hint, /0\.5 小时 = 30 分钟/);
});
