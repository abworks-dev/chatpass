<p align="center">
  <img src="src/chatpass/static/brand/chatpass-icon.svg" width="96" height="96" alt="ChatPass 对话票证图标">
</p>

# ChatPass

**简体中文** | [English](README.en.md)

**将自己的 ChatGPT 订阅接入 Agent 工具。**

ChatPass 是个人自用的本地网关，通过 Sign in with ChatGPT 公开授权流程，让自己的 Agent 工具在获授权范围内使用订阅能力。客户端只需配置本地 API 地址和独立网关密钥，无需接触 ChatGPT OAuth 凭据。

> **状态：本地模拟验证已完成；历史有限授权诊断及用户反馈确认 DSH 新会话可用，但新版完整真实客户端验收未完成，Hermes / OpenClaw 未实测。** 账号资格、可用模型与额度以上游实际授权为准，订阅不等于无限或通用 API 套餐。验证范围见 [验证说明](docs/verification.md)。

## 快速开始

### 1. 获取项目并准备环境

从 [GitHub 仓库](https://github.com/abworks-dev/chatpass) 下载 ZIP 并解压，或用 Git 克隆：

```powershell
git clone https://github.com/abworks-dev/chatpass.git
cd chatpass
```

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，并确认终端能运行 `uv --version`。

- 需要 Python 3.11+；未安装合适版本时，uv 可按需下载。
- 需要系统安全凭据存储：Windows Credential Manager、macOS Keychain，或 Linux Secret Service/KWallet。无可用存储时拒绝保存凭据，不回退到明文文件。
- 首次启动可能下载 Python 和运行依赖；授权及模型调用也需要联网。

### 2. 启动本地管理台

**Windows 推荐：** 在项目目录双击 [start.cmd](start.cmd)。不需要管理员权限，不注册后台服务，也不会自动授权账号。

其他平台或偏好终端的用户，在项目目录运行：

```powershell
uv run --locked --no-dev --system-certs chatpass web
```

服务就绪后自动打开浏览器。保持终端运行，按 `Ctrl+C` 停止。uv 按锁文件准备运行环境，不必先执行 `uv sync`；`--system-certs` 使用系统信任链，不关闭 TLS 校验。

更换端口或手动打开终端输出的管理链接：

```powershell
uv run --locked --no-dev --system-certs chatpass web --port 19841 --no-browser
```

Windows 启动脚本也可传入 `--port 19841 --no-browser`，但只转发 `web` 参数；其他子命令和全局参数请直接使用 CLI。

遇到 Windows Proactor 连接关闭时的 `WinError 10054`，可选择 `start.cmd --event-loop selector` 排查（默认仍为 `auto`）；这不修复上游明确返回的错误。详见[流失败与连接关闭排错](docs/stream-failure-troubleshooting.md)。

### 3. 授权并获取接入信息

1. 在「网关密钥」中创建密钥，复制后妥善保管。
2. 在「账号授权」中点击登录，由你在官方页面确认授权。
3. 在「模型目录」中查询实际可用模型，再配置自己的 Agent 工具。

管理台可在尚未授权或创建密钥时启动。概览中的请求计数重启后归零，**不是官方剩余额度**。

<details>
<summary>可选：不使用管理台，只运行命令行</summary>

在项目目录依次运行：

```powershell
uv run --locked --no-dev --system-certs chatpass key init
uv run --locked --no-dev --system-certs chatpass login
uv run --locked --no-dev --system-certs chatpass status
uv run --locked --no-dev --system-certs chatpass serve
```

`key init` 生成密钥并在本机加密保存；管理台可随时查看或复制，默认隐藏。升级前仅保存哈希的旧密钥仍有效，但无法取回明文，遗失时需手动轮换；`login` 打开官方授权页面，180 秒内完成，可用 `login --no-browser` 自行打开授权链接；`status` 只显示非敏感状态。

`serve` 只运行 API，`web` 同时运行管理台和 API。两者默认使用同一端口，**二选一运行**；已有 CLI 授权和密钥可继续用于管理台。

</details>

## 客户端配置

在支持自定义 API 地址的 Agent 工具中填写：

```text
API 地址：http://127.0.0.1:19840/v1
API Key：管理台或 key init 创建的 cp_ 开头密钥
Model：模型目录或 GET /v1/models 返回的 data[].id
接口：原生 Responses API（流式）
```

若启动时改了端口，API 地址也要相应修改。网关密钥**不是 OpenAI 官方 API Key 或 OAuth token**，必须通过正在运行的 ChatPass 使用；不要把订阅凭据粘贴到客户端，也不要照搬未经查询的模型名。

当前源码支持原生 Responses 和 Chat Completions 的流式／非流式接口，优先使用原生流式 Responses。有限授权诊断及用户反馈已确认 DSH 新会话可用，但不是所有客户端或所有协议模式的验收；已加入空终帧输出的完成项重建及模拟回归，真实新版验收仍待进行。Hermes、OpenClaw 与 DSH 的实际版本、历史和工具轮须逐一验证，见 [客户端说明](docs/clients.md)。

## 与订阅中转平台的区别

**个人、本机、单账号自用；不做账号池、额度转售、多用户中转或多厂商聚合。** ChatPass 仍然是网关/代理，区别不在于是否使用 OAuth，而在于授权路径、上游接口与服务对象。

| 维度 | ChatPass | 其他订阅代理可能采用的方式 |
| --- | --- | --- |
| 授权路径 | 以自身应用名称发起 Sign in with ChatGPT，请求 `chatgpt.tokens.use.direct` 计划使用权限，不导入其他工具凭据 | 使用 Codex 等工具的 OAuth 流程，或导入已有工具凭据 |
| 上游接口 | 公共 `https://api.openai.com/v1/models` 与 `/v1/responses` | 部分项目适配 Codex 私有后端；客户端侧的 `/v1` 地址不能说明上游是哪一种 |
| 服务对象 | 一个人连接自己的本机工具 | 有的也是个人代理，有的面向多账号调度、用户 Key 分发和计费运营 |

例如，[Sub2API](https://github.com/Wei-Shaw/sub2api) 面向订阅额度分发与管理，包含多账号、用户 Key、计费和调度；ChatPass 不面向这类运营场景。不能据此认定所有订阅代理都走私有接口，或对它们的安全性、合法性作统一判断。公共 SIWC 协议不是独占差异；本项目的取舍是个人本地、单账号与保守的凭据生命周期。授权依据与技术细节见 [协议说明](docs/protocol-notes.md)。

## 限制与安全

### 接口限制

当前源码提供 `GET /v1/models`、`POST /v1/responses`、`POST /v1/chat/completions`，不是完整 OpenAI API 兼容层，也不代表已经发布或全部真实客户端验收。

- 两种推理接口可流式或非流式；上游始终 `stream:true`、`store:false`，只有 `response.completed` 算成功。
- 多轮须携带完整历史；不支持 `previous_response_id`、后台任务、服务器状态引用及文件搜索、Code Interpreter、托管 MCP 等工具。网页搜索仅原生 Responses 可请求，实际资格由模型及账号／工作区策略决定。
- 原生 Responses 保留函数／自定义工具、命名空间、`additional_tools` 和 opaque reasoning 历史；Chat 支持普通函数工具的较窄子集，命名空间／自定义工具明确拒绝，不冒充无损兼容。`system` 转为 `developer`。
- 默认兼容模式忽略列明的不支持调参字段，响应头 `X-ChatPass-Ignored-Fields` 标出名称；忽略输出上限不等于实现其约束。使用 `--compatibility-mode strict` 可明确拒绝。

当最终快照 `output` 为空时，聚合器使用连续索引的 `response.output_item.done` 完成项重建输出；仍须收到成功 `response.completed`，冲突、重复身份、缺项或截断不能算成功。此修复已有本地模拟回归，尚未重验真实上游／客户端，不承诺非流式／Chat 完整兼容。`reasoning`、`text`、`include` 等原生字段保留；未有 SIWC 支持证据的调参不会因通用 API 文档而放开。

### 凭据与访问安全

- OAuth 凭据在本机加密保存，主密钥由系统安全凭据存储保护。不要手动删除主密钥，否则已有加密状态无法解密。
- **只支持本机回环访问，不要通过反向代理、隧道或端口转发公开服务。** 本版没有公网部署所需的 TLS、多用户隔离和配额管理。
- 管理入口链接单次使用、五分钟内有效；不要分享或截图。管理会话八小时过期或重启即失效，重新启动 `web` 获取新入口。网关密钥不能登录管理台。详见 [管理台说明](docs/web-console.md)。
- 轮换密钥：`uv run chatpass key rotate`。旧密钥立即失效，客户端须更新；这不会轮换官方账号凭据。
- 所有命令使用同一数据目录，Windows 默认 `%LOCALAPPDATA%\ChatPass`。指定目录示例：`uv run chatpass --data-dir D:\ChatPassData serve --port 19841`。
- 本项目不隔离同一系统用户下的恶意软件。不要公开密钥、token、回调链接或完整对话。

### 登录、退出与恢复

请求消耗与你的 ChatGPT 共享的计划预算；订阅不是无限 API 额度。管理台首次授权先提示确认，实际权限仍由官方页面授予；[官方用量](https://chatgpt.com/settings/usage) 可能反映计划或应用限制，本地请求计数不是令牌／账单额度。

- 浏览器未打开：授权进行中可显式获取短期继续链接；仅管理会话可见，结束／过期即清除。链接不含身份令牌，但仍不可分享。
- 已登录但计划未授权：仅加密保留已验证身份，不允许推理或刷新；由你决定是否重新请求权限。失败的新候选不替换旧有效授权。
- 退出：管理台确认「退出账号」或 `uv run chatpass logout`。尝试官方撤销后清除本地凭据，保留网关密钥、注册与账号绑定。远端撤销未确认时到 ChatGPT 设置手动断开；已开始请求无法撤回。
- 换账号：显式 `uv run chatpass logout --new-account`，或在退出确认中勾选下次使用不同账号；重置客户端／账号绑定，保留本机标识与网关密钥，再手动登录。
- `refresh_not_yet_allowed`：等官方刷新窗口，刷新状态更新等待时间；待验证候选的验证不受旧令牌刷新窗口阻挡。


- 授权超时或取消：重新执行 `login`。
- 刷新结果不确定或刷新途中进程退出：停止调用并重新登录，避免重复使用轮换令牌。已保存但待验证的令牌仅重新验证，不重发旧刷新请求。
- 上游 401/403/429：检查授权、账号资格和额度；不会自动切换到按量计费 API，也不会自动重放推理请求。
- 安装遇到 TLS 错误：配置正确的系统证书，必要时使用 `--system-certs`，不要关闭校验。

## 开发与许可

### 开发

```powershell
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests
uv build
```

进一步阅读：[设计](docs/design.md) · [实施计划](docs/implementation-plan.md) · [进度](docs/work-status.md) · [协议说明](docs/protocol-notes.md) · [验证说明](docs/verification.md)。

### 许可与免责声明

ChatPass 与 OpenAI 无隶属关系，亦非其官方产品或已获批准的合作项目。**公开协议不等于官方背书，OAuth 登录成功也不等于获授计划使用权限。** 使用须满足实际上游授权与适用条款；付费或远程托管应用须另行核实官方准入要求。

ChatPass 自有代码和文档采用 [MIT 许可证](LICENSE)，允许使用、修改、分发、商业使用及闭源再分发；分发时须保留版权与许可声明，软件按原样提供，不作担保。**MIT 授予软件使用权，不授予 OpenAI 服务或商标使用权。**

管理台使用 Tabler 1.6.1（MIT），资源随包提供，不使用 CDN、远程字体或遥测；第三方许可与版权声明见 [第三方声明](THIRD_PARTY_NOTICES.md)。Python 依赖保留各自许可证。官方 DevKit 另有自定义非商业许可证，本实现没有复制或捆绑其代码、文档或资产。
