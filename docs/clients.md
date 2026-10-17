# 客户端接入与手动验收

## 配置与真实验证边界

本文件是协议配置条件，**不是已实测的 Hermes/OpenClaw/DeepSeek Harness 配置文件**。不改动既有客户端凭据，也不承诺所有版本兼容。

- 本机运行 ChatPass：`http://127.0.0.1:19840/v1`。
- API Key：显式创建的 `cp_` 网关密钥，不是 OAuth token；模型取自 `GET /v1/models`。
- 原生 Responses：`POST /v1/responses`，缺省流式，可设 `stream:false`。
- Chat：`POST /v1/chat/completions`，缺省非流式，可设 `stream:true`。
- 多轮携带完整历史和工具结果。不能靠更改模型名冒充协议兼容。

## 参数适配

默认 `compatible` 模式过滤预览不支持的调参/元数据字段。`X-ChatPass-Ignored-Fields` 仅列字段名，不含字段值；**过滤不等于实现，尤其不保证 `max_tokens` / `max_output_tokens` 输出上限**。需要严格语义时使用：

```powershell
uv run chatpass serve --compatibility-mode strict
uv run chatpass web --compatibility-mode strict
```

严格模式对被过滤字段返回 400。适配器强制上游 `store:false`、`stream:true`，将 `system` 转为 `developer`；只有成功 `response.completed` 才聚合 JSON 或输出 Chat 成功终态/`[DONE]`。不自动重放推理。

## 支持矩阵

以下是实现及模拟验证范围，并非完整真实上游兼容保证。**空终帧聚合修复：** 最终 `output` 为空时，使用连续索引的 `response.output_item.done` 完成项重建；必须有成功 `response.completed`，不从增量或 EOF 推断成功。非空终帧须与完成项一致；身份冲突、重复调用 ID、缺失完成项会明确失败。已加入非流式／Chat 与工具结果回传的模拟回归，新版真实客户端验收仍待进行，原生流式 Responses 仍是推荐选项。

| 能力 | 原生 Responses | Chat 桥接 |
| --- | --- | --- |
| 流式 / 非流式 | 支持 | 支持 |
| 函数工具调用、并行调用及完整工具轮 | 保留 `call_id` / 参数 / 结果 | `tool_calls` / `tool_call_id` 双向转换 |
| 自定义工具、opaque reasoning 历史 | 保留 | 自定义工具拒绝；reasoning 输出 metadata 不回传 |
| 文本 / 用户图片（含 data URL） | 保留符合输入格式的内容 | 转换为 Responses 内容项 |
| JSON / JSON Schema | `text.format` | `response_format` 转换；`reasoning_effort` 转 reasoning |
| `stream_options.include_usage` | 转发流没有额外合成 usage 帧 | 成功结束前发 usage 帧 |
| `previous_response_id`、后台任务、服务器引用 | 拒绝（即使 null/false） | 拒绝 |
| 命名空间、`additional_tools` | 保留声明及调用 namespace；仅 function/custom 命名空间子工具 | 拒绝，避免命名空间丢失 |
| 网页搜索 | 可转发声明和已完成调用历史，实际使用取决于模型／账号／工作区策略 | 不提供内置搜索桥接 |
| 文件搜索、Code Interpreter、托管 MCP、`tool_search`、旧式 `functions`、stop、多候选 | 不冒充支持 | 拒绝；仅 `n:1` 可在兼容模式过滤 |
| 拒绝、自定义工具或音频输出 | 原生透传/聚合 | 明确 `unsupported_response`，不假成功 |

Chat 输入不接受非空 `refusal`、命名的普通消息及其他不能无损转换的字段。SDK assistant `refusal:null` / `tool_calls:null`、tool 消息 name 可接受；tool 的规范身份仍是调用 ID。依赖 reasoning 连续性的 Agent **优先选择原生 Responses**。

## 按客户端验收

Hermes、OpenClaw、DeepSeek Harness 均需记录实际版本、provider API 类型及请求字段。新增适配逻辑通过模拟测试；随后用户确认 DSH 使用 `gpt-6.1-sol` 时，新会话的问候和原上下文窗口问题均成功。旧会话失败的具体请求差异仍未确认，Hermes/OpenClaw 未实测，不能据此承诺三客户端或非流式/Chat 完整兼容。原生流式 Responses 路由仍是推荐选项。

