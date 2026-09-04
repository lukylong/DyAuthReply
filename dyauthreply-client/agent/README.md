# dy-agent

DyAuthReply 本地 Rust Agent 的迁移基础。目前实现：

- 持久安装 ID、每次启动唯一 boot ID；
- 数据目录 OS 独占锁；
- 正交账号状态模型；
- SQLite 正确性存储（WAL、receipt/checkpoint、fenced outbound batch/segment）；
- 与 Python 权威 pb2 编码器共用的 PC IM send 黄金语料、有限 protobuf codec、响应解码和
  保守结果分类；
- 只读 `GET /health`。

当前协议模式固定为 `shadow-disabled`。此阶段没有抖音网络客户端或发送入口，也不会读取现有
Python 客户端数据库。黄金语料验证只证明合成的 send request/response 字节对齐，不证明
Cookie、签名、HTTP/TLS、收消息或 WebSocket 已完成迁移。默认监听 `127.0.0.1:18765`，与
当前客户端端口隔离。

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
监听地址；非 loopback 地址会被拒绝。

`--verify-protocol` 是完全离线的启动门禁：它在解析数据目录、获取实例锁或监听端口之前验证
内嵌语料并打印 SHA-256、参考提交和用例数。`--check` 与正常启动也会先执行同一门禁；health
仍返回 `protocol_mode=shadow-disabled`，并在独立字段报告离线 parity 状态。
