# 抖音创作者中心 IM 发送链路分析

更新时间：`2026-04-28`

分析范围：

- 发送消息
- 已读同步
- 会话参与者读索引
- 陌生人链路中的旧接口

说明：

- 本文档建立在同一轮 Proxyman 抓包基础上。
- 敏感字段（cookie、token、证书正文等）全部省略。

---

## 1. 总结

本轮已经明确确认“发送消息”主接口：

- `POST https://imapi.douyin.com/v1/message/send`

同时确认了发送链路旁边的几个重要辅助接口：

- `POST https://imapi.douyin.com/v3/conversation/mark_read`
- `POST https://imapi.douyin.com/v1/conversation/batch_get_conversation_participants_readindex`
- `POST https://imapi.douyin.com/v1/stranger/get_messages`
- `POST https://imapi.douyin.com/v1/stranger/mark_read_conversation`

结论：

- 当前 IM 的“收”和“发”已经都能在协议层看到。
- 你后续完全可以走“协议读 + 协议发 + DOM 兜底”路线。

---

## 2. 已确认发送主接口

### 2.1 `POST /v1/message/send`

完整地址：

```text
https://imapi.douyin.com/v1/message/send
```

抓到的样本：

- `ID 2163`
- `ID 2211`

请求特征：

- 方法：`POST`
- `Content-Type: application/x-protobuf`
- `Accept: application/x-protobuf`
- `Origin: https://creator.douyin.com`
- `Referer: https://creator.douyin.com/`
- 依赖 cookie

响应特征：

- 状态：`200`
- 响应也是 protobuf
- 返回头中带：
  - `bd-ticket-guard-result: 1101`
  - `x-ms-token: ...`

关键判断：

- 这条就是当前私信发送主接口，不是猜测接口。
- 当前仓库 `HttpProtocolTransport` 里预留的 `send_message` 方向是正确的，但 URL 还不对。

当前代码里写的是：

- [http_protocol.py](/Users/long/Work/DyAuthReply/backend-django/core/douyin/runtime/transport/http_protocol.py:60)

现状：

```python
"send_message": {
    "method": "POST",
    "url": "https://imapi.douyin.com/v1/message/send",
}
```

这一条现在已经被抓包确认，可从“占位接口”升级为“真实接口”。

---

## 3. 与发送链路相关的辅助接口

### 3.1 `POST /v3/conversation/mark_read`

完整地址：

```text
https://imapi.douyin.com/v3/conversation/mark_read
```

作用：

- 进入会话后标记 conversation 已读

为什么重要：

- 如果你未来只做“协议发消息”，但不做“协议已读”，页面状态和数据库状态会一直错位。
- 如果 worker 只读不 ack，也会造成未读反复出现。

结论：

- `mark_read` 应该与 `get_by_conversation` 一起接入，不应单独遗漏。

---

### 3.2 `POST /v1/conversation/batch_get_conversation_participants_readindex`

完整地址：

```text
https://imapi.douyin.com/v1/conversation/batch_get_conversation_participants_readindex
```

作用推断：

- 按名字看，是批量获取 conversation 参与者读索引
- 很可能用于 UI 展示“谁已读到哪条”或本地读位置同步

当前价值：

- 不是第一优先级
- 但如果你后面想做更精确的消息去重、读位校准、消息状态同步，这条很有用

结论：

- 先记录，不必在第一阶段强行接入

---

## 4. 陌生人链路中的旧接口

### 4.1 `POST /v1/stranger/get_messages`

完整地址：

```text
https://imapi.douyin.com/v1/stranger/get_messages
```

作用：

- 看名字是“陌生人消息拉取”

现象：

- 在较早的一段流量里出现过
- 后续主要交互中更多出现的是：
  - `get_conversation_list`
  - `get_by_user`
  - `get_by_conversation`

判断：

- 这更像一条旧接口或陌生人分支专用接口
- 不应该作为主收件箱统一接口优先实现

### 4.2 `POST /v1/stranger/mark_read_conversation`

完整地址：

```text
https://imapi.douyin.com/v1/stranger/mark_read_conversation
```

作用：

- 陌生人会话的已读确认

判断：

