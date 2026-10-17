import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const root=new URL('../../browser-extension/douyin-cred-extractor/',import.meta.url);
const core=readFileSync(new URL('capture-core.js',root),'utf8');
const fixture=JSON.parse(readFileSync(new URL('../../dyauthreply-client/agent/tests/fixtures/native_signing.json',import.meta.url),'utf8'));
const ctx=vm.createContext({URL,TextDecoder,Uint8Array,atob,btoa,crypto:webcrypto});vm.runInContext(core,ctx);
const c=ctx.DyCapture, token='S'.repeat(164);
const cookie='sessionid=synthetic; sessionid_ss=synthetic; sid_tt=synthetic; UIFID=browser; s_v_web_id=verify_browser; bd_ticket_guard_tssign=ts.2.synthetic';
const headers=Object.fromEntries(['www.douyin.com','imapi.douyin.com','creator.douyin.com'].map(h=>[h,cookie]));
const keys=JSON.stringify({ec_privateKey:fixture.private_key});
const ticket=JSON.stringify({ticket:'synthetic',ts_sign:'ts.2.synthetic',client_cert:'pub.'+fixture.ree_public});
assert.equal(c.tokenFromUrl('https://www.douyin.com/path?msToken='+token),token);
for(const url of ['http://www.douyin.com','https://evil.test','https://www.douyin.com.evil.test','https://creator.douyin.com'])assert.equal(c.tokenFromUrl(url+'/?msToken='+token),'');
assert.equal(c.missing(headers,token,'synthetic').length,0);
assert(c.missing({...headers,'imapi.douyin.com':cookie+'; sid_tt=other'},token,'synthetic').length);
assert(c.missing(headers,'','synthetic').includes('浏览器 SDK Token'));
assert(await c.selectMaterial([keys],[ticket],cookie));
assert.equal(await c.selectMaterial([keys],[ticket],cookie.replace('ts.2.synthetic','ts.2.foreign')),null);
assert.equal(await c.selectMaterial([keys],[ticket.replace(fixture.ree_public,'other')],cookie),null);
assert(await c.selectMaterial(['{}',keys],['{}',ticket],cookie));
console.log('PASS core: real token scope, completeness, session conflicts, matching key/certificate');

async function popupCase(mode){
 const elements=new Map();const element=id=>{if(!elements.has(id))elements.set(id,{value:'',hidden:true,classList:{add(){},remove(){},toggle(){}},addEventListener(){}});return elements.get(id);};
 let now=100000, removed=[];
 const context=vm.createContext({URL,TextDecoder,Uint8Array,atob,btoa,crypto:webcrypto,console,unescape,encodeURIComponent,
   Date:class extends Date {static now(){now+=1000;return now;}},setTimeout(fn,delay){if(delay<5000)fn();return 1;},clearTimeout(){},
   document:{getElementById:element,addEventListener(){}},HTMLElement:class{},navigator:{}});
 const chrome={
  tabs:{query:async()=>[{id:1,windowId:1,url:'https://creator.douyin.com/creator-micro/home',cookieStoreId:'0'}],
    create:async()=>({id:2,windowId:1,url:'https://www.douyin.com/jingxuan',cookieStoreId:'0'}),
    get:async id=>({id,cookieStoreId:'0',status:'loading'}),remove:async id=>removed.push(id)},
  cookies:{getAllCookieStores:async()=>[{id:'0',tabIds:[1,2]}],getAll:async()=>cookie.split('; ').map(v=>{const[n,...rest]=v.split('=');return {name:n,value:rest.join('=')};})},
  scripting:{executeScript:async({func,target})=>[{result:String(func).includes('document.readyState')?{ready:'interactive',host:'www.douyin.com'}:String(func).includes('media/user/info')?
    (mode==='identity'?{error:'not authenticated'}:{nickname:'SAMPLE',sec_uid:'self'}):
    {origin:target.tabId===1?'creator.douyin.com':'www.douyin.com',keys:[keys],tickets:target.tabId===1?[ticket]:[ticket.replace('ts.2.synthetic','ts.2.stale')],dtrait_blob:mode==='missing'?'':'synthetic-blob',dtrait_path:'/passport/token/beat/web/',ua:'Chrome/152.0',browser_profile:{platform:'MacIntel'}}}]},
  storage:{session:{get:async list=>Object.fromEntries(list.map(k=>[k,{value:k.endsWith('_token')?token:k.endsWith('_ticket')?ticket:'dtrait',capturedAt:999999,storeId:'0',tabId:2,sourceHost:'www.douyin.com'}]))}}
 };
 context.chrome=chrome;vm.runInContext(core,context);vm.runInContext(readFileSync(new URL('popup.js',root),'utf8'),context);
 await vm.runInContext('grab()',context);
 if(mode==='success'){
  assert(element('bundle').value.startsWith('DYCRED1.'),element('tabHint').textContent);
  const payload=JSON.parse(Buffer.from(element('bundle').value.slice(8),'base64url'));
  assert.equal(payload.capture_version,2);assert.equal(payload.query_ms_token,token);assert.equal(payload.sec_uid,'self');
  assert.equal(element('result').hidden,false);
 } else {assert.equal(element('bundle').value,'');assert.equal(element('result').hidden,true);assert.equal(element('tabHint').hidden,false);}
 assert.equal(element('grabBtn').disabled,false);
 assert.deepEqual(removed,mode==='identity'?[]:[2]);
 console.log('PASS popup: '+mode+', no stale bundle, owned-tab cleanup');
}
await popupCase('success');await popupCase('missing');await popupCase('identity');
console.log('PASS extension capture regression suite');
const listeners={}, saved={};
const bg=vm.createContext({URL,TextDecoder,Uint8Array,atob,btoa,Date,crypto:webcrypto});
bg.importScripts=()=>vm.runInContext(core,bg);
bg.chrome={tabs:{get:async id=>({id}),onRemoved:{addListener:f=>listeners.removed=f}},cookies:{getAllCookieStores:async()=>[{id:'normal',tabIds:[1]},{id:'private',tabIds:[2]}]},storage:{session:{set:async v=>Object.assign(saved,v),get:async()=>saved,remove:async ks=>ks.forEach(k=>delete saved[k])}},webRequest:Object.fromEntries(['onBeforeRequest','onHeadersReceived','onBeforeSendHeaders'].map(name=>[name,{addListener:f=>listeners[name]=f}]))};
vm.runInContext(readFileSync(new URL('background.js',root),'utf8'),bg);
listeners.onBeforeRequest({tabId:1,url:'https://www.douyin.com/?msToken='+token});
listeners.onBeforeRequest({tabId:2,url:'https://www.douyin.com/?msToken='+token});
listeners.onBeforeRequest({tabId:1,url:'https://evil.test/?msToken='+token});
listeners.onBeforeRequest({tabId:-1,url:'https://www.douyin.com/?msToken='+token});
listeners.onHeadersReceived({tabId:1,url:'https://www.douyin.com/',responseHeaders:[{name:'x-ms-token',value:'bad\nvalue'}]});
await new Promise(r=>setImmediate(r));
assert.equal(Object.keys(saved).length,2);
assert(saved.dy_capture_v2_normal_1_www_douyin_com_token===undefined);
assert.equal(saved['dy_capture_v2_normal_1_www.douyin.com_token'].storeId,'normal');
assert.equal(saved['dy_capture_v2_private_2_www.douyin.com_token'].storeId,'private');
await listeners.removed(1);assert.equal(Object.keys(saved).length,1);
console.log('PASS background: store/tab/origin isolation, invalid headers, tab cleanup');
