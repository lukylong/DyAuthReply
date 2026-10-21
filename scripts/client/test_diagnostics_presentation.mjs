import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/apps/web-ele/src/views/license/diagnostics/presentation.ts', import.meta.url), 'utf8');
const output = ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const exports = {}; vm.runInNewContext(output,{exports,Date,Intl,Number,Object,String,Math});
const now=Date.parse('2026-10-17T11:00:00+08:00');
const healthy={account:'anonymous',lifecycle:'running',inbound:'ws_healthy',send:'sendable',ownership:'owned',identity_verified:true};
const device={last_report_at:'2026-10-17T10:59:00',runtime_phase:'running',accounts:[healthy],warn_24h:0,error_24h:0};
test('diagnostic context distinguishes mutual follow, segment results and operator changes',()=>{
 const queued={message:'auto-reply queued',fields:{trigger_type:'mutual_follow',trace_id:'0123456789abcdef'}};
 assert.match(exports.eventMessage(queued),/回关|互关/);
 assert.match(exports.eventAdvice(queued),/入队.*不等于|不代表.*送达/);
 const failed={message:'native send was not delivered',fields:{classification:'BusinessRejected',response_codes:'http=200,business=Some(8101)',segment_index:2,segment_count:3,content_kind:'link'}};
 assert.match(exports.eventFields(failed),/链接/); assert.match(exports.eventAdvice(failed),/8101/);
 assert.doesNotMatch(exports.eventAdvice(failed),/账号已被封禁/);
 assert.match(exports.eventMessage({message:'account automatic policy changed',fields:{enabled:false}}),/关闭|停用/);
 assert.match(exports.eventFields({fields:{change_source:'emergency_stop',reason:'inbound_spool_full'}}),/管理急停/);
 assert.match(exports.eventFields({fields:{change_source:'emergency_stop',reason:'inbound_spool_full'}}),/缓冲/);
 assert.match(exports.eventAdvice({message:'receive reconciliation failed',fields:{}}),/未上报/);
 assert.equal(exports.eventCategory({message:'hosted authorization suspended; awaiting license rotation',target:'dy_agent::control_plane'}),'授权与托管');
});
test('fresh complete snapshot is readable and counters do not claim current failures',()=>{
 const v=exports.summarizeDevice({...device,error_24h:20,runtime:{signer_failed:10,queue_rejected:9}},now);
 assert.equal(v.label,'运行正常');assert.equal(v.receiving,1);assert.equal(v.automatic,1);
 assert.equal(exports.stateView('ws_healthy').label,'实时接收正常');
});
test('stale reports and invalid clocks never pretend to be online',()=>{
 for(const time of ['2026-10-17T10:00:00','2026-10-18T12:00:00',null,'bad']){
  const v=exports.summarizeDevice({...device,last_report_at:time},now);assert.equal(v.label,'状态待更新');assert.notEqual(v.tone,'success');
 }
 assert.equal(exports.reportedMillis('2026-10-17 10:59:00'),Date.parse('2026-10-17T10:59:00+08:00'));
});
test('send pending, receive reconnect and identity unknown are separate from auto pause',()=>{
 for(const patch of [{send:'unknown'},{inbound:'backoff'},{identity_verified:false},{ownership:'lost'}]){
  assert.equal(exports.summarizeDevice({...device,accounts:[{...healthy,...patch}]},now).label,'需要关注');
 }
 const paused=exports.summarizeDevice({...device,accounts:[{...healthy,lifecycle:'paused_auto'}]},now);
 assert.equal(paused.label,'运行正常');assert.equal(paused.automatic,0);assert.equal(paused.receiving,1);
});
test('unknown states and missing metrics remain unknown, events show Chinese context',()=>{
 assert.match(exports.stateView('future_state').label,/未识别/);assert.equal(exports.metric(null),'—');
 assert.equal(exports.moduleLabel('dy_agent::protocol::im'),'平台通信');
 assert.match(exports.eventFields({fields:{reason:'rule_cooldown',attempt:1}}),/规则仍在冷却期/);
 assert.equal(exports.summarizeDevice({...device,accounts:[{account:'incomplete'}]},now).label,'需要关注');
});
test('client deletion UI checks explicit bindings and remains blocked while loading',()=>{
 const ui=readFileSync(new URL('../../dyauthreply-client/client-ui/src/views/AccountsView.vue',import.meta.url),'utf8');
 assert.ok(ui.includes('listRulesByAccount(account.id)'));
 assert.ok(ui.includes('rule.account_ids?.includes(account.id)'));
 assert.ok(ui.includes('checkingDelete || deleteBindings.length > 0'));
 assert.ok(ui.includes('停用规则不等于解绑'));
});
test('enabled auto reply is not misrepresented as verified sending',()=>{
 assert.match(exports.automaticView({...healthy,send:'unknown'}).label,/发送待验证/);
 assert.match(exports.automaticView({...healthy,inbound:'backoff'}).label,/接收需关注/);
 assert.equal(exports.automaticView(healthy).label,'已启用，等待消息');
});
test('risk classification outranks generic 8101 advice; missing metadata is never inferred',()=>{
 assert.match(exports.eventAdvice({message:'platform risk control rejected the send; sending paused until recheck',fields:{classification:'RiskControlled',response_codes:'business=Some(8101),raw_check=Some(2)'}}),/暂停/);
 assert.match(exports.eventMessage({message:'auto-reply queued',fields:{}}),/未上报/);
 assert.equal(exports.traceRef({fields:{trace_id:'0123456789abcdef'}}),'0123456789abcdef');
 assert.equal(exports.traceRef({fields:{trace_id:'private-token'}}),'');
 assert.match(exports.eventAdvice({message:'quick-auth session failed',fields:{error:'登录窗口已关闭'}}),/主动关闭可忽略/);
 const view=readFileSync(new URL('../../web/apps/web-ele/src/views/license/diagnostics/index.vue',import.meta.url),'utf8');
 assert.ok(view.includes('row.received_at'));assert.ok(view.includes('showTrace(row)'));
});
test('business codes are concise Chinese labels while raw events are preserved',()=>{
 const event={fields:{response_codes:'http=200,outer=Some(0),business=Some(8101),raw_check=Some(1),msg=OK'}};
 const text=exports.eventFields(event);assert.match(text,/业务码 8101/);assert.match(text,/检查码 1/);assert.doesNotMatch(text,/Some\(/);
 assert.ok(event.fields.response_codes.includes('Some(8101)'));
});
