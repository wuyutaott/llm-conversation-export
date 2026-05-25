#!/usr/bin/env python3
"""跨平台统一导出入口（Windows / macOS / Linux 通用）。

第一屏列出「新建导出任务」+ 每个未完成账号的「继续…」；↑↓/jk 移动，回车确认。
  - 选「继续」= 直接对该平台/账号断点续抓
  - 选「新建」= 再选平台 → 选「当前登录账号」或手动输入账号

随后通过环境变量把参数交给 browser-harness 执行 export.py：
  EXPORT_ROOT      仓库根（供 export.py / core.storage 定位 out/ 目录）
  EXPORT_PLATFORM  平台名（= adapters/ 下的模块名）
  EXPORT_ACCOUNT   可选账号；为空表示自动检测当前登录
  EXPORT_MODE      固定 fetch（抓正文 + 图片，缺清单先拉，已完成的断点续传）

用法：python run.py   （macOS/Linux 也可 ./run.sh，Windows 也可 run.cmd）
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

PLATFORM_LABELS = {"chatgpt": "ChatGPT", "grok": "Grok", "gemini": "Gemini"}


def platform_label(name):
    return PLATFORM_LABELS.get(name, name)


# ---------- 跨平台单键读取：返回 'up' / 'down' / 'enter' / 'other' ----------
def _read_key():
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch == "\x03":            # Ctrl-C
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("\x00", "\xe0"):  # 功能键 / 方向键前缀
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(ch2, "other")
        if ch in ("k", "K"):
            return "up"
        if ch in ("j", "J"):
            return "down"
        return "other"
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x1b":            # ESC：方向键序列 ESC [ A/B 或 ESC O A/B
            seq = sys.stdin.read(2)
            if seq in ("[A", "OA"):
                return "up"
            if seq in ("[B", "OB"):
                return "down"
            return "other"
        if ch in ("k", "K"):
            return "up"
        if ch in ("j", "J"):
            return "down"
        return "other"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _enable_ansi():
    """Windows 10+ 控制台默认未开 ANSI 转义；开启 VT 处理以支持菜单重绘/反显。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)     # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _menu_numbered(options):
    """非交互终端（管道/重定向）回退：打印编号，读一行序号。"""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input("输入序号后回车: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("无效，请重新输入")


def menu(options):
    """方向键菜单，返回所选下标。非 TTY 时回退编号输入。"""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _menu_numbered(options)
    _enable_ansi()
    n = len(options)
    sel = 0
    print("（↑↓/jk 移动，回车确认）")
    first = True
    while True:
        if not first:
            sys.stdout.write(f"\033[{n}A")   # 光标上移 n 行覆盖重绘
        first = False
        for i, opt in enumerate(options):
            if i == sel:
                sys.stdout.write(f"\r\033[K\033[7m ▶ {opt} \033[0m\n")
            else:
                sys.stdout.write(f"\r\033[K   {opt}\n")
        sys.stdout.flush()
        key = _read_key()
        if key == "up":
            sel = (sel - 1 + n) % n
        elif key == "down":
            sel = (sel + 1) % n
        elif key == "enter":
            return sel


def _scan_tasks(storage):
    """扫描 out/{平台}/{账号}/titles.csv，列出未完成任务。返回 (labels, entries)。
    entries[i] = (kind, platform, account)；kind 为 'new' 或 'resume'。"""
    labels = ["➕ 新建导出任务"]
    entries = [("new", "", "")]
    out_root = storage.OUT_ROOT
    if not os.path.isdir(out_root):
        return labels, entries
    for plat in sorted(os.listdir(out_root)):
        pdir = os.path.join(out_root, plat)
        if not os.path.isdir(pdir):
            continue
        for acct in sorted(os.listdir(pdir)):
            csv_path = os.path.join(pdir, acct, "titles.csv")
            if not os.path.isfile(csv_path):
                continue
            try:
                _, rows = storage.load_rows(csv_path)
            except Exception:
                continue
            total = len(rows)
            fin = sum(1 for r in rows if r.get(storage.STATUS_COL) == "完成")
            if fin < total:
                labels.append(f"继续 {platform_label(plat)}: {acct} ({fin}/{total})")
                entries.append(("resume", plat, acct))
    return labels, entries


def _list_platforms():
    adir = os.path.join(ROOT, "adapters")
    plats = []
    for f in sorted(os.listdir(adir)):
        if f.endswith(".py") and f != "__init__.py":
            plats.append(f[:-3])
    return plats


def main():
    # 注入仓库根并让 core 包可被 import（storage 仅用标准库，无需 browser-harness）
    os.environ["EXPORT_ROOT"] = ROOT
    sys.path.insert(0, ROOT)
    from core import storage

    # 1. 任务列表（新建 + 各未完成账号）
    print("==== 选择任务 ====")
    labels, entries = _scan_tasks(storage)
    idx = menu(labels)
    print(f"→ {labels[idx]}\n")
    kind, platform, acct = entries[idx]

    if kind != "resume":
        # 2a. 新建：选平台
        platforms = _list_platforms()
        if not platforms:
            sys.exit("✗ adapters/ 下没有任何平台")
        print("==== 选择平台 ====")
        platform = platforms[menu([platform_label(p) for p in platforms])]
        print(f"→ 平台: {platform}\n")

        # 2b. 新建：选账号
        print("==== 选择账号 ====")
        if menu(["使用当前登录的账号（自动检测）", "手动输入账号"]) == 0:
            acct = ""
            print("→ 账号: 自动检测当前登录")
        else:
            acct = input("请输入账号（邮箱等）: ").strip()
            print(f"→ 账号: {acct}")
        print()

    # 3. 启动 browser-harness 执行 export.py
    exe = shutil.which("browser-harness")
    if not exe:
        sys.exit("✗ 找不到 browser-harness，请确认已安装且在 PATH 中（见 docs/dependencies.md）")
    export_py = os.path.join(ROOT, "export.py")
    code = ("import sys; sys.stdout.reconfigure(line_buffering=True); "
            f"exec(open({export_py!r}, encoding='utf-8').read())")
    env = dict(os.environ)
    env["EXPORT_PLATFORM"] = platform
    env["EXPORT_ACCOUNT"] = acct
    env["EXPORT_MODE"] = "fetch"
    env["EXPORT_ROOT"] = ROOT

    print(f"==== 开始：{platform} / {acct or '当前登录'} ====")
    # Windows 上 browser-harness 若是 .cmd/.bat 包装脚本，需经 shell 才能执行
    use_shell = os.name == "nt" and exe.lower().endswith((".cmd", ".bat"))
    sys.exit(subprocess.run([exe, "-c", code], env=env, shell=use_shell).returncode)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消")
        sys.exit(130)
