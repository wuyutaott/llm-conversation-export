#requires -Version 5
<#
.SYNOPSIS
  Windows 一键安装本项目运行所需依赖：uv、Python、browser-harness，并完成自检。

.DESCRIPTION
  本项目自身零额外依赖（只用 Python 标准库）。此脚本负责装好两样外部依赖：
    1) Python 3（供 run.py 使用）
    2) browser-harness（核心引擎，从 GitHub 克隆后用 uv 安装成全局命令）
  Chrome 的「允许远程调试」需人工点选，脚本最后会给出指引并尝试打开对应页面。

.EXAMPLE
  # 在 PowerShell 中（首次可能需放开执行策略）：
  powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
#>
[CmdletBinding()]
param(
    # browser-harness 克隆目录，默认放用户主目录下
    [string]$HarnessDir = (Join-Path $HOME 'browser-harness')
)

$ErrorActionPreference = 'Stop'

function Info($m)  { Write-Host "→ $m"  -ForegroundColor Cyan }
function Ok($m)    { Write-Host "✓ $m"  -ForegroundColor Green }
function Warn($m)  { Write-Host "⚠ $m"  -ForegroundColor Yellow }
function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# uv 与 uv tool 安装的可执行文件默认落在此目录；装完即时加入本会话 PATH 以便验证
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'
function Add-LocalBinToPath {
    if (Test-Path $LocalBin) {
        if ($env:Path -notlike "*$LocalBin*") { $env:Path = "$LocalBin;$env:Path" }
    }
}

Write-Host ""
Write-Host "==== memory-exportor Windows 依赖一键安装 ====" -ForegroundColor White
Write-Host ""

# ---------- 1. git ----------
if (Have git) {
    Ok "git 已安装"
} else {
    Info "未检测到 git，尝试用 winget 安装 ..."
    if (Have winget) {
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
        Warn "git 安装后可能需要重开 PowerShell 才在 PATH 生效；若后续 clone 失败，请重开窗口再跑本脚本"
    } else {
        throw "缺少 git 且无 winget。请手动安装 Git（https://git-scm.com/download/win）后重试。"
    }
}

# ---------- 2. uv ----------
if (Have uv) {
    Ok "uv 已安装"
} else {
    Info "安装 uv ..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Add-LocalBinToPath
    if (-not (Have uv)) { throw "uv 安装后仍找不到，请重开 PowerShell 后再次运行本脚本。" }
    Ok "uv 安装完成"
}

# ---------- 3. Python ----------
# run.cmd 调用系统 python 命令，故用 winget 装 python.org 官方包（python.exe 必然可用）。
if (Have python) {
    Ok ("Python 已就绪：" + (python --version 2>&1))
} else {
    Info "未检测到 python，尝试安装 ..."
    if (Have winget) {
        winget install --id Python.Python.3.13 -e --source winget --accept-package-agreements --accept-source-agreements
        Warn "Python 安装后通常需重开 PowerShell 才在 PATH 生效；若稍后 run.cmd 提示找不到 python，请重开窗口。"
    } else {
        Warn "无 winget，无法自动安装 Python。"
        Warn "请从 https://www.python.org/downloads/ 下载安装，务必勾选 'Add python.exe to PATH'，然后重开 PowerShell 重跑本脚本。"
    }
}

# ---------- 4. browser-harness ----------
if (Have browser-harness) {
    Ok "browser-harness 已安装"
} else {
    if (-not (Test-Path $HarnessDir)) {
        Info "克隆 browser-harness 到 $HarnessDir ..."
        git clone https://github.com/browser-use/browser-harness $HarnessDir
    } else {
        Info "已存在 $HarnessDir，跳过克隆"
    }
    Info "用 uv 安装 browser-harness（可编辑模式）..."
    Push-Location $HarnessDir
    try {
        # 锁定 Python 3.13：browser-harness 依赖 pillow==12.2.0 等，3.13 对全部依赖均有 Windows 预编译 wheel，
        # 不锁定时 uv 可能选用更新的 Python，个别包若尚无对应 wheel 会退化为本地编译（需 VC++ 工具链）。
        uv tool install --python 3.13 -e .
        uv tool update-shell   # 把 uv tool 的 bin 目录持久写入 PATH
    } finally {
        Pop-Location
    }
    Add-LocalBinToPath
    if (Have browser-harness) { Ok "browser-harness 安装完成" }
    else { Warn "browser-harness 安装后当前会话找不到，请重开 PowerShell 后用 'browser-harness --doctor' 验证。" }
}

# ---------- 5. 自检 ----------
Write-Host ""
Info "依赖安装完毕。下面是连接 Chrome 的最后一步（需人工操作）："
Write-Host ""
Write-Host "  方式 1（推荐，用日常 Chrome、保留登录态）：" -ForegroundColor White
Write-Host "    1) 在 Chrome 打开  chrome://inspect/#remote-debugging"
Write-Host "    2) 勾选 'Allow remote debugging for this browser instance'（每个配置文件勾一次）"
Write-Host "    3) 首次连接时在 Chrome 弹窗点 Allow"
Write-Host ""
Write-Host "  方式 2（独立干净配置、无弹窗，但不带现有登录）：" -ForegroundColor White
Write-Host '    chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\bh-profile'
Write-Host '    然后设置：$env:BU_CDP_URL = "http://127.0.0.1:9222"'
Write-Host ""

# 尝试帮用户打开 inspect 页（失败不影响整体）
try {
    $chrome = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($chrome) {
        Info "正在为你打开 chrome://inspect/#remote-debugging ..."
        Start-Process $chrome 'chrome://inspect/#remote-debugging'
    }
} catch { }

Write-Host ""
Info "完成上面 Chrome 设置后，自检连接："
Write-Host '    browser-harness -c "print(page_info())"'
Write-Host ""
Info "自检通过即可启动导出（在本项目目录下）："
Write-Host "    run.cmd          # 或 python run.py"
Write-Host ""
Ok "安装脚本结束。如某些命令提示找不到，请重开一个 PowerShell 窗口让 PATH 生效。"
