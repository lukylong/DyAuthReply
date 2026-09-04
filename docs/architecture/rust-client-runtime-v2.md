# 本地 Rust 客户端运行时 V2

## 1. 决策与边界

本地客户端逐步收敛为 **Tauri 界面 + 单个 Rust Agent**。远程 Django 继续作为控制面，
不执行抖音协议；本地 Agent 是账号协议会话、收消息、回复消息和本地数据的唯一执行者。

```text
Tauri UI / updater / supervisor
             │ authenticated loopback IPC
             ▼
┌──────────────────── dy-agent ────────────────────┐
│ Supervisor ─ CentralTimer ─ FairScheduler        │
│      │              │              │              │
│      ├── AccountActor(account A) ─ WS/HTTP       │
│      ├── AccountActor(account B) ─ WS/HTTP       │
│      └── AccountActor(account N) ─ WS/HTTP       │
│      │                                             │
│ SignerLane ─ ProtocolCodec ─ StorageWriter        │
└──────┬───────────────────────────────┬─────────────┘
       │ core.sqlite3                  │ rolling segments
       ▼                               ▼
 durable correctness state       bounded history/audit/debug

Remote Django control plane
  entitlement / account execution lease / config / card / aggregate health
```

本地运行时不再依赖 Python、Django、PostgreSQL、Redis 或外部 Node 进程。Rust 与当前 Python
实现迁移期间，Python 在完成账号所有权交接前仍是唯一发送方，禁止双发和双写。

## 2. 多账号执行模型

### 2.1 AccountActor 不是操作系统进程

每个托管账号保留一个轻量 AccountActor，它只是 Tokio task 和有界 mailbox，不是独立
进程或线程。平台协议要求账号级连接时，每个活跃账号维持一个账号绑定的 WebSocket；
空闲账号不创建固定轮询 worker。

Actor 只保存小型热状态：账号 ID、凭证代次、租约 epoch、连接状态、游标、退避状态和
少量会话发送上下文。大对象、历史消息和日志不得长期留在 Actor 内存中。

### 2.2 中央调度

一个中央时间轮负责保活、租约续期、重连、延迟回复、清理和状态汇总。任务队列必须有界，
并分为至少四个优先级：

1. 停止、凭证变化、租约丢失等控制任务；
2. 入站消息确认和自动回复；
3. 连接恢复、保活和租约续期；
4. 历史同步、统计、清理等后台任务。

调度器按账号公平取任务，并分别限制全局连接、签名、HTTP、数据库和后台清理并发。
单个异常账号只能进入自己的指数退避和重连预算，不能形成重连风暴或占满全局队列。

## 3. 状态模型

不得再用单个绿色圆点同时代表登录、连接和发送能力。每个账号至少保留四个正交状态轴：

- 生命周期：`stopped / starting / running / paused_auto / draining / faulted`；
- 所有权：`unowned / acquiring / owned / lost / expired`；
- 入站链路：`disconnected / connecting / ws_healthy / http_degraded / backoff`；
- 发送能力：`unknown / sendable / receive_only / risk_controlled / auth_expired`。

界面展示状态由上述轴派生。账号被风控但仍能收消息时必须显示“接收正常，发送受限”，不能显示
“可发送”。状态变化通过本地长连接/事件广播推送给 UI；REST 仅承担初始快照和断线修复，
不再由每个页面重复轮询 `health/all/status`。

## 4. 所有权、防竞争与升级

本机通过数据目录的 OS 独占文件锁保证只有一个 Agent。跨设备由远程控制面签发账号执行租约：

```text
(account_id, owner_instance_id, owner_boot_id, lease_epoch, expires_at)
```

`lease_epoch` 单调递增。发送前、发送状态落库前和重试前都校验同一个 fence；旧 epoch 即使
进程尚未退出也不能产生新副作用。epoch 只能由远程控制面原子签发，本地 SQLite 只安装并
验证已经验签的租约，绝不自行递增出一个可发送的 epoch。租约时间由存储层在执行事务时读取，
业务任务不能传入或复用捕获的旧时间。授权租约和账号执行租约必须分离。

跨设备换主时，更高 epoch 只解决“谁能发送”，不会自动复制旧设备的 outbox。新设备必须先
接收可验证的未完成批次快照，或以旧稳定 client message ID 完成平台对账；状态转移完成前只
允许接收/检查，禁止直接重新生成回复计划。

覆盖安装和自动更新执行明确状态机：

1. `DRAINING` 后拒绝新的回复批次；
2. 完成正在发送的分段，无法判定结果的分段写入 `UNCERTAIN`；
3. 提交 checkpoint，flush 并关闭存储；
4. 写交接 journal，释放账号租约和本机锁；
5. 安装新版本，校验数据库并执行可恢复迁移；
6. 新 boot 获取更高 epoch；
7. 先对 `UNCERTAIN` 做回查，再恢复发送。

强制退出后同样依赖持久批次、稳定 client message ID 和 fence 恢复，不能依赖进程内布尔值。

## 5. 协议与回复正确性

协议迁移不是把 Python 请求简单翻译成 Rust。兼容基线必须冻结并逐项比对：

