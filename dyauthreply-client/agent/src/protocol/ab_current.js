/* Host-aware signing contract independently ported from DouYin_Spider's
 * utils/ab_pure.py + SM3 (fc8b9c2490a9561265425c26058290f01c064eda).
 * Uses captured browser geometry, never invents missing browser fields.
 * No network, filesystem, global browser hooks or credential persistence. */
function abUtf8(s){const out=[];for(const ch of s){const n=ch.codePointAt(0);if(n<128)out.push(n);else if(n<2048)out.push(192|(n>>6),128|(n&63));else if(n<65536)out.push(224|(n>>12),128|((n>>6)&63),128|(n&63));else out.push(240|(n>>18),128|((n>>12)&63),128|((n>>6)&63),128|(n&63));}return out;}
function abSm3(input){
 const rot=(x,n)=>{n&=31;return ((x<<n)|(x>>>(32-n)))>>>0;};
 const p0=x=>(x^rot(x,9)^rot(x,17))>>>0,p1=x=>(x^rot(x,15)^rot(x,23))>>>0;
 const data=typeof input==='string'?abUtf8(input):Array.from(input);const bits=data.length*8;data.push(128);while(data.length%64!==56)data.push(0);for(let i=7;i>=0;i--)data.push(Math.floor(bits/2**(i*8))&255);
 let state=[0x7380166f,0x4914b2b9,0x172442d7,0xda8a0600,0xa96f30bc,0x163138aa,0xe38dee4d,0xb0fb0e4e];
 for(let off=0;off<data.length;off+=64){const w=[];for(let i=0;i<16;i++){let x=0;for(let j=0;j<4;j++)x=(x<<8)|data[off+i*4+j];w[i]=x>>>0;}for(let i=16;i<68;i++)w[i]=(p1(w[i-16]^w[i-9]^rot(w[i-3],15))^rot(w[i-13],7)^w[i-6])>>>0;
 let [a,b,c,d,e,f,g,h]=state;for(let j=0;j<64;j++){const ss1=rot((rot(a,12)+e+rot(j<16?0x79cc4519:0x7a879d8a,j))>>>0,7),ss2=ss1^rot(a,12);const ff=j<16?a^b^c:(a&b)|(a&c)|(b&c),gg=j<16?e^f^g:(e&f)|(~e&g);const t1=(ff+d+ss2+(w[j]^w[j+4]))>>>0,t2=(gg+h+ss1+w[j])>>>0;d=c;c=rot(b,9);b=a;a=t1;h=g;g=rot(f,19);f=e;e=p0(t2);}
 state=[a,b,c,d,e,f,g,h].map((v,i)=>(v^state[i])>>>0);}
 return state.flatMap(v=>[v>>>24,(v>>>16)&255,(v>>>8)&255,v&255]);
}
function abBase64(bytes,alphabet){let s='';for(let i=0;i<bytes.length;i+=3){const n=Math.min(3,bytes.length-i),v=(bytes[i]<<16)|((bytes[i+1]||0)<<8)|(bytes[i+2]||0);s+=alphabet[(v>>>18)&63]+alphabet[(v>>>12)&63]+(n>1?alphabet[(v>>>6)&63]:'=')+(n>2?alphabet[v&63]:'=');}return s;}
function abRc4(key,input){const box=Array.from({length:256},(_,i)=>255-i);let j=0;for(let i=0;i<256;i++){j=(j*box[i]+j+key[i%key.length])%256;[box[i],box[j]]=[box[j],box[i]];}let i=0;j=0;return input.map(v=>{i=(i+1)%256;j=(j+box[i])%256;[box[i],box[j]]=[box[j],box[i]];return v^box[(box[i]+box[j])%256];});}
function get_ab_current(query,body,ua,profileJson){
 if(typeof query!=='string'||query.length>16384||typeof body!=='string'||body.length>16384||!ua||ua.length>2048)throw Error('bounded signing input');
 const p=JSON.parse(profileJson);if(!Array.isArray(p.geo)||p.geo.length!==8||p.geo.some(n=>!Number.isInteger(n)||n<0||n>32768)||!['MacIntel','Win32','Linux x86_64'].includes(p.platform))throw Error('captured geometry required');
 const abc3='ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe',abc4='Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe';
 const le=(n,count)=>Array.from({length:count},(_,i)=>Math.floor(n/2**(8*i))&255);
 const bytes=s=>Array.from(s).flatMap(c=>c.charCodeAt(0)>255?[c.charCodeAt(0)>>8,c.charCodeAt(0)&255]:[c.charCodeAt(0)]);
 const blend=(a,b,r,s)=>[(r&170)|(a&85),(r&85)|(a&170),(s&170)|(b&85),(s&85)|(b&170)];
 const browser=/chrome\/|chromium\//i.test(ua)?'Chrome':/firefox|fxios/i.test(ua)?'Firefox':/safari/i.test(ua)?'Safari':'Other';
 const rand=()=>Math.random(),now=Date.now(),L={};
 const im=p.signing_context==='current_web';
 const sdk=im&&p.sdk?p.sdk:{version:1,minor:im?0:8,page_id:im?6241:11881,aid:6383,flags:im?[2,2,2]:[132,1,1],env_flags:browser==='Firefox'?33:1};
 if(!Number.isInteger(sdk.version)||sdk.version<1||sdk.version>65535||!Number.isInteger(sdk.minor)||sdk.minor<0||sdk.minor>255||!Number.isInteger(sdk.page_id)||sdk.page_id<1||sdk.page_id>4294967295||sdk.aid!==6383||!Array.isArray(sdk.flags)||sdk.flags.length!==3||sdk.flags.some(v=>!Number.isInteger(v)||v<0||v>255)||!Number.isInteger(sdk.env_flags)||sdk.env_flags<0||sdk.env_flags>65535)throw Error('invalid captured SDK context');
 const minor=sdk.minor,pageId=sdk.page_id,flags=sdk.env_flags;
 const h1=abSm3(abSm3(query+'dhzx')),h2=abSm3(abSm3(body+'dhzx')),hu=abSm3(abBase64(abRc4([sdk.version>>>8,sdk.version&255,minor],Array.from(ua.trim()).map(c=>c.charCodeAt(0))),abc3));
 const escaped=(d,i,res,fallback,force)=>{let n=d[i]??fallback;while(n===res)n=d[++i]??fallback;return force?res:n;};
 L[24]=41;L[26]=Math.trunc((now-1721836800000)/1209600000);L[27]=6;L[28]=3;
 le(now,6).forEach((v,i)=>L[29+i]=v);L[35]=sdk.version&255;L[36]=sdk.version>>>8;L[38]=flags&255;L[39]=flags>>>8;L[40]=sdk.flags[0];L[41]=sdk.flags[1];L[42]=sdk.flags[2];L[43]=0;le(minor,4).forEach((v,i)=>L[44+i]=v);
 L[48]=h1[9];L[49]=h1[18];L[51]=escaped(h1,3,11,12,!!(flags&2));L[52]=h2[10];L[53]=h2[19];L[55]=escaped(h2,4,8,9,!!(flags&4));L[56]=hu[11];L[57]=hu[21];L[59]=escaped(hu,5,12,13,!!(flags&8));
 le(now-1,6).forEach((v,i)=>L[60+i]=v);L[66]=3;le(pageId,4).forEach((v,i)=>L[67+i]=v);le(6383,4).forEach((v,i)=>L[71+i]=v);
 const geo=bytes([...p.geo,p.platform].join('|')),stamp=bytes(((now+3)&255)+',');[L[79],L[80]]=le(geo.length,2);[L[84],L[85]]=le(stamp.length,2);
 let r=Math.floor(rand()*65535);const head=blend(1,0,r&255,(r>>8)&255);rand();r=Math.floor(rand()*240);const check=r>109?r+r%2+1:r;const permission=(Math.floor(rand()*255)&77)|2|16|32|128;head.push(...blend(1,0,check,permission));
 const fields=[24,26,27,28,29,30,31,32,33,34,35,36,38,39,40,41,42,43,44,45,46,47,48,49,51,52,53,55,56,57,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,79,80,84,85];
 const checkSum=[...head,...fields.map(i=>L[i])].reduce((a,b)=>a^b,0);
 const order=[34,44,56,61,73,29,70,45,35,49,38,66,51,68,28,48,64,47,30,71,26,55,31,69,59,40,62,63,27,72,41,74,57,52,42,39,33,67,53,43,65,46,36,24,60,32,79,80,84,85];
 const input=[...order.map(i=>L[i]),...geo,...stamp,checkSum];
 const nonce=blend(3,82,Math.floor(rand()*65535)&255,Math.floor(rand()*40)+({Chrome:0,Firefox:40,Safari:81}[browser]??210));
 const expanded=[];for(let i=0;i<input.length;i+=3){if(i+2<input.length){const z=Math.floor(rand()*1000)&255;expanded.push((z&145)|(input[i]&110),(z&66)|(input[i+1]&189),(z&44)|(input[i+2]&211),(input[i]&145)|(input[i+1]&66)|(input[i+2]&44));}else{expanded.push(input[i]);if(input[i+1])expanded.push(input[i+1]);}}
 return abBase64([...nonce,...abRc4([211],[...head,...expanded].map(x=>x&65535)).map(x=>x&255)],abc4);
}
