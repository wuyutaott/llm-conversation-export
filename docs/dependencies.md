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
3. Python 3.9+ 可用（命令名 macOS/Linux 通常是 `python3`，Windows 通常是 `python`）。

满足后：macOS/Linux 跑 `./run.sh`，Windows 跑 `run.cmd`（或任意平台 `python run.py`）。

## 平台支持

编排与导出逻辑（`run.py` / `core/` / `adapters/`）只用 Python 标准库，已做到 Windows / macOS / Linux 通用，无任何 POSIX 专属调用。

唯一的平台前提是 **`browser-harness` 本身要能在该系统上运行并连上本机 Chrome**——它是独立 CLI，需各自在对应系统上安装确认。`run.py` 启动时会用 `shutil.which` 检测，找不到会给出明确提示而非崩溃。

## Windows 从零安装

本项目自身零额外依赖（只用 Python 标准库），Windows 上只需装好 **Python** 和 **browser-harness** 两样。命令以 PowerShell 为例。

### 1. Python 3.9+

```powershell
winget install Python.Python.3.13
# 或从 https://www.python.org/downloads/ 下载，安装时勾选 "Add python.exe to PATH"
python --version   # 验证
```

### 2. uv（用于安装 browser-harness）

```powershell
winget install astral-sh.uv
# 或：powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3. browser-harness（核心引擎，不在 PyPI）

从 GitHub 克隆后用 `uv tool install` 装成全局命令（需先有 git：`winget install Git.Git`）：

```powershell
git clone https://github.com/browser-use/browser-harness
cd browser-harness
uv tool install -e .
```

新开一个 PowerShell 窗口让 PATH 生效，验证：

```powershell
where.exe browser-harness
```

> uv 在 Windows 会生成 `browser-harness.exe`，`run.py` 用 `shutil.which` 即可直接找到并调用，无需额外配置。

### 4. 让 Chrome 允许远程调试（二选一）

**方式 1（推荐，用日常 Chrome、保留登录态）**

1. Chrome 地址栏打开 `chrome://inspect/#remote-debugging`
2. 勾选 "Allow remote debugging for this browser instance"（每个配置文件勾一次，永久生效）
3. 首次连接 Chrome 弹 "Allow remote debugging?" 时点 **Allow**

**方式 2（独立干净配置、无弹窗，但不带现有登录）**

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\bh-profile
$env:BU_CDP_URL = "http://127.0.0.1:9222"
```

`--user-data-dir` 必须是**非默认路径**（不能是 `%LOCALAPPDATA%\Google\Chrome\User Data`，否则 Chrome 136+ 会静默忽略端口）。

日常导出建议用方式 1，因为需要复用你已登录 ChatGPT / Grok / Gemini 的状态。

### 5. 自检 + 启动

```powershell
browser-harness -c "print(page_info())"   # 能打印页面信息即正常

cd 你的路径\memory-exportor
run.cmd        # 或 python run.py
```

若自检报连接错误，运行 `browser-harness --doctor`，看 `chrome running` 与 `daemon alive` 两行定位问题。
