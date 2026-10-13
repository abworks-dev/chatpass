# Third-party notices

## Tabler dashboard stylesheet

ChatPass's local console adapts the layout conventions and stylesheet of [Tabler](https://github.com/tabler/tabler), a framework-neutral open-source dashboard template. The application behavior and console-specific HTML, CSS and JavaScript are independently implemented here.

- Version: `@tabler/core@1.6.1`
- Source commit: `ec33733290bd0f314ca19f6be58bc69a6ab3e4fa`
- License: MIT; the complete upstream notice is included in [LICENSE.tabler](src/chatpass/static/vendor/LICENSE.tabler). The original stylesheet copyright header is preserved.
- Vendored asset: [tabler.min.css](src/chatpass/static/vendor/tabler.min.css), copied unchanged from `package/dist/css/tabler.min.css` in `https://registry.npmjs.org/@tabler/core/-/core-1.6.1.tgz`.
- Tarball SHA-512 integrity: `sha512-lgmR9gcxRp5JlLxcmRi9Rz85JrEvH6Bz71KnlcPVSa97wtwQNjgSs3g3fxwbAlulHyRMghhIV04QUuW3pqDsIg==`
- Stylesheet SHA-256: `370a2d4e608ae518351d10528fc7dbe19b354ae5062c51b2a9cb294850d65403`
- No Tabler JavaScript, demo data, remote fonts, analytics or other bundled third-party libraries are imported. The stylesheet contains embedded data images, not externally fetched assets.

MIT applies to this third-party asset, not automatically to all ChatPass code. ChatPass's own license remains unselected; see [licensing](README.en.md#licensing-and-affiliation).
