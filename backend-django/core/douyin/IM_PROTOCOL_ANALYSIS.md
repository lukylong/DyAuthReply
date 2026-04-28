# 抖音创作者中心 IM 协议抓包分析

更新时间：`2026-04-28`

分析来源：

- 使用本机 Proxyman 抓取 `creator.douyin.com/creator-micro/data/following/chat`
- 包含的用户操作：
  - 打开私信页
  - 点击聊天列表
  - 进入具体用户会话
  - 拉取聊天记录
  - 标记已读
  - 查看用户详情
  - 进行了发送消息/接收消息等操作

注意：

- 本文档只记录接口形态、职责、主机、路径、协议类型和与当前代码的差异。
- 不记录任何真实 token、cookie、私钥、证书正文等敏感值。
- 本轮“发送消息”的接口尚未完全锁定；本文先聚焦已明确确认的收件箱与会话链路。

---

## 1. 总体结论

当前抖音创作者中心私信页不是纯 DOM 渲染驱动，而是明显存在一套稳定的 IM 协议链路。

抓包结果显示，IM 流量分成两段：

1. 页面初始化阶段
2. 用户交互阶段（点击会话、切会话、拉消息、标已读）

这两段使用的接口主机和接口名不完全相同。

### 1.1 初始化阶段

核心接口：

- `GET https://creator.douyin.com/aweme/v1/creator/im/user_token/`
- `GET https://creator.douyin.com/aweme/v1/creator/im/user_token/v2/`
- `POST https://imapi.snssdk.com/v2/message/get_by_user_init`

说明：

- `user_token/v2` 比 `v1` 返回更多字段，包括 `sdk_cert`、`ts_sign`。
- `get_by_user_init` 请求与响应都是 `application/x-protobuf`。
- 这条链更像页面刚进入 IM 时的 bootstrap 初始化。

### 1.2 用户交互阶段

核心接口：

- `POST https://imapi.douyin.com/v1/stranger/get_conversation_list`
- `POST https://imapi.douyin.com/v1/message/get_by_user`
- `POST https://imapi.douyin.com/v1/message/get_by_conversation`
- `POST https://imapi.douyin.com/v3/conversation/mark_read`

说明：

- 这一套更像真正的“聊天列表 + 进入会话 + 拉会话消息 + 已读确认”主业务接口。
- 当前抓到的频率很高，且与手工点击会话行为强相关。

---

## 2. 接口分层图

```text
creator.douyin.com
  ├─ /aweme/v1/creator/im/user_token
  ├─ /aweme/v1/creator/im/user_token/v2
  ├─ /aweme/v1/creator/im/user_detail
  ├─ /aweme/v1/creator/relation/info
  ├─ /aweme/v1/creator/relation/multi/is/follower
  └─ /aweme/v1/creator/check/user

imapi.snssdk.com
  └─ /v2/message/get_by_user_init

imapi.douyin.com
  ├─ /v1/stranger/get_conversation_list
  ├─ /v1/message/get_by_user
  ├─ /v1/message/get_by_conversation
  └─ /v3/conversation/mark_read
```

---

## 3. 已确认接口职责

### 3.1 IM token 初始化

#### `GET /aweme/v1/creator/im/user_token`

作用：

- 返回基础 IM token 和 `user_id`
- 是进入 IM 业务的基础准备接口

返回特征：

- `status_code`
- `token`
- `user_id`

#### `GET /aweme/v1/creator/im/user_token/v2`

作用：

- 返回更完整的新版本 IM 鉴权材料

比 `v1` 多出的关键字段：

- `sdk_cert`
- `ts_sign`

结论：

- 协议化时不能只接 `v1`
- `v2` 很可能是后续 IM API 或浏览器 SDK 的完整票据来源

---

### 3.2 初始化拉取消息

#### `POST https://imapi.snssdk.com/v2/message/get_by_user_init`

作用：

- 页面初次进入 IM 时的初始化消息拉取

请求特征：

- `Content-Type: application/x-protobuf`
- `Accept: application/x-protobuf`
- `Origin: https://creator.douyin.com`
- 依赖浏览器内 cookie 上下文

结论：

- 这不是后续点击会话时的主接口
- 但它是 IM 页面初始化流程中的关键组成部分

---

### 3.3 会话列表

#### `POST https://imapi.douyin.com/v1/stranger/get_conversation_list`

作用：

- 拉取聊天会话列表
- 至少在当前抓包里，它是会话列表展示的主接口

请求特征：

- protobuf body
- protobuf response
- 同样来自 `creator.douyin.com` 页面的跨域调用

结论：

- 当前代码里预留了这个 endpoint，方向正确
- 这是协议化“替代 DOM 扫列表”的第一优先级接口之一

---

### 3.4 进入会话后拉消息

#### `POST https://imapi.douyin.com/v1/message/get_by_user`

作用：

- 点击会话后按“对端用户”拉取消息
- 很像会话首屏消息加载接口

结论：

- 当前代码没有显式覆盖这条接口
- 它应该纳入协议化实现

#### `POST https://imapi.douyin.com/v1/message/get_by_conversation`

作用：

- 按 conversation 维度继续拉取会话消息
- 更接近历史消息、翻页、稳定按会话 ID 读取

结论：

- 当前代码骨架已预留该接口
- 这是后续替代 DOM 读取消息气泡的核心接口之一

---

### 3.5 会话已读确认

