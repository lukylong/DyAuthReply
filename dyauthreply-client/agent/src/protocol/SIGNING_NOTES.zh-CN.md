# 发送请求签名——现有实现学习笔记

> 本文只解释本仓库 **已经实现** 的代码（`native_signer.rs` / `http_plan.rs` / `dtrait.rs`）在做什么，
> 以及它用到的标准密码学原语。**不包含** 抖音安全 SDK（`webmssdk` / `bdms` / `sdk-glue`）的内部
> 判定逻辑，也不涉及如何让被平台标记的会话通过检测——那部分不在本仓库里，也不在本文范围内。
>
> 一句话前提：如果某个登录态被抖音判为「需重新登录」（业务码 7911），下面这些签名**全部算对也没用**，
> 因为拒绝发生在业务层而不是签名校验层。签名正确只是「请求格式合法」的必要条件，不是「能发」的充分条件。

相关源码：

- `src/protocol/native_signer.rs` —— 真正做密码学运算的地方
- `src/protocol/http_plan.rs` —— 纯离线地「装配」一个待签名请求，并在签名回来后校验/组装最终请求
- `src/protocol/dtrait.rs` —— 设备特征（dtrait）信封的加密
- `src/protocol/account_session.rs` —— 在发送前现场取 `identity` / `conversation` 等材料

---

## 1. 整体流程：plan → sign → finalize

发送一条私信的签名分三步，刻意拆开是为了让「做密码学的代码」和「拼 HTTP 请求的代码」互不污染：

1. **plan（装配）** `prepare_send_request`（`http_plan.rs`）
   纯函数，无网络 / 无时钟 / 无随机数 / 无私钥运算。它把一次发送所需的输入冻结下来，产出
   `UnsignedRequestPlan`，其中 `signer_requests: SignerRequests` 就是「交给签名器的两份活」：
   - `a_bogus`：要喂给 JS 引擎算 `a_bogus` 的 query/body
   - `ticket_guard`：要做 bd-ticket-guard 签名的输入（见第 2 节）

2. **sign（签名）** `NativeSigner::sign`（`native_signer.rs:253`）
   在一个受限并发的 `spawn_blocking` 里真正算三样东西，打包成 `SignerOutputs`：
   - `a_bogus`
   - `client_data`（bd-ticket-guard 的客户端数据，第 2 节）
   - `ree_public_key`（本地私钥对应的公钥，第 2 节）

3. **finalize（组装）** `finalize_send_request`（`http_plan.rs:679`）
   把签名结果塞回去，拼出最终 URL 和请求头。

### plan_digest：防止「张冠李戴」

`UnsignedRequestPlan` 里有一个 `plan_digest`，签名器算完会把同一个 digest 带回 `SignerOutputs`。
`finalize_send_request` 第一件事就是比对二者（`http_plan.rs:683`）：

```rust
if unsigned.plan_digest != outputs.plan_digest {
    return Err(RequestPlanError::PlanDigestMismatch);
}
```

意义：签名是异步、可并发的，plan_digest 保证「这组签名结果」只能配「生成它的那个 plan」，
不会把 A 请求的签名误用到 B 请求上。这是工程上的防呆，和密码学无关。

---

## 2. bd-ticket-guard（票据守卫）

这是本仓库签名里最核心、也最标准的一块。它要在请求头里附上**对本次请求的一个签名**，
让服务端相信「发请求的人掌握着登录时注册的那把私钥」。用到的头（`http_plan.rs:709` 起）：

| 请求头 | 来源 | 含义 |
|---|---|---|
| `bd-ticket-guard-client-data` | `ticket_client_data()` | 下面说的 ClientData（base64） |
| `bd-ticket-guard-ree-public-key` | `ree_public_key()` | 本地私钥对应的公钥（base64） |
| `bd-ticket-guard-version` | 常量 `2` | 协议版本 |
| `bd-ticket-guard-web-version` | 看 `ts_sign` 前缀 | `ts.1` → `1`，否则 `2` |
| `bd-ticket-guard-web-sign-type` | 看是否有 ecdh_key | ECDSA → `0`，HMAC → `1` |

