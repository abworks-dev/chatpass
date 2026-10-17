# dsh-chatpass — isolated implementation evidence

## Scope / decision

阶段 0/1 连接已有核心的 alpha 实现完成至隔离验证停止点；真实宿主和上游兼容性为 needs-verification，不作发布/稳定性保证。依据 `docs/dsh-chatpass-design.md` 与 `docs/dsh-chatpass-implementation-plan.md`；核心、凭据、Codex provider、默认模型、当前 DSH profile 未改。没有执行提交、安装或发布。

## Fresh checks

2026-10-14，插件目录，bundled Node + pnpm 11.7.0：

- `pnpm run check`，最终 job `pwsh-260`（仅去除两处尾随空白后重验），exit 0：严格 source tsc 与 `tsc -p tsconfig.tests.json`；Vitest 6 文件 **49/49**；esbuild host/client 及声明构建。
- 分布：connection 9、transport 8、provider 12、client-store 6、client 13、host 1。所有密钥为 fake；只访问临时 loopback fixture，不访问真实 19840 网关或 OpenAI。
- `pnpm pack` + `tar -tf`，同 job，exit 0：17 个文件，host/client JS 与 sourcemaps、9 个声明文件、Cordis patch、package metadata、README、MIT LICENSE。无 node_modules、秘密、依赖缓存、测试代码或遗留 CSS sidecar。
- `pwsh-255` 与最终 `pwsh-260`，均 exit 0：实际构建产物 host ESM import/export、client ESM import/export、jsdom 内联 style 注入成功。此测试没有应用插件、连接宿主或验证真实浏览器 CSP。
- archive `plugins/dsh-chatpass/dsh-chatpass-0.1.0-alpha.1.tgz` SHA256 `0B880B95C11BF654EDB4501A960E734FB6243B5774CC1BC9F816F449B63395F4`。
- `git diff --check` exit 0；任务新增文件另外检查尾随空白与冲突标记。原有用户 dirty 文档和 Python 核心不是本任务结果，不将其测试或差异归于插件。

## Covered seams / repairs

- Connection owner：验证与修订号跨 await 原子提交；失败 candidate 不替换原配置；remove/save 后旧请求绑定 immutable snapshot；更改地址不得暗用旧 key；公共状态无 key。
- Native HTTP：原始拼写严格 `http://127.0.0.1[:port]/v1`，DNS/IPv6/代理环境/redirect 拒绝；仅 models/Responses；请求 auth 不可覆盖；目录体积/数量、取消、10 秒目录总时限、30 秒 Responses headers 等待、16 MiB 请求上限；无自动重试。没有全面长时 SSE slow-trickle 压测。
- Provider：真实安装的 pi-ai 0.87.1 Responses converter / SDK stream；user/system/assistant/tool 历史、opaque reasoning 与 call IDs；delta、cancel、failed/truncated/max-token 终态；安全错误重映射。`auth.resolve` 必须接受 SDK 将 options.apiKey 转成的 transient `credential.key`，空上下文仍不取 ambient credentials。`reasoning:false` 不会自动发送 encrypted include，因此 onPayload 仅追加 `reasoning.encrypted_content`，不伪造 effort capability；有 outgoing request body 回归。
- Host：注册 `chatpass` 与独立 `/chatpass` RPC handler，释放与 revision 变更触发目录更新。host test mock Connection，只证明 wiring。SDK 源码已确认独立 handle 经相同 admission；没有把 direct executeCommand 误称为认证负向测试。
- UI：双视图共享 store；密码成功/失败/reset 清除；显式冲突 reload；modal 关闭确认；失败 refresh 发送 safe state 并经客户端 runtime guard；5 分钟定时仅更新 stale 显示，不自动 discovery；内嵌页跨卸载只留显式投影的非秘密草稿，密码不入 store，generation/revision 冲突保护，成功 save/reload 清草稿。

## Review / remaining risk

独立只读审阅指出并修复独占 /api 冲突、原始 SDK 错误日志、encrypted reasoning request include、新鲜度和内嵌草稿问题；不将代理意见替代父协调器 fresh 测试。没有新增协议解析、OAuth、账号存储或退休核心路径。薄适配器继续复用 SDK 作为协议单一 owner；额外 guard 分别限定连接、命令、错误/UI 状态边界，无猜测上游能力与静默 fallback。

