# Third-party notices / 第三方声明

## License scope / 许可范围

ChatPass's original code and documentation are licensed under [MIT](LICENSE), copyright (c) 2026 ChatPass contributors. This grant does not replace third-party copyright notices or licenses, grant trademark rights, or authorize use of upstream services.

ChatPass 自有代码和文档采用 [MIT](LICENSE)，版权为 2026 ChatPass contributors。此许可不替代第三方版权与许可声明，不授予商标权，也不授予上游服务使用资格。

## Tabler dashboard stylesheet / Tabler 管理台样式

ChatPass's local console adapts the layout conventions and stylesheet of [Tabler](https://github.com/tabler/tabler), a framework-neutral open-source dashboard template. The application behavior and console-specific HTML, CSS and JavaScript are independently implemented here.

管理台使用 Tabler 的布局约定和样式表；应用行为及项目专用 HTML、CSS、JavaScript 为独立实现。保留原样式文件及完整作者声明。

- Version / 版本: `@tabler/core@1.6.1`
- Source commit / 来源提交: `ec33733290bd0f314ca19f6be58bc69a6ab3e4fa`
- Copyright / 版权: `Copyright (c) 2018-2026 The Tabler Authors`
- License / 许可证: MIT; the complete upstream notice is included in [LICENSE.tabler](src/chatpass/static/vendor/LICENSE.tabler). The original stylesheet copyright header is preserved.
- Vendored asset / 内置文件: [tabler.min.css](src/chatpass/static/vendor/tabler.min.css), copied unchanged from `package/dist/css/tabler.min.css` in `https://registry.npmjs.org/@tabler/core/-/core-1.6.1.tgz`.
- Tarball SHA-512 integrity: `sha512-lgmR9gcxRp5JlLxcmRi9Rz85JrEvH6Bz71KnlcPVSa97wtwQNjgSs3g3fxwbAlulHyRMghhIV04QUuW3pqDsIg==`
- Stylesheet SHA-256: `370a2d4e608ae518351d10528fc7dbe19b354ae5062c51b2a9cb294850d65403`
- No Tabler JavaScript, demo data, remote fonts, analytics or other bundled third-party libraries are imported. The stylesheet contains embedded data images, not externally fetched assets.

## Python dependencies / Python 依赖

Runtime and development dependencies are listed in [pyproject.toml](pyproject.toml), with resolved versions in [uv.lock](uv.lock). Their source code is not vendored into ChatPass's wheel or source distribution. Package managers install them as separate distributions, each subject to its own license and copyright notices; ChatPass's MIT license does not relicense them. Redistribution of a combined environment or executable requires retaining applicable dependency notices as well.

运行与开发依赖分别列于包配置，锁定版本见锁文件。它们的源码未内置于 ChatPass 的 wheel 或源码包，而由包管理器单独安装；各自许可证与版权声明继续有效，不统一改为 MIT。若进一步分发整套环境或可执行程序，也须保留相关依赖声明。

## OpenAI protocol references / OpenAI 协议参考

The official Sign in with ChatGPT DevKit is not a bundled dependency. Its code, documentation and assets have not been copied or distributed with ChatPass. Protocol sources are recorded in [protocol notes](docs/protocol-notes.md); interoperability does not relicense the DevKit or grant account, commercial-service or remote-hosting authorization. ChatPass is not affiliated with or endorsed by OpenAI.

官方 DevKit 不是内置依赖，本项目未复制或分发其代码、文档或资产。协议参考记录于协议说明；互操作不意味着重新许可官方 DevKit，也不授予账号、商用服务或远程托管权限。本项目与 OpenAI 无隶属或背书关系。

## Distribution notices / 分发声明

The source distribution retains [LICENSE](LICENSE), this notice and [LICENSE.tabler](src/chatpass/static/vendor/LICENSE.tabler) at their source paths. The wheel also carries all three under its `.dist-info/licenses/` directory, and the complete Tabler notice remains next to the installed stylesheet. Preserve these notices when redistributing copies or substantial portions.

源码包保留上述三个声明文件；wheel 在 `.dist-info/licenses/` 下携带它们，并在内置样式旁保留 Tabler 完整许可。分发副本或实质性部分时须保留相应声明。
