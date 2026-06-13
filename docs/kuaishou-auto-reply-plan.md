# 快手创作者平台 — 多账号托管 + 自动回复 实施方案

## 一、项目背景与现状

当前项目 (`DyAuthReply`) 是一个 **Django 5.2 + django-ninja** 后端 + **Vue 3 + Element Plus** 前端的多平台账号托管自动回复系统。

### 现有能力

| 平台 | 账号托管 | 消息轮询 | 自动回复 | 评论回复 | 实现方式 |
|------|:--------:|:--------:|:--------:|:--------:|----------|
| 抖音 | ✅ | ✅ | ✅ | — | Playwright 浏览器自动化 |
| **快手** | **待实现** | **待实现** | **待实现** | **附加** | **HTTP API (本方案)** |

### 痛点与方向

> 抖音现有的 Playwright 方案存在 DOM 变更导致选择器失效、反爬检测、资源占用高等问题。
> **快手采用纯 HTTP API 方案**，一次扫码登录导出 Cookie，后续全部走 API 请求。

---

## 二、已掌握的协议信息

通过分析 `cp.kuaishou.com` JS 源码和 GitHub 逆向成果，已确认以下协议细节。

### 2.1 域名体系

| 域名 | 用途 | 签名要求 |
|------|------|:--------:|
| `cp.kuaishou.com` | 创作者平台 (SPA) | __NS_sig3 |
| `jigou.kuaishou.com` | 机构平台 | __NS_sig3 |
| `id.kuaishou.com` | 统一认证中心 | __NS_hxfalcon |
| `open.kuaishou.com` | 开放平台 (不提供 IM API) | OAuth2 |

### 2.2 签名机制

```
请求体 JSON → MD5("{\"page\":1}") → 32位hex
                                    ↓
                        TachyonVM 字节码引擎
                        (kuaiShoSignCore.js)
                                    ↓
                            __NS_sig3 值
```

> 签名算法已由社区逆向 (@gaozhenqiang/kwai-ns_sig3)，可通过 PyMiniRacer (内嵌 V8) 或 Node 子进程执行。

### 2.3 登录流程

```
┌──────────┐   QR Start    ┌──────────┐   扫码    ┌──────────┐
│  Server  │◄─────────────►│  Browser │◄────────►│ 快手APP   │
│          │                │          │           │          │
│  ① POST /rest/c/infra/ks/qr/start                     │
│     ← qrLoginToken + 二维码图片                          │
│                                                         │
│  ② 轮询 POST /rest/c/infra/ks/qr/scanresult             │
│     ← status: 1(已扫码) / 2(已确认) / 3(已过期)         │
│                                                         │
│  ③ 确认后 GET /rest/infra/sts ──→ 获取 session cookies  │
│  ④ GET /pass/kuaishou/login/passtoken ──→ passToken    │
│                                                         │
│  结果: cookies (kwssectoken, kwscode, did)              │
└─────────────────────────────────────────────────────────┘
```

### 2.4 已确认的业务 API

> 注：标记 \* 的端点来自 hackbrowser 登录态爬取，标记 ? 的端点待登录消息页后确认

#### 账号与认证
| API | 方法 | 说明 | 状态 |
|-----|------|------|:----:|
| `/rest/v2/creator/pc/authority/account/current` | POST | 账号权限检查 | ✅ |
| `/rest/cp/works/v2/common/pc/current/user` | POST | 当前用户信息 | ✅ |
| `/rest/cp/creator/pc/home/userinfo` | POST | 首页用户信息 | ✅ |

#### 通知
| API | 方法 | 说明 | 状态 |
|-----|------|------|:----:|
| `/rest/v2/creator/pc/notification/listv3` | POST | 通知列表 | ✅ |
| `/rest/v2/creator/pc/notification/unreadcountv3` | POST | 未读消息数 | ✅ |
| `/rest/v2/creator/pc/notification/readall` | POST | 全部已读 | ✅ |

#### 评论 (附加功能)
| API | 方法 | 说明 | 状态 |
|-----|------|------|:----:|
| `/rest/v/photo/comment/list` | POST | 评论列表 | ✅ |
| `/rest/cp/creator/comment/report/menu` | POST | 评论管理菜单 | ✅ |

#### 私信 IM (核心功能)
| API | 方法 | 说明 | 状态 |
|-----|------|------|:----:|
| `/rest/cp/im/conversation/list` | POST | 私信会话列表 | ❓ |
| `/rest/cp/im/conversation/messages` | POST | 私信历史消息 | ❓ |
| `/rest/cp/im/message/send` | POST | 发送私信 | ❓ |