### 2.1 被签名的内容：sign_payload

`http_plan.rs:638` 组出待签名字符串，格式固定：

```
ticket=<ticket>&path=/v1/message/send&timestamp=<秒级时间戳>
```

对应的 `req_content` 常量是 `"ticket,path,timestamp"`（`http_plan.rs:35`），
就是在声明「我签的是这三个字段」。

### 2.2 两种签名模式（`ticket_client_data`，`native_signer.rs:144`）

代码按「有没有 ecdh_key」二选一：

- **ECDSA 模式（默认，sign-type=0）**
  用登录态里的 P-256 私钥对 `sign_payload` 做 **ECDSA-SHA256** 签名，签名以 **DER** 编码后 base64：

  ```rust
  let key = SigningKey::from(private_key(&input.private_key)?);
  let signature: Signature = key.sign(input.sign_payload.as_bytes());
  STANDARD.encode(signature.to_der().as_bytes())
  ```

  这就是标准的「私钥签名、服务端用对应公钥验签」。服务端那把公钥，就是登录时随
  `bd_ticket_guard_pubKey` 注册上去的、与这把私钥配对的公钥。

- **HMAC 模式（sign-type=1）**
  如果存在一把 32 字节的 `ecdh_key`（见第 3 节），则改用 **HMAC-SHA256(ecdh_key, sign_payload)**：

  ```rust
  let mut mac = Hmac::<Sha256>::new_from_slice(key)?;
  mac.update(input.sign_payload.as_bytes());
  STANDARD.encode(mac.finalize().into_bytes())
  ```

### 2.3 ClientData 结构

两种模式算出的 `req_sign` 最终都包进同一个 JSON（`native_signer.rs:131` 的 `ClientData`），再整体 base64：

```json
{
  "ts_sign": "...",         // 登录态自带，服务端签发的票据会话标记
  "req_content": "ticket,path,timestamp",
  "req_sign": "<上面的签名 base64>",
  "timestamp": 1699999999,
  "t_trust": 1              // 可选；有 bd_ticket_crypt_cookie 时才带
}
```

### 2.4 公钥：ree_public_key（`native_signer.rs:122`）

把本地 P-256 私钥导出成**未压缩 SEC1 点**（65 字节，0x04 前缀）再 base64：

```rust
private_key(private)?.public_key().to_encoded_point(false).as_bytes()
```

它要和 `client_cert`（登录时服务端颁的证书里那把公钥）一致——
`validate_schema` / 测试里会校验「cert 里的公钥 == 私钥导出的公钥」。
这一步解释了为什么「私钥 ↔ 证书」必须配对：服务端用注册过的公钥验 `req_sign`，对不上就拒。

### 2.5 票据会话一致性（`http_plan.rs:615`）

如果 cookie 里有 `bd_ticket_guard_ts_sign_id`，代码会要求 `ts_sign` 以它为前缀，
否则直接 `TicketSessionMismatch`。即「这次用的票据会话」必须和 cookie 里记录的是同一个。

---

## 3. ECDH 密钥派生（`derive_ecdh_key`，`native_signer.rs:92`）

当需要 HMAC 模式时，那把 32 字节 `ecdh_key` 是这样来的，是教科书式的 **ECDH + HKDF**：

1. 本地 P-256 私钥 × 服务端公钥 → 共享点（`diffie_hellman`）
2. 取共享密钥的 x 坐标，过 **HKDF-SHA256**（salt 空、info 空）扩展出 32 字节

```rust
let shared = diffie_hellman(private.to_nonzero_scalar(), public.as_affine());
Hkdf::<Sha256>::new(None, shared.raw_secret_bytes()).expand(b"", &mut key)?;
```

