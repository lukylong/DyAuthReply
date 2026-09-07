# dy-agent

DyAuthReply 本地 Rust Agent 的迁移基础。目前实现：

- 持久安装 ID、每次启动唯一 boot ID；
- 数据目录 OS 独占锁；
- 正交账号状态模型；
- SQLite schema v2 正确性存储（WAL、receipt/checkpoint、fenced outbound batch/segment、
  rolling manifest、cleanup journal）；
- schema v1 打开时先通过 SQLite Online Backup 生成可验证、可回退的一致快照；失败迁移重试会先
  校验再刷新固定路径的快照，且旧快照保留到新快照验证完成并以平台安全的可恢复替换发布，避免备份失败或回退后新增
  WAL/表行造成恢复缺口；
- Chat/Audit/Debug 三类独立滚动分段，v2 长度/反码/摘要/提交标记帧、active 尾恢复、孤立 sealed
  收编、manifest/file 对账和有输出上限的真实 Zstandard 流式压缩；
- 保存时间/字节上限/最少分段、low/high/critical 磁盘水位、每轮删除上限，以及
  `debug -> chat -> audit` 的压力回收；
- 单 Tokio runtime、每账号轻量 Actor、有界 mailbox、一个中央 timer、按账号轮转的
  `16:4:1` 公平队列，以及 manual 有界突发；
- 账号级 transport/signer 熔断、全局 `4/s + burst 8` 重连预算、固定 signer lanes、
  安装级状态聚合和 30 秒 delta/5 分钟 full heartbeat；
- 运行期间周期存储清理、动态 Health API v5，以及拒绝新任务后观察全部 Actor/driver 的
  有界 drain；
- 与 Python 权威 pb2 编码器共用的 PC IM send 黄金语料、有限 protobuf codec、响应解码和
  保守结果分类；
- 与 Python 生产组装路径共用的 HTTP RequestPlan 语料，离线验证独立 query `msToken`、原始
  Cookie、有序 URL/header、空 body A-Bogus 输入、ticket-guard 输入和 signer 输出摘要绑定；
- 只读 `GET /health`。

默认账号托管模式仍固定为 `shadow-disabled`，没有接管现有 Python 客户端数据库或真实账号。
原有两份离线语料验证 send request/response 字节与 HTTP 规划；新增 native 模块的密码运算、
真实 socket 和 HTTPS 证据单独记录（见下文）。这些都不表示 inbox/WebSocket、工作台手动回复
或自动规则已经迁移。默认 Agent 监听 `127.0.0.1:18765`，与现有客户端端口隔离。

账号状态中的 `paused_auto` 只暂停规则自动回复，不会误伤工作台的手动发送；发送能力、入站
链路和租约状态仍分别判断。

## 本地验证

```bash
cargo fmt --manifest-path dyauthreply-client/agent/Cargo.toml -- --check
cargo clippy --locked --manifest-path dyauthreply-client/agent/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path dyauthreply-client/agent/Cargo.toml
cargo test --locked --manifest-path dyauthreply-client/agent/Cargo.toml --test runtime_simulation
cargo run --release --locked --manifest-path dyauthreply-client/agent/Cargo.toml --bin runtime-sim -- --accounts 300 --events-per-account 1
cargo run --release --locked --manifest-path dyauthreply-client/agent/Cargo.toml --bin runtime-soak -- --accounts 300 --cycles 20 --events-per-account 50 --producers 128 --max-rss-mib 512 --max-growth-mib 128 --max-tail-growth-mib 32 --max-cpu-cores 8 --max-drain-ms 10000 --max-backpressure-per-event 100 --max-thread-growth 0
cargo run --release --locked --manifest-path dyauthreply-client/agent/Cargo.toml -- --verify-protocol
```

## 启动

```bash
cargo run --manifest-path dyauthreply-client/agent/Cargo.toml -- --check
cargo run --manifest-path dyauthreply-client/agent/Cargo.toml
curl http://127.0.0.1:18765/health
```