1. 手动官方授权并发现模型；错误网关密钥返回 401。
2. 单轮：分别请求两种 API 的流式、非流式；文本加 EOF 不算完成。
3. 多轮：携带历史，不使用服务器状态引用。
4. 函数工具轮：无副作用工具，确认调用 ID、函数名、参数和结果关联正确；包括并行工具调用。
5. 原生 reasoning：若模型返回，后续历史保留 opaque 项。
6. 兼容模式：客户端发送 `max_output_tokens` 等字段，记录忽略响应头；严格模式须明确 400。
7. 失败恢复：无效模型、额度、授权、取消及截断流均无假成功；取消不泄漏并发槽。
8. 轮换网关密钥：旧密钥失败，新密钥可用。
9. 三客户端各自重复，不用一个客户端的成功证明另外两个。

## SIWC 生命周期：由用户参与的手动验收

**下面是真实操作说明，不是已经执行的结果。** 启用新版源码需重启原 ChatPass 进程；不要同时启动另一个占用相同端口的网关。确认自己愿意授权及消耗共享计划预算后才测试。现有客户端配置不自动改动；最好建立独立测试 profile，关闭客户端自身的自动重试／计费 fallback，使用自己的本地 `cp_` 密钥，不导入其他工具凭据。

1. 取消首次计划确认：不得打开官方授权或调用模型。再显式授权，检查官方应用名／权限、状态与模型目录；缺计划权限时应「已登录，计划未授权」，不调用推理、不尝试刷新。
2. 浏览器无法打开时，在当前管理会话显式获取继续链接。它不含身份令牌，但仍不可截图、分享或记录；检查过期／完成后消失，普通 `status` 不出现 URL／身份值。
3. 同账号返回授权复用注册；不同账号必须明确退出并选择「下次使用不同账号」或 `logout --new-account`。错误账号／失败候选不得替换旧有效授权。不要直接编辑加密状态或复制刷新令牌制造测试。
4. 退出是实际撤销操作，最后再执行：记录本地退出与远端 confirmed/unconfirmed/not_needed 的区别，网关密钥保持不变；未确认时到 ChatGPT 设置手动断开。已开始的请求可能完成，不能承诺撤回。旧 `cp_` 仍能通过网关鉴权，但没有授权时不能完成新的上游模型／推理请求。
5. 额度／资格失败只记录安全代码、实际 HTTP 状态、请求标识和是否有 Retry-After，不猜恢复时间；503 暂不可用不抹凭据，429 不切换付费 API。流开始后的失败仍可能是 HTTP 200 的 `event:error`，必须检查成功终态，不把 EOF／无 `[DONE]` 当成功。
6. 等待刷新窗口时刷新状态更新秒数，不提前刷新。待验证候选的签名验证仍可执行；不要为了触发窗口错误反复推理、删除凭据或重放旧刷新。

每个客户端和实际版本分别填写，结果只写 PASS／FAIL／未测与安全标识，不保存正文或凭据：

| 客户端／版本／provider API | 原生流式文本 | 非流式文本／工具 | Chat 流式／非流式 | 完整历史＋无副作用工具轮 | 取消／净化错误 | 本轮状态 |
| --- | --- | --- | --- | --- | --- | --- |
| Hermes（待填） | 未测 | 未测 | 未测 | 未测 | 未测 | 未测 |
| OpenClaw（待填） | 未测 | 未测 | 未测 | 未测 | 未测 | 未测 |
| DSH（待填） | 历史有限成功，需重验新版 | 重建已模拟验证，真实未测 | 未测 | 历史有限成功，需重验新版 | 旧会话问题未定位 | 未完成新版验收 |

记录日期、源码版本、客户端版本、实际模型 ID、接口类型、stream 模式及忽略字段名。每一项先只发一个短请求，再由你决定是否做下一项；达到额度、授权错误或截断时暂停，不自动重放。非流式／Chat 的空终帧重建修复仍需新版真实验收，不能用模拟测试或历史生命周期测试宣称真实兼容。

## 排错与报告

