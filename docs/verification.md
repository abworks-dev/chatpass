# 验证范围与证据

**验证日期：2026-10-13。** Windows 本地网关与 Web 管理台；范围依据为 [设计](design.md)、[初始实施计划](implementation-plan.md) 与 [管理台计划](web-console-plan.md)。本文件区分代码行为证据和真实服务验收，不把模拟结果写成账号或客户端成功。

## 历史验证结果（Web 管理台初始实现）

- Windows / Python 3.11.9，最终全量普通测试：**258 passed, 2 skipped**，17.67 秒；跳过项是显式可选的原生 keyring 与真实浏览器测试。
- 原生 Windows keyring 测试单独显式执行：**1 passed**，0.40 秒；仅使用随机隔离测试服务。
- 真实 headless Microsoft Edge 的持久回归测试：**1 passed**，3.51 秒；另有隔离浏览器冒烟 **27 项检查通过**，生成 1440×1050 桌面和 390 像素宽手机截图。上游、授权结果及 keyring 均为测试替身，没有真实账号或订阅调用。
- Ruff：**All checks passed**；JavaScript `node --check` 成功；锁文件检查成功，46 个锁定包。
- wheel 与 sdist 重新构建成功；最终 wheel 在独立非编辑虚拟环境重新安装，导入版本 `0.1.0`，CLI 总帮助、`web` 和 `serve` 帮助均返回成功。
- 两个分发包的五项静态资源逐字节与源码匹配，Tabler CSS SHA-256 与第三方声明一致；完整 MIT 文本存在。非编辑安装后的控制台和静态 HTTP 路由成功，不读取系统凭据、不发送上游请求。
- 分发包未包含运行状态、凭据文件、开发虚拟环境或 Python 缓存；没有发布 PyPI 包、创建发布标签或自动执行真实登录。
- 两个新增只读独立审查范围无剩余具体 Critical/Important 问题。后端审查独立执行 **221 项测试**及四种 worker/client 生命周期探针；前端审查独立验证轮询截止、显式重启、规范错误恢复与许可一致性。结论是咨询性代码审查，不是生产发布或真实服务验收。

## 本次兼容层提交前验证

- 按用户指定范围，从 Git 暂存区导出独立快照；包含兼容层、流检测诊断、文档及精确的接入文案/浏览器断言，不含已有 OAuth、凭据与管理台重设计改动。
- 独立快照完整离线回归：**489 passed, 3 skipped**，27.04 秒；Ruff `All checks passed`，暂存差异 whitespace 检查通过。跳过原生 keyring、浏览器、分发包 opt-in 测试；浏览器另外执行。
- 独立快照 headless Edge 回归：**1 passed**，4.91 秒；使用隔离模拟凭据/上游，不发起真实授权或推理。
- 先前经用户授权的有限真实诊断中，`gpt-6.1-sol` 基础流式、带工具字段请求及函数调用/结果回传两轮均达到 `response.completed`。用户随后确认 DSH 新会话的问候和原问题成功；旧会话历史/状态的具体故障原因仍未确认。
- 已知未修复缺口：真实工具完成事件包含调用，但最终快照 `output` 为空；当前非流式聚合器不重建这些输出。离线回归通过不证明非流式/Chat 已完整兼容真实上游，详情见 [客户端边界](clients.md) 与 [工作记录](work-status.md)。
- 临时诊断程序不进入提交；没有将凭据或真实响应正文写入验证记录。

## 协议适配层跟进验证（历史）

