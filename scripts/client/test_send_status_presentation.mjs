import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../../dyauthreply-client/client-ui/package.json', import.meta.url));
const ts = require('typescript');
function load(name, functions) {
  const raw=readFileSync(new URL(`../../dyauthreply-client/client-ui/src/views/${name}.vue`,import.meta.url),'utf8');
  const source=ts.createSourceFile('view.ts',raw.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1],ts.ScriptTarget.Latest,true);
  const picked=source.statements.filter(n=>ts.isFunctionDeclaration(n)&&functions.includes(n.name?.text)).map(n=>n.getText(source)).join('\n');
  const js=ts.transpileModule(picked,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
  return new Function('hasUnavailableLease','statusLabel',`${js};return {${functions.join(',')}}`)(a=>['lost','expired'].includes(a.runtime_state?.ownership),()=> '检测中');
}
const chat=load('ChatView',['platformReason','humanizeSendError']);
function utility(name) {
 const source=readFileSync(new URL(`../../dyauthreply-client/client-ui/src/utils/${name}.ts`,import.meta.url),'utf8');
 const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 const exports={};new Function('exports','require',js)(exports,spec=>utility(spec.replace('./','')));return exports;
}
const accounts=utility('accountStatus');
const summary=accounts.accountStatusPresentation;
const details=accounts.accountStatusDetails;
const sending=a=>details(a).find(row=>row.label==='消息发送').value;
const detail='RiskControlled [http=200,outer=Some(0),business=Some(7911),raw_check=Some(2),msg=OK,biz_msg=系统繁忙，重新登录后可以正常使用私信功能]';
test('7911 shows the business explanation rather than outer OK',()=>{
 assert.match(chat.humanizeSendError(detail),/系统繁忙/);
 assert.doesNotMatch(chat.humanizeSendError(detail),/抖音提示：OK/);
});
test('7467 remains a recipient restriction even in historical risk records',()=>{
 const s=chat.humanizeSendError(detail.replace('7911','7467').replace('系统繁忙，重新登录后可以正常使用私信功能','给对方发送的消息已达上限'));
 assert.match(s,/该收件人/);assert.doesNotMatch(s,/该账号已暂停|风控拒绝/);
});
test('cooldown never promises that recovery occurs in 30 minutes',()=>{
 assert.doesNotMatch(chat.humanizeSendError(detail),/30 分钟内自动复查/);
 assert.match(chat.humanizeSendError(detail),/不代表|不保证/);
});
test('list says online while detail preserves the distinction between unverified and past successful sends',()=>{
 const a={status:1,identity_verified:true,credential_state:'unknown'};
 assert.equal(summary(a).label,'在线'); assert.match(sending(a),/发送待验证/);
 assert.match(summary(a).description,/不代表/);
 const sent={...a,credential_state:'sendable'};
 assert.equal(summary(sent).label,'在线'); assert.match(sending(sent),/上次发送成功/);
 assert.match(sending(sent),/实际回执/);
});
test('identity expiry and actual send restriction remain distinct in detail',()=>{
 assert.equal(summary({credential_state:'invalid'}).label,'离线');
 const restricted={status:1,identity_verified:true,credential_state:'receive_only',last_probe_error:'客户端协议发送被平台拒绝'};
 assert.equal(summary(restricted).label,'受限'); assert.equal(summary(restricted).tone,'warning');
 assert.match(sending(restricted),/发送受限/);
 assert.match(sending({...restricted,last_probe_error:'发送凭证不完整'}),/更新登录/);
});
test('list status and colour have only three consistent states and stale send success is never online',()=>{
 const cases=[
  [null,'离线'], [{},'离线'],
  [{status:0,credential_state:'sendable'},'离线'],
  [{status:2,identity_verified:true,credential_state:'sendable'},'离线'],
  [{status:3,identity_verified:true,credential_state:'sendable'},'受限'],
  [{status:1,identity_verified:false,credential_state:'sendable'},'离线'],
  [{status:1,identity_verified:true,identity_error:'校验失败'},'离线'],
  [{status:1,identity_verified:true,runtime_state:{ownership:'expired'}},'离线'],
  [{status:1,identity_verified:true,runtime_state:{ownership:'lost'}},'离线'],
  [{status:1,identity_verified:true,runtime_state:{send:'risk_controlled'}},'受限'],
  [{status:1,identity_verified:true,credential_state:'receive_only'},'受限'],
  [{status:1,identity_verified:true,credential_state:'unknown'},'在线'],
 ];
 for(const [a,label] of cases){ assert.equal(summary(a).label,label); assert.equal(summary(a).tone,{'在线':'success','离线':'muted','受限':'warning'}[label]); }
});
test('offline lease recovery is not called an expired Douyin login and automatic sending stays independent',()=>{
 const a={status:1,identity_verified:true,credential_state:'sendable',auto_reply_enabled:false,runtime_state:{ownership:'lost',inbound:'ws_healthy'}};
 assert.match(summary(a).description,/不等于抖音登录已失效/);
 assert.equal(details(a).find(row=>row.label==='登录状态').value,'已验证');
 assert.equal(details(a).find(row=>row.label==='消息接收').value,'托管连接恢复中');
 const online={...a,runtime_state:{ownership:'owned'}};
 assert.equal(summary(online).label,'在线'); assert.equal(details(online).find(row=>row.label==='自动回复').value,'已关闭');
 assert.equal(details({...online,auto_reply_enabled:true,runtime_auto_reply_enabled:false}).find(row=>row.label==='自动回复').value,'已开启，等待就绪');
});
test('presentation never mutates account state and full technical messages remain in detail',()=>{
 const a=Object.freeze({status:1,identity_verified:true,credential_state:'receive_only',last_probe_error:detail,identity_error:'单独的校验信息'});
 summary(a);details(a);
 assert.equal(accounts.accountStatusTechnicalInfo(a),detail+'\n单独的校验信息');
 assert.equal(a.credential_state,'receive_only');
});
test('cards have no verbose diagnostic banner and drawer uses the same badge with structured detail',()=>{
 const base=new URL('../../dyauthreply-client/client-ui/src/',import.meta.url);
 const list=readFileSync(new URL('views/AccountsView.vue',base),'utf8');
 const card=list.slice(list.indexOf('<section v-else class="accounts-grid">'),list.indexOf('<AppModal'));
 assert(card.includes('accountStatusPresentation(acc).label'));
 assert(!card.includes('last_probe_error')); assert(!card.includes('account-alert'));
 assert(card.includes('@click.stop="openProfile(acc)"'));
 const drawer=readFileSync(new URL('components/AccountProfileDrawer.vue',base),'utf8');
 assert(drawer.includes('statusView.label'));assert(drawer.includes('accountStatusDetails(props.account)'));
 assert(drawer.includes('查看技术信息'));assert(drawer.includes('<pre>{{ technicalInfo }}</pre>'));
 assert(list.includes('Object.assign(selected, latest)'));
});
test('chat header names sender and recipient explicitly',()=>{
 const raw=readFileSync(new URL('../../dyauthreply-client/client-ui/src/views/ChatView.vue',import.meta.url),'utf8');
 assert.ok(raw.includes('发送账号：'));assert.ok(raw.includes('收件人：'));
});
