# ChatPass

**简体中文** | [English](README.en.md)

**将自己的 ChatGPT 订阅接入 Agent 工具。**

本地优先的 Responses API 网关：客户端只持有 ChatPass 访问密钥，官方 OAuth 凭据保存在网关端。

> **状态：初始实现，已进行模拟测试；尚未完成真实账号和客户端实测。** 不承诺所有订阅账号均有资格、无限额度，或 Hermes / OpenClaw / DeepSeek Harness 已可直接接入。服务器部署只是未来边界，本版拒绝非回环监听。

## 已实现的范围

- 官方公开授权协议：浏览器授权、PKCE、state/nonce、动态客户端标识、签名 ID token 校验。
- 系统安全存储保存主密钥，AES-GCM 加密磁盘状态；没有明文凭据降级。
- 单账号凭据刷新，跨进程文件锁，轮换令牌检查点；不自动重试单次授权码或刷新请求。
- 独立的固定网关访问密钥，显式创建或轮换。
- 受鉴权保护的 `GET /v1/models` 与流式 `POST /v1/responses`。
- 请求体、并发、超时、流帧和总流大小限制；错误不输出上游凭据或任意错误正文。
- 不导入其他工具的凭据，不切换按量计费 API，不做账号池、转售或多厂商聚合。

上述实现通过本地模拟协议验证，**不等于官方批准、账号可用或客户端兼容**。验证边界见 [验证说明](docs/verification.md)。

## 本地 Web 管理台

