"""Regenerate synthetic vectors using existing Python/Node reference code; no accounts."""
import base64
import json
import pathlib
import subprocess
import sys
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
root = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root / 'backend-django'))
from core.douyin.runtime.transport.sign.bd_ticket import derive_ecdh_key, hmac_request_sign
private = ec.derive_private_key(1, ec.SECP256R1())
peer = ec.derive_private_key(2, ec.SECP256R1())
pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
def public(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode()
peer_public = 'pub.' + public(peer)
key = derive_ecdh_key(pem, peer_public)
payload = 'ticket=synthetic-ticket&path=/v1/message/send&timestamp=1700000000'
prelude = 'const OriginalDate=Date; globalThis.Date=class extends OriginalDate { constructor(...a){super(...(a.length?a:[1700000000000]));} static now(){return 1700000000000;} }; Math.random=()=>0.123456789; globalThis.performance={now:()=>0};'
runner = '''const fs=require('fs'), vm=require('vm'), {createRequire}=require('module');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const src=fs.readFileSync(input.path,'utf8');
const context={require:createRequire(input.path)}; vm.createContext(context);
vm.runInContext(input.prelude+src,context,{timeout:2000});
const ab=vm.runInContext('get_ab("msToken=synthetic-only", "")',context,{timeout:2000});
const sig=vm.runInContext('get_req_sign('+JSON.stringify(input.payload)+','+JSON.stringify(input.pem)+')',context,{timeout:2000});
console.log(JSON.stringify({ab,sig}));'''
r = subprocess.run(['node', '-e', runner], input=json.dumps(dict(path=str(root/'backend-django/core/douyin/runtime/transport/sign/js/dy_ab.js'), prelude=prelude, payload=payload, pem=pem)), text=True, capture_output=True)
if r.returncode:
    raise RuntimeError(r.stderr)
node = json.loads(r.stdout)
data = dict(schema_version=1, synthetic=True, private_key=pem, peer_public=peer_public, ree_public=public(private), ecdh_hex=key.hex(), payload=payload, hmac=hmac_request_sign(payload,key), ecdsa=node['sig'], ab_query='msToken=synthetic-only', ab_body='', ab_prelude=prelude, ab_expected=node['ab'])
pathlib.Path(__file__).with_name('native_signing.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
print('SYNTHETIC_PYTHON_NODE_REFERENCE_WRITTEN')
