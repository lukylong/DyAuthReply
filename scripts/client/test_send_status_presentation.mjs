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
const accounts=load('AccountsView',['accountStatusLabel','isAccountHealthy','isSendRestricted','accountCredentialLabel']);
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
test('healthy receive plus unknown send does not claim sendability',()=>{
 const a={status:1,identity_verified:true,credential_state:'unknown'};
 assert.match(accounts.accountCredentialLabel(a),/发送待验证/);
 assert.match(accounts.accountStatusLabel(a),/不代表|不保证/);
 assert.match(accounts.accountCredentialLabel({...a,credential_state:'sendable'}),/上次发送成功/);
});
test('identity expiry and actual send restriction remain distinct',()=>{
 assert.equal(accounts.accountCredentialLabel({credential_state:'invalid'}),'登录失效');
 assert.match(accounts.accountCredentialLabel({status:1,identity_verified:true,credential_state:'receive_only',last_probe_error:'客户端协议发送被平台拒绝'}),/发送/);
});
test('chat header names sender and recipient explicitly',()=>{
 const raw=readFileSync(new URL('../../dyauthreply-client/client-ui/src/views/ChatView.vue',import.meta.url),'utf8');
 assert.ok(raw.includes('发送账号：'));assert.ok(raw.includes('收件人：'));
});
