# dsh-chatpass — checkpoint

## Task as of 2026-10-14

阶段 0/1 的连接模式代码、隔离回归与本地打包已实现。停止边界是隔离实现，不是发布或真实宿主验收。阶段 2 托管模式未开始。批准设计和实施计划仍是产品依据；真实宿主条件不得从 jsdom / build 推断。

- 新包：`plugins/dsh-chatpass/`。双设置入口、共享连接 owner / 修订号 / 客户端 store、独立 `chatpass` provider、原生 Responses SDK、仅内存配置与密钥。
- 使用独立 `/chatpass` Connection RPC channel；不拦截宿主已有 `/api`，不增加裸 HTTP 路由。
- reviewer 发现并修复：保留 opaque reasoning 的请求 include、SDK transient key resolver、原始错误日志、失败刷新快照、新鲜度 TTL、内嵌表单非秘密草稿生命周期。
- `pnpm run check` 最终通过：源与测试严格 tsc、6 文件 49 测试、构建；`pnpm pack` 与 tar 清单通过。生成目录先限制到插件自身 `lib` 再清理，避免旧 CSS sidecar 混入包。
- 当前 SDK 无已核验的宿主导航 guard；modal 有关闭确认，内嵌页改为跨卸载保留非秘密草稿、清除密码、保留 revision/generation 冲突。完整宿主实际退出确认行为仍需验收，不声称完全满足所有设计交互。

## Resume / highest-value next verification

用户已在后续请求中授权安装到当前 desktop；此授权取代早期不安装当前 profile 的限制，但不授权真实账号操作。alpha.1 被宿主兼容检查正确拒绝；alpha.2 改为 SDK/dev/peer 与实际 `0.2.0-rc.2` 宿主匹配，类型检查、49/49 测试、构建、打包与实际宿主官方兼容检查（含负向控制）通过，但后续真实浏览器启动暴露了 ESM 与 classic-script loader 不兼容。alpha.3 保持 SDK/peer 版本并修正客户端打包，隔离及包内客户端检查通过，见 [版本化证据](90-evidence.md)。当前安装候选为 `plugins/dsh-chatpass/dsh-chatpass-0.1.0-alpha.3.tgz`；不要再用 alpha.1/alpha.2，也不要添加版本豁免。alpha.3 的实际安装、冷启动、挂载和交互仍待验证。

desktop 仅允许 Electron 官方插件管理器操作，CLI 明确拒绝；当前代理没有可用的页面操作工具，未绕过管理器改配置。下一步是在侧栏 Plugins → Add Plugin 提交新包，再验证真实装载、authenticated Connection 的 Host/Origin/cookie 拒绝与成功路径、双入口、导航/重载、实际浏览器 CSP/主题/窄屏/键盘。之后才考虑另经用户许可的真实 ChatPass / 上游交互。

## Historical Git / drift

main，HEAD `cefd86d4641a44efa95507db48aa27d7581cb17a`；无任务 branch/worktree，无提交。TaskStartSnapshot 里的用户并发 Python/文档改动保持原样，不纳入本任务。任务文件为新增插件包、实施计划及该工作记录。依赖安装还生成根目录 `.pnpm-store/` 本地缓存；不将其作为源码/发行物。`node_modules`、`lib`、`.tgz` 被插件 `.gitignore` 排除。
