# dsh-chatpass 连接模式实施计划

> 授权时序：下方禁止提交／安装等条款记录原实施任务的历史边界。用户于 2026-10-18 另行确认，将相关实现与配套文档审查后分批提交并推送；本次 Git 授权不等于安装、真实账号操作或发布授权。安装历史与版本缺口见[实施证据](aegis/work/2026-10-14-dsh-chatpass/90-evidence.md)。

## Goal / 批准范围

依据已批准 `docs/dsh-chatpass-design.md` 与用户双设置入口要求，实现阶段 0/1：用户自行启动核心、插件连接本机网关、独立 chatpass provider、模型目录、模型页卡片与内置插件专页共用状态。授权开展实现；不授权安装到现有 DSH profile、真实账号推理、升级、发布或提交。

## Architecture / Tech stack

新增 `plugins/dsh-chatpass/`，TypeScript + React，Node 22.19+，DSH Cordis 插件。HTTP 和认证在宿主；前端仅通过宿主接口操作。核心不变，只有宿主适配器将 DSH 消息传给原生 Responses。首版安全持久化接口未验证则只保存内存密钥，非秘密配置须单一来源。双入口使用 settings.models.footer 与 settings.plugins.tab，实际类型与打包在隔离依赖环境验证。

## Baseline / Authority refs

设计文档、docs/design.md、docs/clients.md、docs/protocol-notes.md、docs/work-status.md、docs/web-console.md 和现有 SIWC 工作。TaskStartSnapshot：2026-10-14，main / cefd86d，已有 README、协议/验证/进度/设计文档、adapters.py、bridges.py、tests/test_gateway.py 修改，另有 response-compatibility 工作目录与两个新增 Python 测试；一律保留，不归本任务。SDK 与宿主版本须从可用包核验而非假定。设计中历史聚合缺口以最新实际工作为准，本任务不动该实现。

## Necessity / Ownership / Compatibility

现有 Python 网关无法提供 DSH 原生 provider 注册与设置卡片，必须新增薄插件；不新建 OAuth 或上游语义所有者。保留所有 /v1 与管理台契约、全局网关密钥、独立客户端与 openai-codex provider。UI 两入口同一宿主状态、单一修订号，失败不提交配置，在途请求捕获配置。只支持显式 127.0.0.1 /v1，无跳转、代理或推理重试，不扫描本地端口。未验证模型元数据不得伪造为上游能力。

## TDD Route

未获 strict TDD 指令；采用实现配套隔离回归，不声称 RED/GREEN 或严格 TDD。安全、状态和适配器行为必须有负向测试与独立审阅；不调用真实服务验证。

## Tasks

1. 核验依赖：定位 DSH llm/adapter、host webserver 与前端插件槽位类型、秘密接口和未知模型元数据；记录版本。核验 stdio 属托管阶段，不阻塞首版。发现会改变设计的契约缺口时明确记录，不编造 SDK。
2. 新增包与安全连接 owner：package.json、tsconfig、构建配置、src/connection.ts、src/transport.ts；严格本机地址、内存秘密、revision 冲突、原子验证保存、失败保留旧配置、有界目录/错误、取消、超时、不跟随跳转、不用环境代理。每次请求捕获连接。
3. src/provider.ts 与 src/index.ts：复用实际 SDK Responses 与 PiAiAdapter，注册 chatpass，真实目录映射，完整消息/工具/reasoning、取消、终态失败、不重放。宿主接口只返回非秘密状态，写接口验证来源/认证与请求大小。
4. src/client.tsx、src/client-store.ts：同一共享状态，模型轻卡片、内置插件完整表单、更多设置、草稿与冲突、密钥清除、授权说明、目录缓存/失败/更新时间、诊断。浏览器无 node 依赖、无静态远程资源或秘密持久化。
5. tests/：安全 URL/redirect/错误脱敏、目录限额、状态 revision、失败保存、捕获旧连接、取消与流失败；宿主注册与前端双入口共享/草稿测试；打包/peer/exports 检查。可用浏览器测试以隔离 fixture 实施，不用真实 GUI 会话。
6. 独立审阅与修复：连接安全与 provider 兼容优先，修复重要发现；运行 test/typecheck/build/pack 与 whitespace，记录缺口。插件 README 和兼容证据说明用户安装步骤，但不执行现有 profile 安装。

## Verification

在插件目录使用 bundled node + pnpm：install 后 pnpm run check（typecheck、隔离单元/集成测试、build），pack dry run 或 tar 内容核查。使用 fake keys、临时 loopback mock、合成 SSE 与 DSH message fixtures，禁止真实 ChatPass 19840 或 OpenAI 调用。Python 核心未改不将用户并发修改失败归本任务。浏览器测试与实际 DSH 端到端安装未执行则明确标为未验证，不能用构建通过代替。完成后核对 git diff/status，确认未修改现有配置/凭据；只展示插件交付与证据，不自动 commit/push。

## Risks / Stop / Execution

DSH peer 版本、模型元数据、Responses API 差异和 authenticated UI transport 必须用类型/实现验证。无安全持久化则内存，不明文 fallback。未知 upstream 能力不得宣称支持。缺失依赖可隔离安装工作区依赖，不改全局 SDK。执行 inline 为主，独立只读 provider 调查已委托；后续可独立审阅。用户无需再次授权本范围实现；真实账号、安装到现有 DSH、公开发布、破坏性迁移必须另行确认。无旧插件路径可退役，其他 provider 均保留。
