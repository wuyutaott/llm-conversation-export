# memory-exportor

把已登录网页版 AI 助手（ChatGPT、Grok…）的聊天记录导出成本地文件：原始 JSON + 可读 Markdown + 图片/附件。靠 `browser-harness` 驱动你已登录的 Chrome，用浏览器现成登录态调各平台接口，不碰密码。

## 快速开始

```bash
./run.sh        # macOS / Linux：选平台 → 选账号，开始导出
run.cmd         # Windows（或任意平台 python run.py）
```

前提：Chrome 已登录目标平台，且本机有 `browser-harness`。详见 [docs/usage.md](docs/usage.md)。

Windows 首次安装依赖可一键完成：`powershell -ExecutionPolicy Bypass -File .\install-windows.ps1`（详见 [docs/dependencies.md](docs/dependencies.md)）。

## 结构

- `run.sh` / `export.py` — 入口
- `core/` — 通用编排（浏览器交互、存储、主循环）
- `adapters/` — 各平台适配器（`chatgpt.py`、`grok.py`、`gemini.py`…，一个平台一个文件）
- `out/{平台}/{账号}/` — 导出结果（已被 .gitignore）

## 文档

- [docs/usage.md](docs/usage.md) — 使用说明（菜单、续传、停止、查看进度）
- [docs/dependencies.md](docs/dependencies.md) — 依赖（browser-harness、Python）
- [docs/architecture.md](docs/architecture.md) — 架构 & 如何新增一个平台
- [docs/notes.md](docs/notes.md) — 实测经验与坑（限流、认证、各平台接口细节）

## 特性

断点续传 · 账号隔离 · 图片/附件下载 · 进度条+耗时 · 429 退避 · 连续失败熔断 · 指定账号一致性校验