- 当前源码完整普通回归：**462 passed, 3 skipped**，24.12 秒；Ruff `All checks passed`，`git diff --check` 成功。跳过原生 keyring、浏览器和分发包 opt-in 测试；浏览器另行验证如下。
- 隔离 headless Edge 回归：**1 passed**，4.85 秒。新增断言验证接入说明展示 Chat、忽略字段响应头及严格模式；包含已有管理会话、密钥与响应式布局流程。未使用真实凭据或上游。
- 独立只读审查复现并促成两项修复：多消息/内容项流式文本顺序与终态不一致时不得成功；SDK 可空缺省字段不得误拒绝。修复后独立 adapter/bridge 定向复查 **152 passed**，未发现这两处剩余 Important 问题。
- 覆盖 Responses 与 Chat 四种请求模式、默认兼容/严格拒绝、CLI 配置传递、函数工具轮/参数增量/使用量/JSON Schema、截断/失败/超时/取消清理。原生 Responses 保留 opaque reasoning；Chat 未支持的输出明确失败。
- 初次沙箱回归的跨进程 Queue 命名管道被 Windows 文件策略阻挡；宽权限重新执行后全量通过，未调整生产逻辑或把测试跳过伪装成通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check --no-cache src tests
$env:CHATPASS_BROWSER_TEST = "1"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_web_browser.py
Remove-Item Env:CHATPASS_BROWSER_TEST
```

本次没有重新发布、实测官方推理或修改真实客户端配置。忽略调参字段并不实现其含义，尤其不保证输出 token 上限；真实客户端仍按 [客户端验收](clients.md) 验证。

## 可以复现的自动验证

在项目根目录执行：

```powershell
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests
uv lock --check
uv build
```

普通测试使用临时状态、测试 keyring 和模拟官方 HTTP 响应。本地 HTTP 冒烟及可选浏览器测试启动临时回环 uvicorn 监听，使用真实 HTTP 客户端或 Edge，但官方上游仍为 MockTransport；不执行真实账号授权或消耗订阅额度。

真实浏览器回归是显式可选项，使用已安装的 Edge，或开发者事先安装的 Playwright Chromium：

```powershell
$env:CHATPASS_BROWSER_TEST='1'
uv run pytest tests/test_web_browser.py -q
Remove-Item Env:CHATPASS_BROWSER_TEST
```

它验证桌面/手机布局、fragment 清除、管理 Cookie、无浏览器秘密存储、显式密钥轮换、恶意模型名文本渲染、待验证凭据恢复、210 秒轮询截止与主动刷新重启、过期会话锁屏，以及所有浏览器资源均为同源。

原生系统存储测试是显式可选项：

```powershell
$env:CHATPASS_NATIVE_KEYRING_TEST='1'
uv run pytest tests/test_native_storage.py -q
Remove-Item Env:CHATPASS_NATIVE_KEYRING_TEST
```

该测试只使用随机临时目录对应的新系统 keyring 服务，生成测试主密钥，最后删除这一个测试服务。它不读取或删除现有 ChatPass、Codex 或其他客户端的账号凭据。普通测试默认跳过此项。已在本机 `keyring.backends.Windows` 验证加密读写和密钥轮换；不是 macOS/Linux 的实测证据。

## 证据映射

| 范围 | 证据 | 证明的行为 |
| --- | --- | --- |
| 配置 | [配置测试](../tests/test_config.py) | 回环默认值、参数限值、公网监听拒绝 |
| 状态与密钥 | [存储测试](../tests/test_storage.py) | AES-GCM 密文、错误主密钥/损坏状态拒绝、事务回滚、密钥轮换、无明文降级 |
| 真实 Windows keyring | [原生存储测试](../tests/test_native_storage.py) | 显式可选、独立临时服务的读写和清理 |
| 官方授权协议处理 | [OAuth 测试](../tests/test_oauth.py) | PKCE、动态注册、回调参数、签名/issuer/audience/nonce/subject/scope、受限发现地址；HTTP 与 JWT 为测试数据 |
| 回调监听 | [登录测试](../tests/test_login.py) | 真实回环请求、Host/path/state、单次回调、空闲与慢速请求的绝对截止时间 |
| 凭据生命周期 | [凭据测试](../tests/test_credentials.py) | 单次刷新、轮换检查点、验证失败保留待验证替代令牌、不重放不确定刷新 |
| 跨进程 | [并发测试](../tests/test_multiprocess.py) | spawn 进程并发更新不丢失、刷新仅发送一次、仅一次 key init 成功且成功密钥保持有效；上游/主密钥仍是测试替身 |
| 网关 | [网关测试](../tests/test_gateway.py) | 先鉴权再读正文/凭据、请求与并发限制、禁止字段、模型目录转换、工具历史与 SSE、错误净化、取消/断连资源释放 |
| CLI | [CLI 测试](../tests/test_cli.py) | 不泄漏凭据、显式密钥操作、初始化竞争、模块导入无副作用、网关真实所有者接线 |
| 本地 HTTP | [HTTP 冒烟](../tests/test_http_smoke.py) | 真正监听器上的 health/401/models/SSE 与立即轮换；官方上游为模拟响应 |
| 管理接口 | [管理台测试](../tests/test_console.py) | 单次入口、过期会话、重复参数、Host/Origin/CSRF、正文限额、显式操作、错误净化、生命周期与并发容量 |
| 真实浏览器 | [浏览器测试](../tests/test_web_browser.py) | 实际 Edge 桌面/手机 DOM 与网络；模拟上游和隔离存储，不是账号验证 |
| 分发安装 | wheel/sdist 构建与独立干净虚拟环境安装 | 非编辑安装后 CLI 及静态路由可用，资源/许可逐字节一致，无状态与凭据文件；不是 PyPI 已发布证据 |

## 独立审查与针对性修复

两名只读审查者分别检查授权/状态与网关/协议，结论仅为咨询性，不代替测试或真实服务验收。每项修复先用针对性失败用例复现：

- CLI 启动调用网关时所有者参数顺序错误：纠正编排层接线，不添加兼容分支。
- 数字指数溢出转为非有限浮点数：由同一个 JSON 解析所有者拒绝，客户端请求返回净化的 400，异常上游目录/流不被当成成功。
- 鉴权线程在请求超时/取消后仍运行：提交前限制工作数，容量跟随实际验证任务完成释放，避免无界线程工作队列。
- 慢速回调头绕过空闲超时：回调所有者施加连接处理及登录绝对截止时间，不以重复收到字节延长等待。
- `key init` 在锁外检查再写入：初始化检查与哈希写入归并至单一存储事务；CLI 不再承担状态判定。显式 `key rotate` 保留原语义。
- SSE 合法混合换行与末尾 CR：由单一帧所有者识别逻辑换行，末尾 CR 等待下一字节或真实 EOF，避免提前发出完成帧而丢失后续 LF。遍历合法空行字节组合及每个分片位置验证字节透明；单个 CRLF 不会被误当作空行，缺少完成帧仍报错。

上述修复均为 `Decision: code-change`：仅改文档/配置不能修复复现行为。所有者边界保持不变，不新增付费/明文/凭据导入降级。没有旧实现、迁移或遗留适配器需要删除。

### Web 管理台后续审查

- 管理 GET 与写操作都执行实际会话截止校验；入口在有界正文读取后重新检查截止并原子消耗，拒绝重复 Host/Origin/Cookie/JSON 字段与不可编码 Unicode。
- 管理存储/登录/模型查询，以及公开接口凭据/鉴权任务，在 HTTP 超时或取消后继续持有其有界容量；关闭时先阻止新任务并等待真实 worker 完成，再关闭自有 HTTP 客户端。外部注入的客户端仍由调用者负责。
- 前端错误映射改为后端实际代码，临时签名验证不可用时明确提示等待验证、不要重复授权。轮询只有一个调度所有者，210 秒预算结束后不再复活定时器；主动刷新可以显式重新开始。
- 原有 30 毫秒 SSE 测试在负载下可能在响应头前合法超时，不能稳定证明“正在流式传输时截断”。仅将该测试预算设为 0.5 秒、外层等待设为 2 秒；未释放的流 gate 仍保证发生超时，并保留 200、净化错误、无伪完成和关闭流的断言。生产超时语义未改；流截止从鉴权完成后开始，鉴权另有独立超时。
- 浏览器 fake-clock 回归等待实际刷新响应完成，再推进下一次轮询；过期会话通过重新加载稳定触发 GET 校验，避免测试点击已被轮询锁屏隐藏的按钮。这是测试竞态修正，不是界面绕过。

## MIT 许可与打包验证（2026-10-13）

本轮许可改动以 `main` / `0eb8377` 为基线，使用 Git 已提交源文件加本轮许可文件的隔离快照验证；工作区当时已有的管理台前端及浏览器测试修改没有纳入此证据或提交范围。

- 自有代码与文档采用标准 [MIT](../LICENSE)，版权为 `ChatPass contributors`；Tabler 完整原作者声明与样式字节保留，Python 依赖和上游服务权限不重新许可。
- 包配置使用 PEP 639 的 `license = "MIT"`、明确的 `license-files`，补齐作者、关键词、Alpha 分类及仓库/问题/文档地址；不使用已弃用的许可证分类器。
- [打包回归](../tests/test_packaging.py)：显式构建检查 **3 通过**（3.57 秒）；隔离完整回归 **263 通过、3 跳过**（19.38 秒）。三项默认跳过分别为原生 keyring、真实浏览器及构建检查；构建检查已另行显式执行。
- Ruff、46 个包的锁文件检查及 Git 空白检查通过；依赖与锁定版本没有改变。
- wheel 的 `METADATA` 与源码包的 `PKG-INFO` 均声明 `License-Expression: MIT` 及三个 `License-File`。项目 MIT、第三方声明、Tabler 完整许可在两个包内均与源文件字节一致；wheel 的 `.dist-info/licenses/` 及样式旁保留声明。内置静态资源完整且字节一致，包不含运行状态、凭据、缓存或环境目录。
- Python 3.11.9 非 editable 安装验证通过：安装后元数据及三个声明与源文件一致，`chatpass --help`、`web --help`、`serve --help` 可运行。没有读取现有账号凭据、进行官方登录或访问上游服务。

可复现的许可与发布包检查：

```powershell
uv sync --locked --group dev
uv run pytest tests/test_packaging.py -q
$env:CHATPASS_PACKAGE_TEST = "1"
uv run pytest tests/test_packaging.py -q -s
Remove-Item Env:CHATPASS_PACKAGE_TEST
uv build
```

显式构建检查需要 uv 和可信包索引证书；只构建与检查发布包，不启动服务或使用账号。它不替代真实服务及客户端验收。

## 仍未验证与已知边界

- **真实官方登录/推理/刷新、账号资格与额度扣减：未验证。** 测试签名及响应不能证明这些能力。
- **Hermes/OpenClaw/DeepSeek Harness 的真实配置、流式、多轮和工具调用：未验证。** 按 [客户端验收](clients.md) 在具体版本上分别记录。
- **远程/公网部署：不支持。** 本版拒绝非回环绑定，未完成 TLS、管理权限分离、用户配额与公网加固。
- **macOS/Linux 原生 keyring：未实测。** 无可用受支持安全后端时拒绝保存，不降级为明文。
- Python 无法安全终止已运行的同步 keyring/浏览器线程。若后端永久挂起，有界容量将保持占用，关闭等待也可能延长；HTTP 请求仍受超时约束，后续请求可返回 429。修复后端并重启，而不是不断提交替代任务。
- ChatPass 自有代码和文档已采用 [MIT](../LICENSE)，第三方许可保持独立；此许可不授予上游服务资格。尚未发布到 PyPI。

初始代码与本地模拟验证可以完成，真实服务及三客户端验收仍需用户主动登录与授权测试。任何报告都应保留这一分界。