> 私信 API 端点需在登录 cp.kuaishou.com 后点击「消息」页签抓包确认，或分析 JS bundle 中 lazy-loaded 的 chunk 来提取。

---

## 三、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Django Backend                             │
│                                                                    │
│  ┌────────────────────  Web 管理后台 ──────────────────────────┐  │
│  │  账号管理  │  规则配置  │  回复日志  │  数据统计  │  告警   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌────────────────────  规则引擎 (共享) ────────────────────────┐  │
│  │  关键词匹配 │ 正则匹配 │ 兜底回复 │ 冷却控制 │ 去重/黑名单  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│       │                              │                             │
│       ▼                              ▼                             │
│  ┌──────────────┐           ┌───────────────────┐                 │
│  │ DouyinAdapter│           │ KuaishouAdapter   │                 │
│  │ (现有,        │           │ (本次实现)         │                 │
│  │  Playwright)  │           │  HTTP API + 签名  │                 │
│  └──────┬───────┘           └────────┬──────────┘                 │
│         │                            │                             │
│         ▼                            ▼                             │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │               BasePlatformAdapter (统一接口)                  │  │
│  │  login() │ fetch_messages() │ send_reply() │ is_alive()     │  │
│  │  fetch_comments() │ reply_comment()  (附加)                  │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌───────────────────  Scheduler (APScheduler) ─────────────────┐  │
│  │  消息轮询 Worker  │  Cookie 保活  │  心跳检测  │  统计汇总  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 四、核心接口定义

### 4.1 统一数据结构

```python
@dataclass
class Message:
    msg_id: str           # 消息唯一 ID
    from_user_id: str     # 发送者用户 ID
    from_user_name: str   # 发送者昵称
    text: str             # 消息文本
    timestamp: int        # Unix 时间戳
    conv_id: str = ""     # 会话 ID (私信用)

@dataclass  
class AccountInfo:
    id: int               # 内部 DB ID
    platform: str         # "douyin" | "kuaishou"
    platform_user_id: str # 平台侧用户 ID
    account_name: str     # 账号昵称
    credential: dict      # {"cookies": {...}, "token": "..."}
    last_cursor: str = "" # 消息拉取游标
```

### 4.2 Adapter 接口

```python
class BasePlatformAdapter(ABC):

    async def login(self, account: AccountInfo) -> bool:
        """验证凭证有效性"""

    async def fetch_messages(
        self, account: AccountInfo, cursor: str = None
    ) -> list[Message]:
        """拉取新消息"""

    async def send_reply(
        self, account: AccountInfo, msg: Message, text: str
    ) -> bool:
        """发送消息回复"""

    async def is_alive(self, account: AccountInfo) -> bool:
        """心跳检测"""

    # 附加功能: 评论回复
    async def fetch_comments(self, account, cursor=None) -> list[Comment]: ...
    async def reply_comment(self, account, comment, text) -> bool: ...
```

### 4.3 签名接口

```python
class SignProvider:
    def sign(self, path: str, body: dict) -> str:
        """计算 __NS_sig3 并返回完整 URL"""
        # 例: sign("/rest/cp/im/conversation/list", {"page":1})
        # → "/rest/cp/im/conversation/list?__NS_sig3=xxxxx"

    def compute_raw(self, body: dict) -> str:
        """只返回 sig3 值，不拼 URL"""
```

---

## 五、数据模型

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| `core_kuaishou_account` | 快手托管账号 | `platform_user_id`, `account_name`, `cookie_encrypted` (Fernet), `status` (active/expired/paused), `last_cursor` |
| `core_reply_rule` | 回复规则 (通用, 已有) | `platform`, `account_id`, `keyword`, `match_type`, `reply_text`, `priority` |
| `core_reply_log` | 回复日志 (通用, 已有) | `platform`, `account_id`, `msg_id`, `from_user_id`, `rule_id`, `reply_text`, `created_at` |
| `core_daily_stat` | 日统计 (通用, 已有) | `platform`, `account_id`, `date`, `total_msg`, `total_reply` |

### Cookie 安全存储

```
用户扫码 → Playwright 导出 cookies → json.dumps()
                                          ↓
                               Fernet.encrypt() (AES-128-CBC)
                                          ↓
                               base64 → 存入 DB cookie_encrypted 字段
                                          ↓
                               Worker 启动时 Fernet.decrypt() → httpx 使用
```