服务端公钥既可以是 `pub.` 前缀的 base64 原始点，也可以是 PEM 证书（代码两种都认）。
这就是「双方各拿自己的私钥 + 对方公钥，算出同一把对称密钥」的标准做法。

---

## 4. a_bogus（`abogus`，`native_signer.rs:210`）

`a_bogus` 是抖音网页端的一个查询参数签名。本仓库**没有重写它的算法**，而是：

- 内置一份厂商的 `dy_ab.js`（`backend-django/.../sign/js/dy_ab.js`，编译期 `include_str!` 进来）
- 用嵌入式 JS 引擎 `rquickjs` 跑它，调用其中的 `get_ab(query, body)` 取结果

关键是把这段不受信任的脚本**关在沙箱里**跑（`abogus_with_prelude`，`native_signer.rs:171`）：

- 内存上限 32 MB、栈上限 1 MB
- 2 秒超时中断（`set_interrupt_handler` + deadline），防止死循环卡住
- 结果长度上限 8 KB

> 对这份黑盒脚本，本仓库的定位是「原样执行、不解读内部」。它的内部算法既没在本仓库实现，
> 本文也不去逆向——要学的是「怎么安全地在宿主里跑一段外部签名脚本」，而不是它算了什么。

---

## 5. dtrait 设备特征信封（`dtrait.rs`）

发送时请求头可能带 `x-tt-session-dtrait`，它是对登录时抓到的一段设备特征 blob 的**加密信封**，
用的是很常规的「混合加密」套路（`dtrait.rs:46` 的 `header_with_iv`）：

1. 随机生成 16 字节对称密钥 `key`，再用内置 RSA 公钥（`dtrait_public.pem`）**RSA-PKCS1v15 加密**这把 key
2. 把 `{dtrait, timestamp, sdkVersion, path}` 序列化成 JSON
3. 用 `key` + 随机 IV 做 **AES-128-CBC（PKCS7 填充）** 加密该 JSON
4. 最终头 = `d0_<RSA 加密的 key, base64>_<IV||密文, base64>`

```
d0_{base64(RSA(key))}_{base64(iv || AES_CBC(key, iv, json))}
```

这是标准的「RSA 包对称密钥 + 对称密钥加数据」的混合加密，和 TLS 里会话密钥交换的思路一致。
注意 `path` 会参与明文，所以同一份 blob 在不同接口（identity / send）下生成的头不同。

---

## 6. 并发与隔离（工程部分）

- `NativeSigner` 用一个 `Semaphore` 限制同时在跑的签名数（`native_signer.rs:216`），
  CPU 密集的 JS 执行和签名都在 `spawn_blocking` 里，不会堵塞异步运行时。
- 带密钥的结构体都手写了 `Debug`（`http_plan.rs` 里的 `Redacted`），私钥 / ticket / cookie
  在日志里只打印长度，不打印内容——排查时可以放心打 Debug。

---

## 小结：每个字段「为什么要它」

| 字段 | 作用 | 对应密码学 |
|---|---|---|
| `bd-ticket-guard-ree-public-key` | 声明「我是谁」 | P-256 公钥（SEC1 未压缩点） |
| `bd-ticket-guard-client-data.req_sign` | 证明「我持有私钥 / 共享密钥」 | ECDSA-SHA256 或 HMAC-SHA256 |
| `ecdh_key` | HMAC 模式下的对称密钥 | ECDH + HKDF-SHA256 |
| `a_bogus` | 网页端查询签名 | 厂商 JS 黑盒（沙箱执行） |
| `x-tt-session-dtrait` | 设备特征信封 | RSA 包 AES-128-CBC |
| `plan_digest` | 防签名错配（非安全字段） | SHA-256 域分隔摘要 |

想更细地看某一块（比如 ECDSA 验签在服务端是怎么对的、HKDF 为什么这么用、
或 `http_plan.rs` 里 header 顺序为什么要固定），告诉我哪一节，我就着这份代码继续展开。
