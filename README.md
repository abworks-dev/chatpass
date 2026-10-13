<p align="center">
  <img src="src/chatpass/static/brand/chatpass-icon.svg" width="96" height="96" alt="ChatPass 对话票证图标">
</p>

# ChatPass

**简体中文** | [English](README.en.md)

**面向个人 Agent 用户的 ChatGPT 公开授权协议本地网关。**

通过 Sign in with ChatGPT，由你明确授予 ChatGPT 计划使用权限，再将授权范围内的能力接入自己的 Agent 工具。客户端只持有 ChatPass 访问密钥，上游 OAuth 凭据保存在本机网关端；不复制其他工具的订阅凭据，不做账号池或额度转售。

> **状态：初始实现，已进行模拟测试；尚未完成真实账号和客户端实测。** 不承诺所有订阅账号均有资格、无限额度，或 Hermes / OpenClaw / DeepSeek Harness 已可直接接入。服务器部署只是未来边界，本版拒绝非回环监听。

## 定位与同类项目的区别

ChatPass 服务于**一个人在本机连接自己的 Agent 工具**，不是订阅中转站或多用户 API 运营平台。从网络结构看，它仍然是网关/代理；区别不在于“有没有 OAuth”或“是否转发请求”，而在于**授权范围、上游接口和服务对象**。

| 对比维度 | ChatPass 的选择 | 其他订阅代理/中转项目可能采用的方式 |
| --- | --- | --- |
| 授权来源 | 以 ChatPass 的应用名称发起 Sign in with ChatGPT，动态注册客户端，并请求 `chatgpt.tokens.use.direct` 计划使用权限 | 使用 Codex 等工具的 OAuth 登录流程，或导入已有工具凭据；同样使用 OAuth 不代表获得相同权限 |
| 上游接口 | 公共 `https://api.openai.com/v1/models` 与 `/v1/responses` | 部分项目适配 `chatgpt.com/backend-api/codex/*` 等私有后端；客户端侧的 `/v1` 地址本身不能说明上游是哪一种 |
| 服务对象 | 个人、本机、单账号，供自己的客户端接入 | 有的也是个人代理；另一些面向多账号调度、多用户 Key 分发、共享额度或计费运营 |
| 凭据边界 | 本项目自行取得并保护凭据，客户端只持有独立网关密钥 | 各项目实现不同；OAuth、独立 Key 和加密存储并非 ChatPass 独有能力 |
| 明确不做 | 账号池、订阅转售、多厂商聚合、按量付费 API 自动降级及公网服务 | 部分平台以这些能力为主要目标；不能将其功能或部署方式套用到 ChatPass |

例如，[Sub2API](https://github.com/Wei-Shaw/sub2api) 的仓库定位是订阅额度分发与管理平台，包含多账号管理、用户 API Key 分发、计费和调度；**ChatPass 不面向这类运营场景**。[codex-sub-proxy](https://github.com/tech-grandpa/codex-sub-proxy) 则更接近个人订阅代理，但明确使用 Codex 私有后端。以上是不同路线的例子，不代表所有同类项目都使用私有接口，也不是对它们的安全性或合法性作统一判断。

**公开协议不等于官方背书，也不等于通用 API 套餐。** [OpenAI 的公开说明](https://developers.openai.com/siwc/token-sharing-open-source)描述了开源、本地应用请求 ChatGPT 计划使用权限的路径；ChatPass 按该路径独立实现，而非 OpenAI 官方产品或已获批准的合作项目。仅有 OAuth 登录成功还不够，必须实际获授计划使用权限，并满足账号资格、额度及适用条款。付费或远程托管应用须另行核实官方准入要求；MIT 软件许可不授予上游服务使用权。

官方 [Sign in with ChatGPT DevKit](https://github.com/openai/sign-in-with-chatgpt-devkit) 主要帮助开发者将授权与推理嵌入自己的应用；ChatPass 的目标则是为已有工具提供可配置的本地地址和独立访问密钥。这是产品形态的区别，不是宣称授权、加密或流式转发技术首创。协议依据与许可边界见 [协议说明](docs/protocol-notes.md)；真实账号和客户端兼容性仍须验证。

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

**ChatPass 自有代码和文档采用 [MIT 许可证](LICENSE)**，版权声明为 `Copyright (c) 2026 ChatPass contributors`。允许使用、修改、分发、商业使用及闭源再分发；分发副本或实质性部分时须保留版权与许可声明。软件按原样提供，不作担保。

内置 Tabler 样式保留原作者的 MIT 许可及版权声明，详见 [第三方声明](THIRD_PARTY_NOTICES.md)；Python 依赖遵循各自许可证，并非统一改为 MIT。wheel 与源码包均携带项目许可证、第三方声明及 Tabler 完整许可。

**MIT 授予的是 ChatPass 软件的使用权，不是 OpenAI 服务或商标的使用授权。** 账号资格、订阅额度、商用场景及远程服务仍受适用的上游条款和授权约束；当前版本依然仅支持本机回环。官方 DevKit 使用独立的自定义非商业许可证，本实现没有复制或捆绑其代码、文档或资产，也不以 ChatPass 的 MIT 许可重新许可它们。