- 实际 DSH/profile 版本兼容、host loader、浏览器 CSP、light/dark/窄屏/原生 dialog/键盘、host reload 未验证。
- Host/Origin/cookie 真实拒绝与 authenticated positive RPC 没有端到端测试。SDK 默认 RPC admission buffer 为 314572800 bytes；插件字段限制发生于 envelope 解析后，不能声称已做到小型入口内存上限。未来应通过宿主公开 API 限制，不绕过认证新建裸路由。
- 内嵌导航退出没有已核验的宿主 guard；已通过非秘密 draft retention 降低丢失风险，密码故意丢弃；真实导航的“放弃/继续编辑”完整设计验收仍需实机核验。
- 全部配置/密钥 memory-only；重启需配置。未验证安全持久化接口，禁止添加明文 config/localStorage fallback。
- 没有真实账号、计费、配额、图像或上游 inference 证据；目录模型存在不等于可用。用户确认容量、text-only、未知价格/能力不伪装成服务保证。

结构性治理检查另行执行：`aegis-workspace.py bundle` exit 2（缺 task-intent-draft.json），`check` exit 1（本仓库未配置 docs/aegis/adr、baseline、specs、README.md、INDEX.md、BASELINE-GOVERNANCE.md）。没有为追求绿色检查而初始化完整治理体系或修改用户既有记录；本工作记录是简化文本，不声称结构校验通过。这不改变插件测试结果。

置信等级：隔离实现与回归 B；真实宿主/上游 C。下一项最高价值验证：经用户授权，在隔离 profile 进行 admission + 双入口真实浏览器 smoke。

## Current desktop compatibility repair — alpha.2

用户随后授权当前 desktop 安装；其官方插件管理器拒绝 alpha.1，因为宿主是 `0.2.0-rc.2`，而包的 DSH peers 全部精确要求 `0.2.1-alpha.1`。此前隔离测试只覆盖较新 SDK，不能证明当前桌面可装；此前直接推荐 alpha.1 安装路径缺少这一必要核验。此处更正，不把旧测试重新解释为当前宿主证据。

- Change Necessity：config-only；canonical owner 是插件 package manifest / lockfile，不是宿主兼容策略。保持 provider、RPC、界面与核心职责不变，没有扩展 fallback 或强行豁免。SDK/dev/peer 同步绑定当前宿主 DSH `0.2.0-rc.2`、Cordis `4.0.4`；pi-ai 保持实际宿主相同的 `0.87.1`；版本递增 `0.1.0-alpha.2`。
- `pwsh-389` 安装真实目标 SDK exit 0；`pwsh-390` exit 0：source/test 严格类型检查、6 文件 **49/49** 回归、host/client 构建。无需修改运行时代码，证明当前公开 SDK 接口足够支持已有实现。
- 新增只读 `scripts/check-installed-host.mjs`：直接调用当前 Electron archive 内官方 `evaluatePluginCompatibility`，不提供版本豁免，并核对 build SDK 与实际宿主 peers。负向控制将 dsh-llm peer 改回 `0.2.1-alpha.1`，确认仍被拒绝。`pwsh-394` 对 **新 tarball 解包的 manifest** 检查 exit 0。没有修改当前 profile、关闭检查或绕过插件管理器。
- `pnpm pack` / `tar -tf` exit 0，17 文件，alpha.2 包 SHA256 `9DF2215E73655E8B08AEF4D14E66E2A67600824E038FD76068A0EA5340B74653`。旧 alpha.1 不再作为当前宿主安装候选；保留本地旧 archive 仅作历史，未发布。
- 已证明当前宿主的安装兼容检查接受新包；**尚未证明安装成功、实际 loader 挂载、真实浏览器交互或账号推理成功**。这些边界与上述未覆盖项仍有效。当前 GUI 内的实际安装仍需官方插件管理器提交。

## Installed alpha.2 activation failure — diagnosis remains open

用户已实际安装 alpha.2，但 GUI 立即启用提示 `dsh: warning: 1 entry did not activate llm-chatpass (dsh-chatpass): failed to import`。不能把前项兼容检查成功称为 loader/activation 成功。