#### `POST https://imapi.douyin.com/v3/conversation/mark_read`

作用：

- 进入会话后标记该 conversation 已读

结论：

- 当前代码实现中没有这层逻辑
- 如果未来收件箱读取改走协议，不补 `mark_read` 会导致：
  - 页面未读状态和本地状态不一致
  - 重复处理边界更混乱

---

### 3.6 用户详情补全

#### `POST https://creator.douyin.com/aweme/v1/creator/im/user_detail/`

请求体特征：

- `Content-Type: application/json`
- body 里直接传 `user_ids: [...]`

返回内容特征：

- 昵称
- 头像
- ShortId
- 签名
- SecretUseId

作用：

- 批量补齐会话对端用户信息

结论：

- 当前很多昵称/头像依赖 DOM 解析
- 这条接口可以直接替代大量页面文本猜测

---

### 3.7 关系与身份辅助接口

已确认：

- `POST /aweme/v1/creator/relation/multi/is/follower/`
- `GET /aweme/v1/creator/relation/info/`
- `GET /aweme/v1/creator/check/user/`

作用：

- 判断是否粉丝
- 查询用户关系
- 校验用户/账号身份

说明：

- 这些接口不是 IM 消息主链路
- 但会影响 UI 展示和后续策略判断

---

## 4. 与当前代码的差异

### 4.1 当前已存在的协议化方向

仓库里已经有 protocol transport 骨架：

- [runtime/transport/http_protocol.py](./runtime/transport/http_protocol.py)
- [runtime/transport/sign_provider.py](./runtime/transport/sign_provider.py)

当前 `_ENDPOINTS` 中已预留：

- `send_message`
- `get_by_conversation`
- `list_conversations`

这说明路线没有错，说明已经朝正确方向走了。

### 4.2 当前抓包确认但代码还没补上的接口

缺失或未显式接入：

- `imapi.snssdk.com/v2/message/get_by_user_init`
- `imapi.douyin.com/v1/message/get_by_user`
- `imapi.douyin.com/v3/conversation/mark_read`
- `creator.douyin.com/aweme/v1/creator/im/user_detail/`

### 4.3 当前 DOM 方案的局限

当前收件箱逻辑仍然以 DOM 扫描为主：

- [runtime/inbox.py](./runtime/inbox.py)

现状问题：

- 会话列表靠页面结构和文本解析
- 消息方向靠 class 名猜测
- 消息唯一键仍然是弱推断
- 页面结构一变就容易失效

而本轮抓包已证明：

- 会话列表、消息拉取、已读确认都有协议接口
- DOM 可以退为兜底，不应再是唯一数据源

---

## 5. 当前最可靠的协议化优先级

建议按这个顺序做：

1. `get_conversation_list`
2. `get_by_user`
3. `get_by_conversation`
4. `mark_read`
5. `im/user_detail`
6. `get_by_user_init`

原因：

- 先把“列表 -> 点开 -> 拉消息 -> 标已读”闭环跑通
- 再把初始化 bootstrap 接回去
- 这样即使发送链路仍然走 DOM，收件箱读取也已经能稳定很多

---

## 6. 目前仍未完全锁定的部分

本轮尚未稳定确认：

- 发送消息真实接口
- 链接消息发送接口
- 图片/卡片消息接口
- 是否存在稳定 WebSocket 增量消息通道

说明：

- 不是说这些不存在
- 而是当前这轮样本里，还没把它们从监控/埋点/页面噪音中明确分离出来

---

## 7. 技术特征与实现启示

### 7.1 协议类型

- `imapi.*` 主业务接口大量使用 protobuf
- `creator.douyin.com` 辅助接口多为 JSON

### 7.2 鉴权特征

`creator.douyin.com` 自身接口通常带：

- `x-secsdk-csrf-token`
- `msToken`
- `a_bogus`
- 完整 cookie

`imapi.*` 接口通常带：

- protobuf body
- 浏览器 cookie
- `Origin: https://creator.douyin.com`

### 7.3 对当前架构的启发

当前 `SignProvider` 保留浏览器上下文做签名页的方案是合理的：

- 短期不适合完全脱浏览器
- 更现实的方案是：
  - 浏览器负责 cookie / 签名环境
  - Python 负责主业务 HTTP / protobuf 协议

---

## 8. 建议的下一轮抓包目标

下一轮应只聚焦“发送链路”，建议动作：

1. 在现有会话里发送一条纯文本
2. 再发送一条带链接文本
3. 再让对方回复一条消息
4. 切换不同会话分组（陌生人 / 朋友私信 / 全部）

重点观察：

- 新出现的 `imapi.* send*` 接口
- 是否有新的 `message/ack` / `send_receipt` 类接口
- 是否有长连接 / WebSocket IM 帧

---

## 9. 结论摘要

本轮抓包已经足以确认：

- 当前项目继续完全依赖 DOM 扫描已经没有必要
- 抖音创作者中心 IM 的核心读取链路是可见的
- 协议化第一阶段应优先覆盖“会话列表 + 会话消息 + 已读”
- 当前代码路线正确，但覆盖面还不够

最关键的 4 条待接入接口：

- `POST https://imapi.douyin.com/v1/stranger/get_conversation_list`
- `POST https://imapi.douyin.com/v1/message/get_by_user`
- `POST https://imapi.douyin.com/v1/message/get_by_conversation`
- `POST https://imapi.douyin.com/v3/conversation/mark_read`

