"""统一导出入口。由 browser-harness 以 exec 方式运行，run.sh 通过环境变量传参：
    EXPORT_PLATFORM  平台名（= adapters/ 下的模块名，如 chatgpt / grok）
    EXPORT_ACCOUNT   可选，指定账号；与当前登录不一致会中止
    EXPORT_MODE      fetch（默认，抓正文+图片）/ list（只刷新会话清单）

不要直接 python 运行——需要 browser-harness 注入的 js/page_info/new_tab/wait_for_load。
"""
import importlib
import os
import sys

ROOT = "/Users/stone/Documents/wuyutaott.com/memory-exportor"
sys.path.insert(0, ROOT)

from core import browser, driver  # noqa: E402

# 绑定 browser-harness 注入到 exec 全局的 helper（python 直跑会 NameError，必须经 browser-harness）
browser.bind(js=js, page_info=page_info, new_tab=new_tab, wait_for_load=wait_for_load, cdp=cdp)  # noqa: F821

_platform = os.environ.get("EXPORT_PLATFORM")
_account = os.environ.get("EXPORT_ACCOUNT") or None
_mode = os.environ.get("EXPORT_MODE", "fetch")
if not _platform:
    raise SystemExit("缺少 EXPORT_PLATFORM 环境变量")

_mod = importlib.import_module(f"adapters.{_platform}")
driver.run(_mod.adapter, _account, _mode)
