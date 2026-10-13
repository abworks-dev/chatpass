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

以下是实现及模拟验证范围，并非完整真实上游兼容保证。**已知真实上游缺口：** 工具调用已在 `response.output_item.done` 中返回，但终帧 `response.completed.response.output` 可为空；当前聚合器只使用终帧，因此非流式可能丢失输出，Chat 终态核对也可能失败。此缺口尚未修复，暂优先使用原生流式 Responses。

| 能力 | 原生 Responses | Chat 桥接 |
| --- | --- | --- |
| 流式 / 非流式 | 支持 | 支持 |
| 函数工具调用、并行调用及完整工具轮 | 保留 `call_id` / 参数 / 结果 | `tool_calls` / `tool_call_id` 双向转换 |
| 自定义工具、opaque reasoning 历史 | 保留 | 自定义工具拒绝；reasoning 输出 metadata 不回传 |
| 文本 / 用户图片（含 data URL） | 保留符合输入格式的内容 | 转换为 Responses 内容项 |
| JSON / JSON Schema | `text.format` | `response_format` 转换；`reasoning_effort` 转 reasoning |
| `stream_options.include_usage` | 转发流没有额外合成 usage 帧 | 成功结束前发 usage 帧 |
| `previous_response_id`、后台任务、服务器引用 | 拒绝（即使 null/false） | 拒绝 |
| 托管工具、旧式 `functions`、stop、多候选 | 不冒充支持 | 拒绝；仅 `n:1` 可在兼容模式过滤 |
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

## 排错与报告

- 400 `unsupported_capability` / `invalid_request_error`：检查字段名与支持矩阵；不要绕过状态/托管工具限制。
- 502 `unsupported_response`：输出超出 Chat 桥接能力，切换原生 Responses，不能丢弃工具或拒绝结果。
- 流内 `error` 且无 `[DONE]`：失败而非正常结束；检查上游与截断情况。
- 401 区分本地密钥与上游授权；403/429 检查资格和额度，不切换付费 API。
- `login_required`：手动重新登录。待验证令牌只恢复网络/JWKS，不重发旧刷新令牌。

只报告版本、错误码及字段名，不发布密钥/token、回调链接、完整对话或任意上游错误正文。账号资格和扣减规则依据官方账号侧观测，不将模拟测试解释为无限免费。