管理台基于 [Tabler](https://github.com/tabler/tabler)（MIT，固定版本 1.6.1）改造，静态资源随 Python 包提供，不使用 CDN、远程字体或遥测。模板来源与许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

### 推荐：Windows 双击启动

先安装 [uv](https://docs.astral.sh/uv/)，然后在仓库目录中双击 [start.cmd](start.cmd)。需要 Python 3.11+；若本机没有合适的 Python，uv 可按需下载。首次启动可能下载 Python 和锁定的运行依赖，因此需要联网；账号授权和上游调用也需要网络。

脚本会自动定位项目目录，调用已有 CLI，在服务就绪后打开浏览器。**不需要管理员权限，不注册后台服务，也不会自动授权账号。** 使用时保持终端运行，按 `Ctrl+C` 停止；缺少 uv 或启动失败时保留错误窗口。

### 通用方式：命令行启动

在项目目录运行，适用于 Windows、macOS 和具备安全 keyring 的 Linux：

```powershell
uv run --locked --no-dev --system-certs chatpass web
```

不必每次先执行 `uv sync`。uv 会按锁文件准备运行环境，不安装测试、浏览器测试等开发依赖；`--system-certs` 使用系统信任链，**不会关闭 TLS 校验**。需要系统安全凭据存储：Windows 使用 Credential Manager；无头 Linux 若没有可用的 Secret Service/KWallet 将拒绝保存凭据。

需要换端口或自行打开管理入口时：

```powershell
.\start.cmd --port 19841 --no-browser
```

等价命令为 `uv run --locked --no-dev --system-certs chatpass web --port 19841 --no-browser`。启动脚本只转发 `web` 的参数；`--data-dir` 等全局参数、其他子命令或开发操作直接使用 CLI。**标准入口仍然是 `chatpass web`，双击脚本只是便利封装。**

### 启动后做什么

管理台可以在尚未创建密钥、尚未授权账号时启动：

1. 在「网关密钥」中创建密钥，复制到自己的客户端；显示内容不会保存在浏览器本地存储，离开密钥页面或点击清除即隐藏。
2. 在「账号授权」中点击登录，由你在官方页面确认；管理台只显示进度和结果，不显示 OAuth 凭据。
3. 在「模型目录」中主动查询实际可用模型，在「客户端接入」中查看 Responses 接入信息。
4. 概览显示授权/密钥状态、当前进程请求计数与运行时间；**不是官方剩余额度**，重启后计数重置。

管理入口是终端输出的带片段链接，**单次使用、五分钟内有效**；不要分享或截图该链接。浏览器先清除链接中的令牌，再向同源接口交换独立管理会话；管理 Cookie 为 HttpOnly，推理密钥不能获得管理权限。管理会话八小时过期或重启即失效；需要重新启动 `web` 获取新的入口。`--no-browser` 只输出入口，不自动打开浏览器。

`web` 与 `serve` 默认使用同一端口，**二选一运行**，不要同时启动。它们使用同一加密状态目录，已有 CLI 授权和密钥可直接继续使用。本地管理接口仍不支持远程浏览器或公网暴露。安全与验证范围见 [Web 管理台说明](docs/web-console.md)。

## 纯命令行启动

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/) 和受支持的系统安全凭据存储。Windows 使用 Credential Manager；无头 Linux 若没有可用的 Secret Service/KWallet 将拒绝保存凭据。

在项目目录运行：

```powershell
uv sync --locked --no-dev --system-certs
uv run --locked --no-dev --system-certs chatpass key init
uv run --locked --no-dev --system-certs chatpass login
uv run --locked --no-dev --system-certs chatpass status
uv run --locked --no-dev --system-certs chatpass serve
```

1. `key init` 只显示一次网关密钥。复制到自己的客户端，不提交到 Issue、截图或版本库。
2. `login` 打开浏览器，由你确认官方授权；180 秒内完成。可用 `login --no-browser` 显示授权链接自行打开。
3. `status` 只输出非敏感状态。
4. `serve` 默认监听 `127.0.0.1:19840`。终端需保持运行。

更换访问密钥：`uv run chatpass key rotate`。旧密钥立即失效，所有客户端须更新；这不会轮换官方账号凭据。

可指定非敏感存储位置和端口：

```powershell
uv run chatpass --data-dir D:\ChatPassData serve --port 19841
```

所有命令须使用相同数据目录。默认 Windows 目录为 `%LOCALAPPDATA%\ChatPass`。不要手动删除系统安全存储主密钥；丢失后已有加密状态无法解密。

## 客户端填写什么

```text
API 地址：http://127.0.0.1:19840/v1
API Key：key init / key rotate 显示的 cp_ 开头密钥
Model：GET /v1/models 返回的 data[].id
接口：Responses API（不是 Chat Completions）
```

此密钥**不是 OpenAI 官方 API Key**，也不是 ChatGPT OAuth token；必须通过正在运行的 ChatPass 使用。

先查询实际模型，再配置客户端。不要照搬某个模型名或把订阅凭据粘贴到客户端。具体接入条件和测试清单见 [客户端说明](docs/clients.md)。

| 客户端 | 真实接入状态 |
| --- | --- |
| Hermes Agent | 未实测；需确认其版本可使用原生流式 Responses |
| OpenClaw | 未实测；需确认 provider 的 API 类型和完整历史行为 |
| DeepSeek Harness | 未实测；需确认 Responses 路由和请求字段 |

## 当前协议限制

本版遵循已核实的官方 HTTP 预览协议，而不是宣称完整 OpenAI API 兼容：

- 仅支持流式请求，缺省设置 `stream:true`；`stream:false` 明确报错。
- 固定发送 `store:false`，没有服务器会话保存。
- 多轮对话须带完整 `input` 历史；`previous_response_id` 不受支持。
- 保留函数/自定义工具调用及工具结果；不支持托管工具或 Chat Completions 转换。
- `system` 历史项不受支持；采用上游支持的 `instructions`/`developer` 用法。
- `temperature`、`max_output_tokens` 等预览不支持字段返回明确的 400，不静默丢弃。
- 上游资格/额度/授权错误仍会导致调用失败；订阅不等于无限或通用 API 套餐。

依据和许可边界见 [协议说明](docs/protocol-notes.md)。

## 故障恢复与安全边界

- 无可用系统 keyring：先修复系统安全存储，**不会回退到明文文件**。
- 授权超时/取消：重新运行 `login`，不要公开回调 URL 或 token。
- 刷新请求网络结果不确定或进程在请求中退出：为避免重复使用轮换令牌，停止调用并重新 `login`。
- 已收到新令牌但临时无法验证签名：加密保留待验证状态，下次调用仅重新验证，不重发旧刷新令牌。
- 上游 401/403/429：检查授权、账号资格及额度；不自动切到付费 API，也不自动重放可能计费的推理。
- 本版只接受回环监听；**不要用反向代理、隧道或端口转发公开它**。尚无公网 TLS、配额隔离或多用户加固；本地管理会话不能替代这些能力。
- 同一系统用户下的恶意软件不在凭据隔离边界内；网关密钥仍应按密码保管。

## 开发与测试

```powershell
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests
uv build
```

若本机代理证书导致安装 TLS 验证失败，可配置正确的系统证书，或在明确了解本机信任链后使用 uv 的 `--system-certs` 选项；不要关闭证书校验。

设计与执行记录：[设计](docs/design.md)、[实施计划](docs/implementation-plan.md)、[进度](docs/work-status.md)。

## 许可与免责声明

本项目与 OpenAI 无隶属关系，亦非其官方产品。使用须遵守适用服务条款及依赖许可证。

**项目许可证尚未选择。** 公开仓库不自动授予开源许可。官方 DevKit 的自定义非商业许可证不适合直接当作宽松开源依赖；本实现没有复制其代码。协议互操作也不等于获得商用或远程托管授权。
