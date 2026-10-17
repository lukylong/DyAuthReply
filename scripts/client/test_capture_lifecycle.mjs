import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const root=new URL('../../browser-extension/douyin-cred-extractor/',import.meta.url);
const key='dy_capture_owned_tabs_v1';
const tick=()=>new Promise(r=>setImmediate(r));
function event(){const listeners=[];return {addListener:f=>listeners.push(f),emit:(...args)=>listeners.forEach(f=>f(...args)),listeners};}
function worker({persisted={},delayCreate=false}={}){
 const tabs=new Map([[1,{id:1,url:'https://creator.douyin.com/creator-micro/home'}]]);
 for(const [id,url] of Object.entries(persisted))tabs.set(Number(id),{id:Number(id),url});
 let next=100,release;const removed=[],storage={[key]:persisted},connect=event();
 const chrome={runtime:{id:'synthetic-extension',getURL:p=>'chrome-extension://synthetic-extension/'+p,onConnect:connect},
  tabs:{create:async options=>{if(delayCreate)await new Promise(r=>release=r);const t={id:next++,...options};tabs.set(t.id,t);return t;},get:async id=>{if(!tabs.has(id))throw Error('gone');return tabs.get(id);},remove:async id=>{removed.push(id);tabs.delete(id);},onRemoved:event()},
  storage:{session:{get:async k=>k===null?{...storage}:{[k]:storage[k]},set:async v=>Object.assign(storage,v),remove:async keys=>{for(const k of keys)delete storage[k];}}},cookies:{},webRequest:Object.fromEntries(['onBeforeRequest','onHeadersReceived','onBeforeSendHeaders'].map(n=>[n,event()]))};
 const ctx=vm.createContext({chrome,URL,console});ctx.importScripts=(...names)=>{for(const n of names)vm.runInContext(readFileSync(new URL(n,root),'utf8'),ctx);};
 vm.runInContext(readFileSync(new URL('background.js',root),'utf8'),ctx);
 assert.equal(connect.listeners.length,1,'background must own capture tab lifetime independently of popup finally');
 function port(url=chrome.runtime.getURL('popup.html')){const p={name:'dy-capture-owned-v1',sender:{id:chrome.runtime.id,url},onMessage:event(),onDisconnect:event(),replies:[],postMessage(v){this.replies.push(v);},disconnect(){this.onDisconnect.emit();}};connect.emit(p);return p;}
 return {tabs,removed,storage,port,release:()=>release?.()};
}
test('closing popup removes only its own temporary tabs',async()=>{
 const w=worker(),p=w.port();p.onMessage.emit({id:1,op:'create',windowId:1,url:'https://www.douyin.com/?recommend=1'});await tick();await tick();
 assert.equal(p.replies[0].ok,true);assert.equal(w.tabs.size,2);
 p.onDisconnect.emit();await tick();await tick();
 assert.deepEqual([...w.tabs.keys()],[1]);assert.deepEqual(w.removed,[100]);assert.equal(Object.keys(w.storage[key]).length,0);
});
test('popup destruction while tab creation is in flight cannot orphan the result',async()=>{
 const w=worker({delayCreate:true}),p=w.port();p.onMessage.emit({id:1,op:'create',windowId:1,url:'https://www.douyin.com/?recommend=1'});await tick();p.onDisconnect.emit();w.release();await tick();await tick();assert.deepEqual([...w.tabs.keys()],[1]);
});
test('normal finish is idempotent and never closes the original tab',async()=>{
 const w=worker(),p=w.port();p.onMessage.emit({id:1,op:'create',windowId:1,url:'https://creator.douyin.com/creator-micro/data/following/chat'});await tick();p.onMessage.emit({id:2,op:'finish'});await tick();p.onDisconnect.emit();await tick();assert.deepEqual(w.removed,[100]);assert(w.tabs.has(1));
});
test('restarted worker cleans recorded same-session owned tabs',async()=>{
 const w=worker({persisted:{2:'https://www.douyin.com/?recommend=1'}});await tick();await tick();assert.deepEqual(w.removed,[2]);assert(w.tabs.has(1));
});
test('content pages and arbitrary creation URLs are rejected',async()=>{
 const w=worker(),foreign=w.port('https://www.douyin.com/');foreign.onMessage.emit({id:1,op:'create',windowId:1,url:'https://www.douyin.com/?recommend=1'});await tick();assert.equal(w.tabs.size,1);
 const p=w.port();p.onMessage.emit({id:2,op:'create',windowId:1,url:'https://foreign.example/'});await tick();assert.equal(p.replies[0].ok,false);assert.equal(w.tabs.size,1);
});
test('concurrent popup scopes cannot clean each other or user tabs',async()=>{
 const w=worker(),a=w.port(),b=w.port();
 for(const p of [a,b])p.onMessage.emit({id:1,op:'create',windowId:1,url:'https://www.douyin.com/?recommend=1'});
 await tick();await tick();a.onDisconnect.emit();await tick();await tick();
 assert(w.tabs.has(1));assert.equal(w.tabs.size,2);b.onDisconnect.emit();await tick();await tick();assert.deepEqual([...w.tabs.keys()],[1]);
});