- 已读取当前 profile 实际安装的 alpha.2 manifest、host entry 和 patch；与期望产物一致。直接以无 DSH resolver 的 Electron import 报 `ERR_MODULE_NOT_FOUND` / `Cannot find package '@deepseek-ai/dsh-client-connection'`，该复现缺少官方解析器，**不作为用户症状的根因**。
- 新增只读 `scripts/smoke-installed-host.mjs`，独立 Electron 进程中调用当前 archive 的 `createRuntimeResolution` / `PluginPackages` 与 Cordis `ModuleLoader.fromInternal().import('dsh-chatpass', profileManifestURL, {})`；`pwsh-403` exit 0，安装目录 host module 导入通过。过程仅创建进程内 resolution scope，不应用 ChatPass、读凭据、请求网关或修改 profile。
- 首轮诊断 `pwsh-400` 导入本身成功，但诊断脚本误用 `ctx.dispose()` 报 `TypeError: ctx.dispose is not a function`；这是诊断清理错误，已移除，不归因于插件。最终脚本显式捕获异常并设置 exitCode，最终 `pwsh-403` 无该错误。
- 实际服务 19387 的程序确认为当前安装的 Electron。所查磁盘 crash/startup logs 无此次底层 import 栈，未检查浏览器/session storage 或任何凭据。
- 冷进程 host import 成功，而运行中启用失败；热启用/解析缓存与冷启动差异仍是待验证假设，不宣称根因已证实。下一步应由用户保存会话后完整退出并重新打开 DSH，再启用已安装包；不要只刷新网页，不自动终止当前进程，不据此生成猜测性 alpha.3。真正的 apply、RPC/browser 和冷启动启用仍未验证。

## 2026-10-15 browser cold boot — alpha.3 packaging repair

- 用户冷启动截图与实际日志 `C:\Users\ASUS\AppData\Roaming\@deepseek-ai\dsh-desktop\logs\crash-2026-10-15T06-59-03-105Z-web-boot.log:18-22` 确认浏览器异常 `Uncaught SyntaxError: Cannot use import statement outside a module`。此前仅热加载假设被否定；此前 host import 成功仍准确，但不能证明 browser boot。
- 根因：同一个构建 owner `plugins/dsh-chatpass/scripts/build.mjs` 曾把 client 输出为 ESM，当前 DSH 按 classic script 加载，要求 `window.__ModuleLoader__.load({ id, factory(require) { ...; return module.exports; } })`；已核对实际安装 Codex 产物头尾。不是 Python/账号问题。
- 修复：host 保持 ESM，client 改为 CJS 加官方 loader 包装；新增 `scripts/check-client-bundle.mjs` 并挂入每次 build，JSDOM classic-script eval + 受控 require fixture，断言 id/factory/apply/name/inject/CSS。没有双模式 fallback 或关闭启动检查。
- 红测：本地原 alpha.2 client 精确重现该 SyntaxError，exit 1。旧安装目录检查为 ENOENT/Test-Path=False，本轮未改 profile/安装副本，未重启。
- 绿测 `pwsh-3` check exit 0：源码和测试类型检查、6 文件 49/49 测试、构建和新 classic-script 检查通过。`pwsh-4` pack exit 0；`plugins/dsh-chatpass/dsh-chatpass-0.1.0-alpha.3.tgz` SHA256 `75121CA391B05AACF6996A34100F2BBBCDDA52429CA438403B8D8F3CDA0AE1F2`。`pwsh-5` 解压真实归档并对其中 client 执行相同检查，exit 0。
- 未覆盖：fixture 不等于真实 DSH renderer；alpha.3 实际安装、冷启动与双入口交互仍需验证。不声称完整可用，不做真实网关/账号操作，不自动重启，不提交。下一步官方插件管理器安装 alpha.3 后验收。

## 2026-10-18 review and source-submission authorization

The user separately authorized reviewing the related implementation and documentation, committing them in scoped batches and pushing them. This supersedes only the historical no-Git boundary; it does not authorize installation, live accounts or publication.

Fresh `pnpm run check` passed source/test typechecks, 49 tests in 6 files and the host/client build with classic-script registration checks. A fresh `pnpm pack` archive contained only the manifest allowlist (17 files); the extracted client also passed `check-client-bundle.mjs`. Archive SHA-256: `1d9c9409d463f3bbba51ebaab98aa4b70fc64896b482ddc6408203294d67565c`. Existing historical archives were not overwritten; generated archives/build outputs and dependency/cache directories are excluded from the source submission. Gateway regression and review results are recorded in [current verification](../../../verification.md#2026-10-18-提交前复核).

Independent read-only source review found no concrete new blocking defect or actual secret in the proposed plugin sources. This is advisory review, not real-host activation evidence. The alpha.3 installation, browser cold start, authenticated Host/Origin/cookie rejection cases and live inference remain unverified.

## Historical Git receipt

main，HEAD `cefd86d4641a44efa95507db48aa27d7581cb17a`。no-commit：实施计划明确禁止自动 commit/push。任务改动未提交，因此 task clean=false；repository clean=false（另含 TaskStartSnapshot 的用户并发 dirty 状态）。没有新建 branch/worktree。保留安装产生的本地 `.pnpm-store/` 缓存，不作为源代码或发行物。
