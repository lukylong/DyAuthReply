import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../../backend-django/core/douyin/runtime/transport/sign/js/dy_ab.js',import.meta.url),'utf8');
const fixture=JSON.parse(readFileSync(new URL('../../dyauthreply-client/agent/tests/fixtures/native_signing.json',import.meta.url),'utf8'));
const mac='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
const win='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
function sign(ua){const c=vm.createContext({require:()=>({})});vm.runInContext(fixture.ab_prelude+source,c);return c.get_ab('msToken=synthetic-only','',ua);}
test('actual account UA affects the signature without random or clock differences',()=>{assert.notEqual(sign(mac),sign(win));assert.equal(sign(mac),sign(mac));});
test('legacy fixture remains reproducible without a supplied UA',()=>{const c=vm.createContext({require:()=>({})});vm.runInContext(fixture.ab_prelude+source,c);assert.equal(c.get_ab(fixture.ab_query,fixture.ab_body),fixture.ab_expected);});