- 400 `unsupported_capability` / `invalid_request_error`：检查字段名与支持矩阵；不要绕过状态/托管工具限制。
- 502 `unsupported_response`：输出超出 Chat 桥接能力，切换原生 Responses，不能丢弃工具或拒绝结果。
- `server_is_overloaded`：上游服务繁忙，请稍后手动重试；不需要因此重新授权或更换密钥。非流式失败返回 503；流已开始时保留 HTTP 200，通过流内 `error` 明确失败，不发送成功终态。网关不会自动重放，客户端是否自动重试由其自身配置决定。
- 流内 `error` 且无 `[DONE]`：失败而非正常结束；检查上游与截断情况。
- 401 区分本地密钥与上游授权；403/429 检查资格和额度，不切换付费 API。
- `login_required`：手动重新登录。待验证令牌只恢复网络/JWKS，不重发旧刷新令牌。

### 安全流诊断

重启 ChatPass 加载更新后，已接入上游流的响应带 `X-ChatPass-Request-ID`，由网关随机生成，不信任客户端传入的 ID。流处理失败时，启动终端会输出一条 `ChatPass stream failure` 警告，用同一 ID 关联；成功流不输出该警告。耗时从网关鉴权完成、请求获准处理后计算，包含读取请求及连接上游。

- `category=eof_without_completion`：连接结束但没有完整 `response.completed` 事件。
- `category=transport_error`：读取上游流时发生 HTTP 传输异常。
- `category=upstream_stream_failed`：上游明确返回失败、未完成或错误事件。
- `category=invalid_upstream_stream` / `stream_limit_exceeded`：格式异常或流大小超限。
- `category=upstream_timeout`：请求总时限或上游读取超时。

日志仅包含本地请求 ID、固定错误分类、耗时、白名单最后事件类型/上游错误码以及完整帧计数和字节数。非白名单值显示 `other`，未提供的值显示 `none`；心跳不覆盖最后数据事件。不会记录响应正文、工具参数、凭据、上游原始消息或异常堆栈。帧计数和字节数仅统计已校验完整帧，不代表网络总量。此诊断范围是上游流处理，响应头前失败及 Chat 桥接自身校验失败不在此警告覆盖范围内；不会新增自动重试。

#### 显式开启一次性未知错误码诊断

仅在本人同意查看未知 `code` 时使用。默认关闭；环境变量必须精确为 `CHATPASS_DIAG_UNKNOWN_CODE_ONCE=1`，并在创建网关时读取。停止已有 ChatPass 后，在项目目录的 PowerShell 运行：

```powershell
$env:CHATPASS_DIAG_UNKNOWN_CODE_ONCE = "1"
try { .\start.cmd } finally {
    Remove-Item Env:CHATPASS_DIAG_UNKNOWN_CODE_ONCE -ErrorAction SilentlyContinue
}
```

下一次上游流明确失败且包含未知错误码时，终端额外输出 `ChatPass one-shot code diagnostic request_id=... upstream_code=... capture=consumed`。每个网关实例只捕获一次，并发请求共享这一次机会；成功、已知错误码、断流和无错误码事件不消耗机会。不会主动发起推理，不会将未知码加入 API 响应，不会创建诊断文件。重启且仍带该变量会重新开启；诊断完成后用 Ctrl+C 停止，以上命令会清除变量，然后正常启动。

仅输出 1–80 个 ASCII 字母、数字、下划线、点、冒号或连字符组成的错误码；换行、空格、URL、对象和超长值统一为 `invalid_or_oversized_code`，并消耗这次机会。未知码即使符合格式也来自不可信上游，格式过滤不保证其内容一定不敏感；请先在本机检查，只分享确认为错误标识的码。`message`、`param`、请求/响应正文、凭据和异常堆栈不会输出。这是临时诊断例外，正常模式仍严格使用白名单；定位后关闭即可，无需改变协议或成功判定。

报错时提供对应警告行、客户端版本和接口类型即可，不要上传完整请求/响应抓包。只报告版本、错误码及字段名，不发布密钥/token、回调链接、完整对话或任意上游错误正文。账号资格和扣减规则依据官方账号侧观测，不将模拟测试解释为无限免费。
