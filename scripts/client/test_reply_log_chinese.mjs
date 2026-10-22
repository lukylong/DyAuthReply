import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
const file = (p) => readFileSync(new URL('../../'+p, import.meta.url),'utf8');
function load(source) {
  const exports={};
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports});
  return exports;
}
const ui=file('dyauthreply-client/client-ui/src/views/LogsView.vue');
const view=load(file('dyauthreply-client/client-ui/src/views/logs-presentation.ts'));
const client=file('dyauthreply-client/client-ui/src/api/client.ts');
const labels=load(client.slice(client.indexOf('export function resultLabel('),client.indexOf('export function statusLabel(')));
test('reply log cooldown and guard skips use Chinese guidance and neutral styling',()=>{
 for(const result of ['cooldown','quota_exceeded','silent','skipped']) {
  assert.equal(view.resultTone(result),'muted');
  assert.doesNotMatch(view.reasonText('internal_reason',result),/[a-z]/i);
 }
 assert.equal(view.reasonTitle('cooldown'),'跳过原因');
 const text=view.reasonText('rule_cooldown','cooldown');
 assert.match(text,/冷却时间/);assert.match(text,/不会自动补发/);assert.doesNotMatch(text,/5 分钟/);
});
test('reply log uncertain partial and pending never masquerade as failed',()=>{
 for(const result of ['pending','partial','uncertain']) assert.equal(view.resultTone(result),'warn');
 assert.equal(view.resultTone('failed'),'bad');assert.equal(view.reasonTitle('failed'),'失败原因');
 assert.equal(view.reasonTitle('pending'),'等待原因');assert.equal(view.reasonTitle('uncertain'),'结果说明');
 assert.match(view.reasonText('unknown_reason','uncertain'),/勿重复发送/);
});
test('reply log preserves useful Chinese detail and numeric platform codes',()=>{
 const chinese='本条消息被平台拒绝，未送达（平台状态码：8101）';
 assert.equal(view.reasonText(chinese,'failed'),chinese);
 const raw=view.reasonText('BusinessRejected [http=200,business=Some(8101),msg=OK]','failed');
 assert.match(raw,/8101/);assert.doesNotMatch(raw,/[a-z]/i);
 assert.doesNotMatch(view.reasonText('BusinessRejected [business=Some(0)]','failed'),/状态码：0/);
 assert.equal(view.reasonText('', 'success'),'');
});
test('reply log all result labels have Chinese fallbacks',()=>{
 for(const result of ['success','failed','partial','uncertain','pending','cooldown','quota_exceeded','silent','skipped','future_status']) assert.doesNotMatch(labels.resultLabel(result),/[a-z]/i);
 assert.equal(labels.resultLabel('future_status'),'状态待确认');
 assert.equal(labels.resultLabel('partial'),'部分送达');
});
test('reply log list and details share reason formatting and severity',()=>{
 assert.ok(ui.includes('reasonText(log.error_message, log.result)'));
 assert.ok(ui.includes('reasonText(selected.error_message, selected.result)'));
 assert.ok(ui.includes('reasonTitle(selected.result)'));
 assert.ok(ui.includes('reason-${resultTone(log.result)}'));
 assert.ok(ui.includes('reason-${resultTone(selected.result)}'));
 assert.ok(!ui.includes("'error-text': !!log.error_message"));
 assert.ok(!ui.includes('class="message-section error-section"'));
 assert.match(ui,/reason-section\.reason-bad p/);
 assert.match(ui,/reason-section\.reason-muted p/);
});
