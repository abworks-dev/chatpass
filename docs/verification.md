# 验证范围与证据

**验证日期：2026-10-13。** 初始 Windows 实现；验收依据为 [设计](design.md) 与 [实施计划](implementation-plan.md)。本文件区分代码行为证据和真实服务验收，不把模拟结果写成账号或客户端成功。

## 最后一次验证结果

- Windows / Python 3.11.9，全量普通测试：**180 passed, 1 skipped**，12.89 秒；跳过项是显式可选的原生 keyring 测试。
- 原生 Windows keyring 测试单独显式执行：**1 passed**，0.36 秒。
- Ruff：**All checks passed**；锁文件检查成功，43 个锁定包。
- wheel 与 sdist 构建成功；最终 wheel 在独立非编辑虚拟环境重新安装，导入版本 `0.1.0`，CLI 总帮助及 serve 帮助均返回成功。
- 检查分发包未包含运行状态、凭据文件、开发虚拟环境或 Python 缓存；没有发布 PyPI 包或自动执行真实登录。
- 两个只读独立审查范围均无剩余具体 Critical/Important 问题；网关原有 Minor 换行问题也已复测关闭。审查者独立范围分别为 42 项授权/存储/CLI 测试与 138 项网关/真实本地 HTTP 测试。结论是咨询性代码审查，不是生产发布或真实服务验收。

## 可以复现的自动验证

在项目根目录执行：

```powershell
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src tests
uv lock --check
uv build
```

普通测试使用临时状态、测试 keyring 和模拟官方 HTTP 响应。唯一真实 HTTP 冒烟测试启动临时回环 uvicorn 监听，使用真实 httpx 客户端访问，但其官方上游仍为 MockTransport；不执行真实账号授权或消耗订阅额度。

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
| 配置 | `tests/test_config.py` | 回环默认值、参数限值、公网监听拒绝 |
| 状态与密钥 | `tests/test_storage.py` | AES-GCM 密文、错误主密钥/损坏状态拒绝、事务回滚、密钥轮换、无明文降级 |
| 真实 Windows keyring | `tests/test_native_storage.py` | 显式可选、独立临时服务的读写和清理 |
| 官方授权协议处理 | `tests/test_oauth.py` | PKCE、动态注册、回调参数、签名/issuer/audience/nonce/subject/scope、受限发现地址；HTTP 与 JWT 为测试数据 |
| 回调监听 | `tests/test_login.py` | 真实回环请求、Host/path/state、单次回调、空闲与慢速请求的绝对截止时间 |
| 凭据生命周期 | `tests/test_credentials.py` | 单次刷新、轮换检查点、验证失败保留待验证替代令牌、不重放不确定刷新 |
| 跨进程 | `tests/test_multiprocess.py` | spawn 进程并发更新不丢失、刷新仅发送一次、仅一次 key init 成功且成功密钥保持有效；上游/主密钥仍是测试替身 |
| 网关 | `tests/test_gateway.py` | 先鉴权再读正文/凭据、请求与并发限制、禁止字段、模型目录转换、工具历史与 SSE、错误净化、取消/断连资源释放 |
| CLI | `tests/test_cli.py` | 不泄漏凭据、显式密钥操作、初始化竞争、模块导入无副作用、网关真实所有者接线 |
| 本地 HTTP | `tests/test_http_smoke.py` | 真正监听器上的 health/401/models/SSE 与立即轮换；官方上游为模拟响应 |
| 分发安装 | wheel/sdist 构建与独立干净虚拟环境安装 | 非编辑安装后可导入 0.1.0 并运行 CLI help；不是 PyPI 已发布证据 |

## 独立审查与针对性修复

两名只读审查者分别检查授权/状态与网关/协议，结论仅为咨询性，不代替测试或真实服务验收。每项修复先用针对性失败用例复现：

- CLI 启动调用网关时所有者参数顺序错误：纠正编排层接线，不添加兼容分支。
- 数字指数溢出转为非有限浮点数：由同一个 JSON 解析所有者拒绝，客户端请求返回净化的 400，异常上游目录/流不被当成成功。
- 鉴权线程在请求超时/取消后仍运行：提交前限制工作数，容量跟随实际验证任务完成释放，避免无界线程工作队列。
- 慢速回调头绕过空闲超时：回调所有者施加连接处理及登录绝对截止时间，不以重复收到字节延长等待。
- `key init` 在锁外检查再写入：初始化检查与哈希写入归并至单一存储事务；CLI 不再承担状态判定。显式 `key rotate` 保留原语义。
- SSE 合法混合换行与末尾 CR：由单一帧所有者识别逻辑换行，末尾 CR 等待下一字节或真实 EOF，避免提前发出完成帧而丢失后续 LF。遍历合法空行字节组合及每个分片位置验证字节透明；单个 CRLF 不会被误当作空行，缺少完成帧仍报错。

上述修复均为 `Decision: code-change`：仅改文档/配置不能修复复现行为。所有者边界保持不变，不新增付费/明文/凭据导入降级。没有旧实现、迁移或遗留适配器需要删除。

## 仍未验证与已知边界

- **真实官方登录/推理/刷新、账号资格与额度扣减：未验证。** 测试签名及响应不能证明这些能力。
- **Hermes/OpenClaw/DeepSeek Harness 的真实配置、流式、多轮和工具调用：未验证。** 按 [客户端验收](clients.md) 在具体版本上分别记录。
- **远程/公网部署：不支持。** 本版拒绝非回环绑定，未完成 TLS、管理权限分离、用户配额与公网加固。
- **macOS/Linux 原生 keyring：未实测。** 无可用受支持安全后端时拒绝保存，不降级为明文。
- Python 无法安全终止已运行的同步 keyring 线程。若后端永久挂起，鉴权容量将保持占用；请求仍受超时约束，后续请求可返回 429。修复后端并重启，而不是不断提交替代任务。
- 项目许可证尚未选定，也未发布到 PyPI；不能把公开仓库称为已获得宽松开源授权。

初始代码与本地模拟验证可以完成，真实服务及三客户端验收仍需用户主动登录与授权测试。任何报告都应保留这一分界。