- 这说明“陌生人分组”在协议层可能有专门分支
- 当前主链路里的 `v3/conversation/mark_read` 未必覆盖所有陌生人场景

结论：

- 如果你第一阶段先做通用会话链路，可以先接 `v3/conversation/mark_read`
- 等稳定后，再补陌生人专用分支

---

## 5. 发送链路的实现含义

### 5.1 为什么这对当前项目很重要

当前项目发送仍然主要靠 DOM：

- [sender.py](/Users/long/Work/DyAuthReply/backend-django/core/douyin/runtime/sender.py:1)

这会带来：

- 输入框选择器漂移
- 页面结构变化
- 确认发送成功依赖气泡渲染
- 多账号并发时更容易发生页面串扰

而协议发送一旦可用，优势很明确：

- 不依赖输入框 DOM
- 不依赖按钮点击
- 不依赖页面前台焦点
- 能和协议读取链路统一到同一个 transport 里

### 5.2 为什么还不能完全脱浏览器

虽然 `send` 已经抓到了，但当前仍然不适合完全脱浏览器，原因是：

- 请求是 protobuf
- 仍然依赖 cookie
- 浏览器上下文会自动参与签名环境
- 响应头里存在票据/风控相关字段，例如：
  - `bd-ticket-guard-result`
  - `x-ms-token`

因此当前最佳路线仍是：

- 浏览器保留为签名/登录态容器
- Python 负责协议收发

这与现有 `SignProvider` 设计吻合：

- [sign_provider.py](/Users/long/Work/DyAuthReply/backend-django/core/douyin/runtime/transport/sign_provider.py:149)

---

## 6. 现有代码的校对结论

### 6.1 已经对上的点

当前 transport 骨架里已经有：

- `send_message`
- `get_by_conversation`
- `list_conversations`

说明大方向正确。

### 6.2 需要补全的接口

发送相关：

- `POST /v1/message/send`

读位相关：

- `POST /v3/conversation/mark_read`
- `POST /v1/conversation/batch_get_conversation_participants_readindex`

陌生人兼容分支：

- `POST /v1/stranger/get_messages`
- `POST /v1/stranger/mark_read_conversation`

### 6.3 当前优先级建议

建议优先级：

1. `send`
2. `mark_read`
3. `get_by_user`
4. `get_by_conversation`
5. `get_conversation_list`
6. `batch_get_conversation_participants_readindex`
7. `stranger/*`

理由：

- `send` 一旦稳定，最先能减少 DOM 发送失败与重复回复
- `mark_read` 不补的话，协议读取后的未读状态会失真
- `stranger/*` 可以先作为补充分支，不必第一阶段强推

---

## 7. 当前仍未锁定的点

虽然 `send` 已确认，但还有几件事仍未完全明确：

- protobuf 字段结构
- 发送普通文本和发送带链接文本是否共用同一消息体 schema
- 发送成功后响应里的 message id / server ack 结构
- 是否还有额外的发送回执接口

也就是说：

- “接口名”已经知道
- “字段级协议”还需要继续解码或比对

---

## 8. 推荐的下一步分析动作

接下来最值得做的是：

1. 对 `send` 请求体做 schema 对账
2. 对 `send` 响应体做解码
3. 比较：
   - 纯文本消息
   - 带链接消息
   - 多段消息
4. 观察发送后是否还会紧跟：
   - `mark_read`
   - `readindex`
   - 额外的 ack / sync 请求

---

## 9. 结论摘要

本轮已经明确确认：

- 发送消息主接口：`POST https://imapi.douyin.com/v1/message/send`
- 已读接口：`POST https://imapi.douyin.com/v3/conversation/mark_read`
- 读索引接口：`POST https://imapi.douyin.com/v1/conversation/batch_get_conversation_participants_readindex`
- 陌生人旧链路仍然存在：`/v1/stranger/get_messages`、`/v1/stranger/mark_read_conversation`

这意味着：

- 当前仓库的协议化发送路线已经具备继续推进的基础
- 下一阶段不该再花主要精力强化 DOM 发送
- 应优先把 `send + mark_read + get_by_user/get_by_conversation` 做成闭环

