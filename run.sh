#!/usr/bin/env bash
# 统一导出入口：选平台 → 选账号 → 选动作 → 开始。
#   - 平台 = adapters/*.py（以后加新平台只需丢一个 adapter 文件，菜单自动出现）
#   - 账号 = out/{平台}/ 下已有账号 + 「当前登录账号(自动检测)」
#   - 导出脚本自检：没有 titles.csv 就先拉取，有就按 CSV 标记从未完成处续抓
#
# 用法： ./run.sh        然后按提示选择
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- 1. 选平台（扫描 adapters/*.py）----------
platforms=()
for f in "$ROOT"/adapters/*.py; do
    b="$(basename "$f" .py)"
    [ "$b" == "__init__" ] && continue
    platforms+=("$b")
done
if [ ${#platforms[@]} -eq 0 ]; then
    echo "✗ adapters/ 下没有任何平台"; exit 1
fi

echo "==== 选择平台 ===="
PS3="输入序号选平台: "
select platform in "${platforms[@]}"; do
    [ -n "$platform" ] && break
    echo "无效选择，请重输"
done
echo "→ 平台: $platform"
echo

# ---------- 2. 选账号 ----------
OUTDIR="$ROOT/out/$platform"
accounts=()
if [ -d "$OUTDIR" ]; then
    for a in "$OUTDIR"/*/; do
        [ -d "$a" ] || continue
        name="$(basename "$a")"
        csv="$a/titles.csv"
        if [ -f "$csv" ]; then
            total=$(($(wc -l < "$csv") - 1))
            done=$(grep -c ',完成,' "$csv" 2>/dev/null || echo 0)
            accounts+=("$name  [$done/$total 已完成]")
        else
            accounts+=("$name  [无清单]")
        fi
    done
fi

echo "==== 选择账号 ===="
echo "（选「自动检测」= 导出当前 Chrome 登录的账号；选已有账号需与当前登录一致）"
options=("当前登录账号(自动检测)" "${accounts[@]}")
PS3="输入序号选账号: "
select choice in "${options[@]}"; do
    [ -n "$choice" ] && break
    echo "无效选择，请重输"
done
if [ "$choice" == "当前登录账号(自动检测)" ]; then
    acct=""
    echo "→ 账号: 自动检测当前登录"
else
    acct="${choice%%  [*}"   # 去掉 "  [.../...]" 进度后缀
    echo "→ 账号: $acct"
fi
echo

# ---------- 3. 选动作 ----------
echo "==== 选择动作 ===="
PS3="输入序号选动作: "
select act in "导出(抓正文+图片，缺清单先拉取)" "仅刷新会话清单"; do
    [ -n "$act" ] && break
    echo "无效选择，请重输"
done
case "$act" in
    "仅刷新会话清单") MODE="list" ;;
    *) MODE="fetch" ;;
esac
echo

# ---------- 4. 开始 ----------
echo "==== 开始：$platform / ${acct:-当前登录} / $MODE ===="
export EXPORT_PLATFORM="$platform"
export EXPORT_ACCOUNT="$acct"
export EXPORT_MODE="$MODE"
exec browser-harness -c "import sys; sys.stdout.reconfigure(line_buffering=True); exec(open('$ROOT/export.py').read())"
