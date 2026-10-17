import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const require=createRequire(new URL('../../dyauthreply-client/client-ui/package.json',import.meta.url));
const ts=require('typescript');
const source=readFileSync(new URL('../../dyauthreply-client/client-ui/src/utils/quickAuthProgress.ts',import.meta.url),'utf8');
const output=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const exports={};vm.runInNewContext(output,{exports});
test('late responses cannot reopen closed or replaced sessions',()=>{
 for(const current of [undefined,'next'])assert.equal(exports.acceptsQuickAuthResponse('previous',current,1,1,false),false);
 assert.equal(exports.acceptsQuickAuthResponse('same','same',1,2,false),false);
 assert.equal(exports.acceptsQuickAuthResponse('same','same',2,2,true),false);
 assert.equal(exports.acceptsQuickAuthResponse('same','same',2,2,false),true);
});
test('failed reads hide stale chips and preserve old-agent compatibility',()=>{
 assert.equal(exports.freshQuickAuthProgress(null),false);
 assert.equal(exports.freshQuickAuthProgress({progress_fresh:false}),false);
 assert.equal(exports.freshQuickAuthProgress({progress_fresh:true}),true);
 assert.equal(exports.freshQuickAuthProgress({}),true);
});
test('UI applies response fence and keeps importing/cancel failures visible',()=>{
 const ui=readFileSync(new URL('../../dyauthreply-client/client-ui/src/views/AccountsView.vue',import.meta.url),'utf8');
 assert(ui.includes('acceptsQuickAuthResponse(id, quickAuth.value?.session_id'));
 assert(ui.includes('quickAuth && freshQuickAuthProgress(quickAuth)'));
 assert(ui.includes("quickAuthBusy || quickAuth.state === 'importing'"));
 const close=ui.slice(ui.indexOf('async function closeQuickAuth()'),ui.indexOf('async function retryQuickAuth()'));
 assert(close.indexOf('await cancelQuickAuth')<close.indexOf('showQuickAuth.value = false'));
 assert(!close.includes('catch { /* owned session timeout'));
});
