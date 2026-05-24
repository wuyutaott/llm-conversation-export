# 依赖

## Python

只用**标准库**，无需 `pip install` 任何第三方包。要求 Python 3.9+（开发用 3.13）。
`requirements.txt` 故意为空（仅注释）。

## 外部工具：browser-harness（必需）

整个项目靠 `browser-harness` 驱动你**已登录的 Chrome**，在页面内用 `fetch` 调各平台自己的接口——
所以无需填 token / 密码，用的是你浏览器现成的登录态。

- 它不在 PyPI，是一个独立 CLI（本机装在 `~/.local/bin/browser-harness`）。
- 调用形态：`browser-harness -c "<python代码>"`，运行时会自动启动后台 daemon 并连上 Chrome。
- 它向被执行代码注入了 `js / page_info / new_tab / wait_for_load / cdp` 等 helper，
  本项目通过 `core.browser.bind(...)` 接收并复用（见 [architecture.md](architecture.md)）。

### 自检

```bash
browser-harness -c 'print(page_info())'
```

能打印当前标签页信息即正常。若报连接错误，确认 Chrome 在运行、且 browser-harness 能连上它。

## 运行前提

1. Chrome 已登录目标平台（chatgpt.com / grok.com）。
2. `browser-harness` 可用（在 `$PATH` 里）。

满足后直接 `./run.sh`。