---

## 六、Worker 调度流程

```
┌─────────────────────────────────────────────────────┐
│                 KuaishouWorker                       │
│                                                       │
│  ┌─────────────────────────────────────────────┐    │
│  │  主循环 (每账号独立, 间隔 10-25s)              │    │
│  │                                                │    │
│  │  ① is_alive() ──→ 失效? → 通知重登, skip      │    │
│  │       │                                        │    │
│  │  ② fetch_messages(cursor) ──→ list[Message]    │    │
│  │       │                                        │    │
│  │  ③ 逐个处理:                                    │    │
│  │       ├─ 去重检查 (msg_id 已处理?)              │    │
│  │       ├─ 黑名单检查 (from_user_id 在黑名单?)    │    │
│  │       └─ 规则匹配 (关键词/正则/兜底)            │    │
│  │              │                                  │    │
│  │        ④ 命中规则?:                               │    │
│  │              ├─ 冷却检查 (该用户 N 秒内已回复?)  │    │
│  │              └─ send_reply() → 记录日志+统计    │    │
│  │                                                │    │
│  │  ⑤ 更新 cursor → 休眠 → 回到①                  │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

---

## 七、文件结构

```
backend-django/
├── common/
│   └── base_adapter.py               # 统一 Adapter 接口
│
├── core/
│   ├── kuaishou/
│   │   ├── __init__.py
│   │   ├── sign_provider.py          # NS_sig3 签名 (PyMiniRacer/Node回退)
│   │   ├── ks_sign_core.js           # 签名核心 JS (从 kwai-ns_sig3 仓库获取)
│   │   ├── kuaishou_account_model.py # 快手账号模型
│   │   ├── kuaishou_account_api.py   # 账号 CRUD API
│   │   ├── kuaishou_adapter.py       # 快手 Adapter 实现
│   │   ├── login_manager.py          # 一次性扫码登录 (Playwright)
│   │   └── kuaishou_worker.py        # 消息轮询 Worker
│   │
│   ├── kuaishou_compat/
│   │   └── (预留: 评论回复相关)
│   │
│   └── router.py                     # 注册 kuaishou 路由
│
├── scheduler/
│   └── tasks.py                      # 启动/停止 Worker
│
└── requirements/
    └── kuaishou.txt                  # 新增依赖
```

---

## 八、依赖清单

```
# backend-django/requirements/kuaishou.txt

py-mini-racer>=0.6.0      # 内嵌 V8 执行签名 JS (首选)
httpx>=0.27.0              # 异步 HTTP 客户端
playwright>=1.40.0         # 仅用于首次扫码登录
cryptography>=41.0.0       # Fernet 加密 Cookie (已有)
```

---

## 九、实施计划

| 阶段 | 内容 | 预估工时 |
|:----:|------|:--------:|
| **P0** | `base_adapter.py` + `sign_provider.py` + `ks_sign_core.js` 部署 | 1d |
| **P0** | `kuaishou_account_model.py` + Migration + `kuaishou_account_api.py` | 0.5d |
| **P0** | `login_manager.py` — 一次性扫码登录获取 Cookie | 0.5d |
| **P1** | `kuaishou_adapter.py` — 消息拉取 + 发送 + 心跳 | 1d |
| **P1** | `kuaishou_worker.py` — Worker 调度 + 规则引擎对接 | 1d |
| **P1** | Api 测试 —— 私信 API 端点确认与适配 | 0.5d |
| **P2** | 评论回复 (`fetch_comments` + `reply_comment`) | 1d |
| **P2** | 管理后台集成 (账号管理页 + 规则配置页) | 1d |
| **P3** | 多账号压测 + Cookie 保活策略优化 | 1d |

---

## 十、关键风险

| 风险 | 等级 | 缓解措施 |
|------|:----:|----------|
| `__NS_sig3` 算法更新 | 🟡 中 | 监控 JS bundle 变更 + 保留 Node 回退 |
| Cookie 有效期短 | 🟡 中 | 心跳检测 + 过期通知重登 + 钉钉/企微告警 |
| 快手反爬/风控 | 🔴 高 | 拟人化间隔(10-25s随机) + 限制每日回复量 |
| 私信 API 端点变化 | 🟡 中 | Adapter 模式隔离，变更只影响一个文件 |
| `__NS_hxfalcon` 未知 | 🟢 低 | 仅登录时需要，扫码一次即可，不影响运行时 |
