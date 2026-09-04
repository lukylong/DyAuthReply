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
- 与 Python 权威 pb2 编码器共用的 PC IM send 黄金语料、有限 protobuf codec、响应解码和
  保守结果分类；
- 与 Python 生产组装路径共用的 HTTP RequestPlan 语料，离线验证独立 query `msToken`、原始
  Cookie、有序 URL/header、空 body A-Bogus 输入、ticket-guard 输入和 signer 输出摘要绑定；
- 只读 `GET /health`。

当前协议模式仍固定为 `shadow-disabled`。此阶段没有抖音网络客户端或发送入口，也不会读取现有
Python 客户端数据库。两份语料只证明合成的 send request/response 字节和 HTTP 请求规划对齐；
Rust 仍未执行真实 A-Bogus、ticket-guard crypto、证书获取或 HTTP/TLS，更不代表收消息或
WebSocket 已完成迁移。默认监听 `127.0.0.1:18765`，与当前客户端端口隔离。

账号状态中的 `paused_auto` 只暂停规则自动回复，不会误伤工作台的手动发送；发送能力、入站
链路和租约状态仍分别判断。

## 本地验证

```bash
cargo fmt --manifest-path dyauthreply-client/agent/Cargo.toml -- --check
cargo clippy --locked --manifest-path dyauthreply-client/agent/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path dyauthreply-client/agent/Cargo.toml
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
与 schema v2 manifest，执行一次有界清理，并在 Health API v4 中报告压力、写入抑制、分段数量、
回收和恢复计数。默认值是后续 10/100/300 账号负载门禁前的保守初值，不是容量承诺。

当前默认 Chat/Audit/Debug 都持久化且不压缩；生产启动始终装载内置 `ZstdCodec`，因此策略关闭新
压缩后仍可读取既有 `.segment.zst`，编码/解码都走流式接口且解码输出受 family 目标大小限制。
每类是否持久化、是否压缩、保存时间、总字节、目标大小、单记录上限和最低分段数均使用独立
策略。临界磁盘水位只抑制可丢弃正文，`core.sqlite3` 正确性事务继续工作；清理后会重新采样磁盘，
High/Critical 状态保持到使用率回落至 low watermark。

`--verify-protocol` 是完全离线的启动门禁：它在解析数据目录、获取实例锁或监听端口之前验证
内嵌 wire 与 HTTP-plan 语料并打印各自 SHA-256、参考提交和用例数。`--check` 与正常启动也会
先执行同一门禁；health 仍返回 `protocol_mode=shadow-disabled`，并分别报告 wire、HTTP-plan
以及聚合 parity 状态。