- URL、query 顺序和编码；
- 原始 Cookie 字节及账号级隔离；
- 有序 headers、设备参数和时间字段；
- protobuf 请求字节、响应字段及未知字段处理；
- 签名输入输出和异常分类；
- Chrome 风格 TLS/HTTP2 行为；
- 平台业务码、登录失效、发送风控和不确定结果。

签名迁移先以黄金样本验证 Rust 原生实现；尚未证明等价的 JavaScript 逻辑只允许放入进程内、
无文件/网络权限的 QuickJS 沙箱，不能继续携带外部 Node runtime。

自动回复按批次持久化：

```text
InboundReceipt[] + payload/pending + page Checkpoint (同一事务)
        │
        ▼
ReplyClaim -> OutboundBatch -> OutboundSegment[0..n]
```

每个 `(account_id, trigger_key)` 只允许建立一个持久回复 claim；response key 与回复正文不一致
时必须报告幂等冲突。所有分段及稳定 client message ID
必须在首次网络发送前落库。分段状态只允许经过受控转换：
`PREPARED -> SENDING -> CONFIRMED | REJECTED | UNCERTAIN | RETRYABLE`。
`RETRYABLE` 只能由 transport 在确认请求字节尚未提交时设置；出现超时或断链时记录
`UNCERTAIN`，必须先回查，禁止盲目重发整批消息。状态 claim 返回原子 `applied` 结果，
重复调用不能再次获得网络发送许可。

## 6. 本地存储与磁盘回收

### 6.1 正确性库

`core.sqlite3` 使用 WAL、`synchronous=FULL`、外键、busy timeout 和单写者。它只保存恢复与
防重所需的小型数据：

- 安装/启动标识、账号和凭证代次；
- 账号租约与 fence；
- checkpoint、入站 receipt、回复 claim；
- 出站 batch/segment、冷却和日限额；
- 最近发送指纹、命令回执、聚合统计；
- 滚动分段 manifest、迁移和清理水位。

首次建库完成后写入独立初始化 marker。已有 marker 却缺少 `core.sqlite3` 时必须停止就绪并
进入备份恢复，禁止静默创建空库，否则稳定 message ID 和 reply claim 会一起丢失。

删除历史正文不能破坏防重、冷却、日限额、回复审计、会话发送上下文或会话摘要。

### 6.2 滚动数据

聊天正文、协议审计和调试事件写入互相独立的日期/大小滚动分段。分段采用临时文件写入、
`fsync`、原子 rename 后再提交 manifest；活跃分段不参与清理。每类数据独立配置：

- 保存天数；
- 最大总字节数；
- 单分段目标大小；
- 最低保留分段数；
- 是否压缩和是否允许关闭正文持久化。

默认建议值必须经过真实负载校准，而不是硬编码为承诺值：应用日志 7 天、协议调试 3 天、
聊天正文 30 天，同时受磁盘字节上限约束。

### 6.3 水位控制

- 低水位：异步删除过期且已封口的分段；
- 高水位：优先删除调试数据，再删除超额历史并暂停低优先级同步；
- 临界水位：停止产生新的可丢弃正文，保留正确性事务和告警；
- 数据库执行短批次清理和增量 checkpoint，禁止在消息高峰做整库 `VACUUM`。

## 7. 远程控制面交互

REST 用于启动 bootstrap、全量快照、修复和审计查询。一个鉴权长连接只承载变化通知和唤醒；
命令序号、确认游标和最终结果仍持久化，因此断线重连不会丢命令。Agent 将账号状态聚合、压缩、
带版本号上报，禁止每账号固定频率打点。

远程不可用时可在已签名账号租约有效期内继续执行；租约到期后当前保守门禁停止该账号全部协议
工作，并在 UI 明确显示 `lease_expired`。以后若要在租约过期后
继续只接收，必须先拆出独立的接收租约并验证不会造成平台连接互踢，不能复用发送租约绕过门禁。

## 8. 交付阶段与门禁

1. **Foundation**：进程身份、单实例锁、正交状态、SQLite 正确性原语、非生产 health；
2. **Parity**：Rust codec/transport/signer 与冻结协议语料逐字节比对，只读 shadow；
3. **Scheduler**：Actor、中央时间轮、有界公平队列、状态事件流和仿真压测；
4. **Canary**：只给一个测试账号转移更高 fence，先手动回复再自动回复；
5. **Storage**：滚动分段、水位清理、故障注入、备份/恢复；
6. **Desktop**：Tauri supervisor、authenticated IPC、drain/handoff、打包；
7. **Removal**：全量稳定后删除本地 Python/Node runtime 与旧轮询路径。

每一阶段都必须可以单独回退。进入 Canary 以前 Rust 没有生产发送入口；删除旧运行时以前，安装包
必须通过冷启动、睡眠恢复、断网、磁盘压力、强杀、覆盖安装，以及 10/100/300 账号仿真门禁。

## 9. 当前 Foundation 的刻意限制

首个实现切片默认监听 `127.0.0.1:18765`，协议模式为 `shadow-disabled`。它不会连接抖音、
不会读取现有客户端数据库、不会抢占现有账号租约、不会发送消息，也不会绑定现有生产端口。
这一限制用于先证明身份、锁、状态和持久化不变量，再进入协议兼容工作。
