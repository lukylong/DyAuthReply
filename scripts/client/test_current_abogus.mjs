import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../../dyauthreply-client/agent/src/protocol/ab_current.js',import.meta.url),'utf8');
const fixture=JSON.parse(readFileSync(new URL('../../dyauthreply-client/agent/tests/fixtures/captured_abogus.json',import.meta.url),'utf8'));
function context(c){const ctx=vm.createContext({});vm.runInContext(`Date.now=()=>${c.now};Math.random=()=>${c.random};`+source,ctx);return ctx;}
test('SM3 matches its independent published abc vector',()=>{const c=context(fixture.cases[0]);assert.equal(Array.from(c.abSm3('abc')).map(x=>x.toString(16).padStart(2,'0')).join(''),'66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0');});
test('captured signature matches all independent Python vectors',()=>{for(const f of fixture.cases){const c=context(f);assert.equal(c.get_ab_current(f.query,f.body,f.ua,JSON.stringify(f.profile)),f.expected);}});
test('full query coverage and actual geometry affect the signature',()=>{const f=fixture.cases.at(-1),c=context(f),sign=(q,p)=>c.get_ab_current(q,f.body,f.ua,JSON.stringify(p));assert.notEqual(sign(f.query,f.profile),sign('msToken=synthetic-only',f.profile));const p=structuredClone(f.profile);p.geo[0]=1280;assert.notEqual(sign(f.query,f.profile),sign(f.query,p));assert.throws(()=>sign(f.query,{}));});