开发时可通过 `DY_AGENT_DATA_DIR` 指定隔离数据目录，通过 `DY_AGENT_BIND` 覆盖 loopback
监听地址；非 loopback 地址会被拒绝。Agent 启动时恢复 `<data_dir>/segments`，校验 sealed 文件
与 schema v2 manifest，执行启动清理；正常 serve 后中央 timer 会继续周期清理。Health API v5
实时报告压力、Actor/timer、队列上限和拒绝、signer lanes、熔断、聚合 heartbeat、回收与 drain
计数。默认值是后续真实账号负载门禁前的保守初值，不是容量承诺。

`runtime-sim` 只使用合成账号、opaque durable ID 和确定性虚拟延迟，输出 JSON 负载报告；它不
读取 Cookie、不连接抖音，`network_requests` 必须为 0。10/100/300 账号仿真、300 账号断线风暴、
sleep/resume coalesce 和聚合 heartbeat 均是 CI 门禁。真实手动/自动回复仍必须通过后续 fenced
canary，不能由此仿真推断为可用。

`runtime-soak` 走真实 `RuntimeHandle`、账号 Actor、mailbox、中央 timer 和公平队列，但使用
network-free shadow dispatch。它反复创建/删除账号并并发灌入唯一 durable work，采样当前 RSS、
进程 CPU 时间和线程数，记录显式背压重试、队列/定时器峰值、尾部 RSS 增长和最终 drain 状态。
生产默认认证范围是300账号，运行时硬保护上限是512；达到512时还必须证明第513个账号被拒绝。
资源采样缺失、超阈值、任务/定时器/队列未归零、未解决任务或背压无法恢复都会让命令非零退出。

当前默认 Chat/Audit/Debug 都持久化且不压缩；生产启动始终装载内置 `ZstdCodec`，因此策略关闭新
压缩后仍可读取既有 `.segment.zst`，编码/解码都走流式接口且解码输出受 family 目标大小限制。
每类是否持久化、是否压缩、保存时间、总字节、目标大小、单记录上限和最低分段数均使用独立
策略。临界磁盘水位只抑制可丢弃正文，`core.sqlite3` 正确性事务继续工作；清理后会重新采样磁盘，
High/Critical 状态保持到使用率回落至 low watermark。

`--verify-protocol` 是完全离线的启动门禁：它在解析数据目录、获取实例锁或监听端口之前验证
内嵌 wire 与 HTTP-plan 语料并打印各自 SHA-256、参考提交和用例数。`--check` 与正常启动也会
先执行同一门禁；health 仍返回 `protocol_mode=shadow-disabled`，并分别报告 wire、HTTP-plan
以及聚合 parity 状态。

### 关闭与超时

`drain()` 超时会明确返回错误，并保持 `Draining`；后台协调任务继续持有未完成操作，
不提前报告 `Stopped`。同步磁盘 I/O 由 blocking worker 执行并被清理任务等待，
不创建脱离所有权的线程。进程保留安装目录锁直到实际清理完成；极端永久 I/O 阻塞需要
后续桌面监督器的进程级退出策略，不以提前释放锁来伪装成功退出。

## 显式 native protocol 执行模块（尚未接管账号 worker）

新增 `protocol::native_signer`、`live_http`、`live_sender`：RustCrypto 实现 P-256
ECDSA/ECDH/HKDF/HMAC 和 ree public key；A-Bogus 仍执行权威 vendored JS，采用进程内
QuickJS、32 MiB 内存上限、2 秒中断和最多 4 个默认签名槽。运行时不启动 Python/Node。
这属于“Rust 主程序＋嵌入式签名脚本”，不是 A-Bogus 全算法原生 Rust 重写。

`LiveSender` 读取正确性库的固定 payload/client message ID，复核 watch 账号状态与凭证代次，
签名后再原子 StartAttempt（真实 lease/fence 校验），只发一次 HTTP；确认回执再写 Confirm，
无确认/网络错误写 Uncertain。外部业务参数不会覆盖持久化正文与 client ID。
调用方仍需提供已验证的服务器 lease、完整凭证、正确的账号会话上下文；GUI、真实
inbox/WebSocket、自动规则、账号迁移和远端 lease 下发还未接入这些模块。

HTTP 使用固定版本的 wreq/BoringSSL 浏览器 profile，按导入 UA 选择不高于其版本的可用
Chrome profile；禁止自动重试和重定向、不共享 Cookie jar，连接与读取均有限时，响应最多
2 MiB。这不是浏览器 TLS 字节级 parity 的证明；真实账号仍需独立 canary。

