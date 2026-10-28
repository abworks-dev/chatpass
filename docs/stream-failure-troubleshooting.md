# 流失败与 Windows 连接关闭异常

## 这次日志能证明什么

报告的 `request_id=81aa55c1f620450eacac5b0a776f0f31` 对应：

```text
category=upstream_stream_failed elapsed_ms=13547 last_event=error upstream_code=other frames=184 bytes=211040 timeout_phase=none
```

网关已经解析到上游的 `error` 数据事件，收到 184 个完整帧后终止，耗时约 13.5 秒。它不是本地超时，也不是仅凭 EOF/读连接断开判断出的失败。`other` 仅说明错误码未被白名单识别（也可能是非字符串等异常值），不能认定是额度、过载或某种工具调用问题。安全诊断不保存原始错误正文，不能事后恢复这次未知码。

同一终端中的 Python 3.11.9 异常：

```text
Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)
... self._sock.shutdown(socket.SHUT_RDWR)
ConnectionResetError: [WinError 10054]
```

属于 Windows Proactor **连接关闭阶段**。在所检查的标准库实现中，此处 `shutdown()` 失败会跳过后续 socket close、server detach 和完成标志设置。事件循环捕获并打印回调异常后仍可继续运行；不等于整个服务进程退出，但也不应简单宣称“只有无害日志”。

两个日志缺少相同连接的关联标识，**尚不能证明它们来自同一个 socket，也不能证明关闭异常导致了上游的错误事件**。模拟测试中，单纯 HTTPX 读重置会显示 `category=transport_error`；读超时会显示 `upstream_timeout`。真实问题可能存在共同触发因素，但目前没有足够证据。

## 可选绕开 Proactor 关闭路径

保持默认 `auto` 不变。需要排查 Windows 关闭异常时，先 Ctrl+C 停止旧 ChatPass，再在项目目录运行：

```powershell
.\start.cmd --event-loop selector
```

或直接运行：

```powershell
uv run --locked --no-dev --system-certs chatpass web --event-loop selector
uv run --locked --no-dev --system-certs chatpass serve --event-loop selector
```

`web` 与 `serve` 二选一，不同时占用端口。`--event-loop selector` 通过 Uvicorn 显式指定 `asyncio:SelectorEventLoop`，不是修改系统/进程全局 loop policy，不修改 Python 标准库，不屏蔽异常，也不启用 reload/workers。未指定参数或指定 `--event-loop auto` 时维持原行为。

自定义 loop factory 需 Uvicorn 0.36+；本次实际环境为 0.54.0。旧版本会在访问凭据前明确拒绝此选项，不会默默忽略。项目原有默认启动仍支持其声明的较旧 Uvicorn 范围。不要把 `--loop asyncio` 或修改全局 policy 当成等价 workaround：当前 Uvicorn 的 asyncio factory 在 Windows 单进程模式仍会直接选择 Proactor。

Selector 是本机 socket-only 服务的**可选运行方式**，不是对上游服务错误的修复。Windows 下有 select/socket 规模限制，不支持 asyncio subprocess/pipe；未来若新增这些能力，应重新验证，不应扩大成通用运行时默认。还原方式为停止服务后正常启动（不带该参数）。

## 捕获真正的上游未知错误码

如果出现相同的 `upstream_code=other`，需要再次显式开启现有的一次性诊断。可以和 Selector 一起启动：

```powershell
$env:CHATPASS_DIAG_UNKNOWN_CODE_ONCE = "1"
try { .\start.cmd --event-loop selector } finally {
    Remove-Item Env:CHATPASS_DIAG_UNKNOWN_CODE_ONCE -ErrorAction SilentlyContinue
}
```

下一次未知错误额外输出 `ChatPass one-shot code diagnostic request_id=... upstream_code=... capture=consumed`。只在本机检查 `code`；不输出 message、对话、工具参数或凭据。机会在本次网关实例中消耗后不会自动恢复。符合标识符格式的未知值仍来自不可信上游，分享前先检查，不上传管理链接、token 或完整抓包。如果没有这行日志，报告是否已消耗机会，不要把缺失信息推断为服务过载。

文件修改或工具执行已完成的部分不会因为后续模型请求失败而自动回滚；先检查现有工作再手动继续，避免重复执行有副作用的操作。本项目不增加自动重放，也不伪造 `[DONE]` 或完成事件。

## 证据与边界

- 当前代码检查：HEAD `dc9a9f9`，原工作区干净；本次只新增显式运行方式及回归/说明，不修改推理协议、授权、模型参数或默认重试策略。
- 独立只读复现使用真实 `_ProactorBasePipeTransport` 和模拟 reset socket，在 Python 3.11.9 与现成 3.11.15 上都复现跳过关闭清理；正常对照完成清理。安装的 3.12 源码也具有同一未保护路径，因此**没有证据支持“升级到这些版本就会修好”**。
- 每个运行时的三个真实 localhost RST 对照未自然触发相同 shutdown 错误。人工注入证明回调机制及后果，不证明自然发生率、持续泄漏或本次上游错误的因果关系。
- 合成 184 帧上游错误、HTTPX ReadError 和 ReadTimeout 三种对照验证了错误分类、不重试及资源关闭；未调用真实账号/上游。
- [运行方式回归](../tests/test_event_loop.py)覆盖两种 CLI、默认不变、旧版 Uvicorn 拒绝、真实本机 HTTP/TLS SSE 和回调错误检查。针对性组合测试 90 项通过；最终全量 Python 回归 843 项通过、3 项显式可选测试跳过，Ruff 与差异空白检查通过。不声称真实客户端、上游负载或所有断连模式已被验证。
