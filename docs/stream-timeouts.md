# 分阶段超时 / Phase-separated upstream timeouts

## 为什么修改

旧策略将请求准备阶段的 `timeout_seconds=120` 同时用于上游响应流的绝对总期限。即使上游不断发出有效 SSE 心跳、分片或模型输出，超过这一期限仍会中断。`Upstream request timed out.` 无法区分请求准备、等待响应头、读流停顿和流总时限。

现在由 gateway 统一管理分阶段时间预算；不改请求内容、不重放推理请求、不自动切换账户、不生成新的下游心跳。真正的上游过载、断流、错误帧仍然报错，而非伪造成功。

## 默认值和配置

| 阶段 | Settings 字段 | `serve` / `web` 参数 | 默认秒数 | 可设置秒数 |
| --- | --- | --- | ---: | --- |
| 请求准备、鉴权及管理/模型目录请求 | `timeout_seconds` | `--preparation-timeout` | 120 | 1–600 |
| 连接、连接池等待及写入请求 | `connect_timeout_seconds` | `--connect-timeout` | 10 | 1–600 |
| 发起上游请求到收到响应头 | `response_header_timeout_seconds` | `--response-header-timeout` | 600 | 1–3600 |
| 每次等待读取上游数据 | `stream_idle_timeout_seconds` | `--stream-idle-timeout` | 180 | 1–3600 |
| 收到响应头后的流绝对总时间 | `stream_total_timeout_seconds` | `--stream-total-timeout` | 1800 | 0–86400 |

所有配置必须为有限数值。`--stream-total-timeout 0` **只关闭流总时限**，并不会关闭空闲、连接、响应头或准备阶段的保护。默认总时限保留 30 分钟安全边界，避免只发心跳的上游无限占用槽位。

```powershell
chatpass serve --response-header-timeout 600 --stream-idle-timeout 180 --stream-total-timeout 1800
chatpass web --no-browser --preparation-timeout 120 --connect-timeout 10 --stream-total-timeout 1800
```

改变 CLI 参数需要重新启动 ChatPass 服务；现有进程不会自动取得新配置。以上是 ChatPass 的启动参数，不是调用端请求 JSON 参数。服务仍仅监听 loopback，不改变认证、凭据存储或官方上游地址。

## 流和取消语义

- 响应头等待有独立的绝对等待期限，不占用流总时长。HTTPX 共用的 transport read 限制取响应头/读空闲预算的较大值，避免响应头配置较短时误截断响应正文；实际阶段限制由 gateway 各自的计时范围执行。
- 收到响应头后，准备期限不再控制响应流。原始上游数据（包含心跳、注释和未完整成帧的分片）使下一次读等待重新获得空闲预算。
- 下游写入较慢时，不会被当作上游读空闲；可选流总时限仍然限制整个流处理过程。
- 空闲限制适用于 Responses / Chat Completions 的流式和非流式收集，也适用于缺少 Content-Type 时的 SSE 预读识别；预读阶段同样受总时限约束。
- 不生成下游保活，不无限缓存，不放宽已有请求/帧/流大小限制。客户端断开仍会取消上游工作并释放响应和并发槽位。
- 旧的自定义 duck-typed settings 对象若没有新字段，采用原有 `timeout_seconds` 作为相应阶段的值。正式 `Settings` 和 CLI 总是提供以上显式默认值。

## 如何定位超时

兼容原错误码 `upstream_timeout`，HTTP 响应开始前返回 504；SSE 已开始时发送明确错误事件，不发送成功完成或 `[DONE]`。错误中新增安全枚举 `timeout_phase`，并保留 `X-ChatPass-Request-ID` 用于关联日志。

| `timeout_phase` | 含义 / 优先检查 |
| --- | --- |
| `preparation` | 请求准备、鉴权或凭据读取未在预算内完成 |
| `request` | 管理/模型目录等沿用已有通用预算的请求 |
| `connect` / `pool` / `write` | 连接建立、连接池等待或请求写入超时 |
| `response_headers` | 上游没有及时返回响应头；可能是排队或网络问题，但此字段不证明具体原因 |
| `stream_idle` | 上游长时间未提供下一批数据；可能是停顿、网络或上游未发心跳 |
| `stream_total` | 流已达到显式总时限，即使有心跳也会停止 |

安全日志记录阶段、耗时、请求关联 ID 和已有流计数，不记录 HTTPX 原始异常、请求/响应正文、Cookie、token 或凭据。不要把 `server_is_overloaded` 误判为本地超时；前者是上游明确报告的过载，延长超时不会消除过载。

调用端（包括 DSH）、反向代理或客户端 SDK 若有自己的 120 秒总时限、首输出 watchdog 或读空闲限制，仍可能先终止请求。此改动只修正 ChatPass 自身的计时；需要另外核对调用端配置。上游心跳只证明有数据活动，不保证模型已产生正文。没有完整上游完成事件时不能声称成功。

## 参考与验证范围

设计参考 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)，固定参考提交 `4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1`，静态源码研究而非实际服务验证：

- [HTTP upstream transport](https://github.com/Wei-Shaw/sub2api/blob/4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1/backend/internal/repository/http_upstream.go)：连接限制与响应头等待分开，客户端不共用一个总时限。
- [Config defaults](https://github.com/Wei-Shaw/sub2api/blob/4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1/backend/internal/config/config.go)：通用响应头默认 600 秒、流空闲默认 180 秒；具体 OpenAI profile 的默认值另有差异，不声称整个项目统一使用此数值。
- [Response stream handling](https://github.com/Wei-Shaw/sub2api/blob/4d9ac3e482106d2da3a39fe3e546d8d6801f1cc1/backend/internal/service/openai_gateway_response_handling.go)：上游活动与下游保活职责分离。

独立实现该思路，没有复制 Sub2API 源码、无限响应头等待、自动 failover 或计费 drain 策略。重点回归见 [test_timeouts.py](../tests/test_timeouts.py)：短准备预算下的持续输出、读空闲、原始碎片、心跳触发总时限、headerless 预读、禁用总时限仍保留空闲保护、HTTPX 阶段分类、日志安全、槽位释放及两个 CLI 入口。使用缩短预算的合成 HTTPX/ASGI 上游；未执行真实 OAuth 或推理请求，不能证明线上上游延迟或过载已经消失。