构建新增 CMake、C/C++ 编译器及 libclang 要求（BoringSSL/QuickJS 静态库），开发构建工具
不是客户端运行时依赖。macOS 构建需 Xcode Command Line Tools；尚未完成 Windows 安装包验收。

显式无凭证 HTTPS 探测（只读取 robots.txt，不证明账号收发能力）：

```bash
cargo run --locked --manifest-path dyauthreply-client/agent/Cargo.toml --bin protocol-probe
```

Health 的 `protocol_execution` 单独报告 ticket guard / A-Bogus / HTTP 实现和
`account_worker=shadow_disabled`，避免把底层已实现误报为账号已切换。

## 账号 Session 真实读取

`account-probe --credentials <owner-only JSON>` 使用 Rust 执行 ECDH 换证、身份 token、
get_by_user 和会话 ticket 请求。支持现有扩展 DYCRED1 的库内解析，以及单次旧数据迁移
格式 `{account_id,user_agent,expected_sec_uid,storage_state}`。凭证容器不提供 Debug/Serialize；
CLI 只打印成功标志、协议状态和计数，不打印 Cookie、私钥、token、ticket 或消息正文。
读测试不启动账号 worker，也不发送消息。读取成功与“账号已经接管/可以自动回复”仍分别验收。

本轮真实账号验证还修正了单行 PEM base64 正文解析兼容：只在密码解析层转换为 DER，
保留原凭证文本与请求摘要不变。不要因这种本地格式错误把账号标记为平台风控。

### Schema 3: durable automatic-reply guards (migration in progress)

The core now upgrades v2 to v3 with a verified `backups/core-v2-to-v3-<database-id>.sqlite3`
Online Backup. v1 upgrades retain both the v1->v2 and v2->v3 backups. An old v2 binary rejects
v3 data; stop the Agent and restore a verified v2 backup into an independent data directory
before testing a code rollback. Never copy a live SQLite main file without its committed WAL.

`CoreStore::consume_inbound_guarded` atomically reserves account/rule/peer quota scopes with
the reply plan and inbound ACK. Confirmed/terminal-partial delivery charges once; a fully
rejected batch releases without charging; uncertain sends retain reservations until reconciled.
Per-account scope growth is capped; terminal history/index retention is still a separate gate.
This storage gate does **not** enable native automatic sends: rule preview remains preview-only
until automatic dispatch, account policies, ownership and real automatic-reply tests are connected.

### Opt-in native automatic HTTP canary

`MessagingSettings.automation` now accepts scoped account policies (enabled, daily quota, min/max
interval, silent window, optional peer limit and blocked peer IDs). With a validated rule snapshot,
owned lease and actual Sendable evidence, the native executor can perform automatic replies through
`StartAutomaticAttempt`. Existing configurations without this list keep automation disabled.

The native receive path currently uses the single shared timer's HTTP reconciliation (15s with jitter
when degraded,60s backoff,300s WS-healthy). A real two-account rule/guard/send/receipt canary passed.
This is not the final packaged client: cold-start unattended sendability, richer trigger/echo/blacklist
parity, primary Frontier WebSocket, native licensing, UI integration and terminal metadata retention
remain separate gates. Never label a read-only inbox success or an old command replay as Sendable.

### Schema4 and unattended account startup

Native query/user plus structured profile UID/sec_uid verification now binds Cookie to account. A
credential-bound cache avoids repeated identity work; failed verification clears readiness. Schema4
persists actual send observations atomically with delivery/guard settlement. Positive evidence has a
5-minute restoration lifetime. Known risk survives restart for the same credential binding, while a
new identity-verified credential generation resets stale risk to Unknown and requires a fresh send ACK.
Missing browser-generated `msToken` or `s_v_web_id` uses a stable session-bound fallback without
rewriting imported Cookie headers; ticket guard may use ECDSA until ECDH certificate refresh succeeds.

A verified/owned Unknown account can now perform its first guarded automatic reply without a manual
warmup, remaining Unknown until the actual acknowledgement. Real cold-start and restart/new-message
canaries passed. Full credential/config reload, Frontier WS, native licensing,
workbench/installer cutover and retention/soak are still unfinished. The read-only `self-probe` tool prints
only verification flags, not credential material.
