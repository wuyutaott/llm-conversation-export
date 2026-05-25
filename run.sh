#!/usr/bin/env bash
# 统一导出入口。第一屏列出「新建导出任务」+ 每个未完成账号的「继续…」；方向键 ↑↓ 选择，回车确认。
#   - 选「继续」=直接对该平台/账号断点续抓
#   - 选「新建」=再选平台 → 选「当前登录账号」或手动输入账号
# 用法： ./run.sh
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- 方向键菜单：menu <选项...> → 结果索引存入 REPLY_INDEX ----------
menu() {
    local opts=("$@") n=$# sel=0 first=1 key k2 i
    printf '%s\n' "（↑↓ 移动，回车确认）" >&2
    while true; do
        [ $first -eq 0 ] && printf '\033[%dA' "$n" >&2
        first=0
        for i in "${!opts[@]}"; do
            if [ "$i" -eq "$sel" ]; then
                printf '\r\033[K\033[7m ▶ %s \033[0m\n' "${opts[$i]}" >&2
            else
                printf '\r\033[K   %s\n' "${opts[$i]}" >&2
            fi
        done
        IFS= read -rsn1 key || break
        if [ "$key" = $'\x1b' ]; then IFS= read -rsn2 -t 1 k2 || true; key+="$k2"; fi
        case "$key" in
            $'\x1b[A'|$'\x1bOA'|k) sel=$(( (sel - 1 + n) % n )) ;;
            $'\x1b[B'|$'\x1bOB'|j) sel=$(( (sel + 1) % n )) ;;
            '') REPLY_INDEX=$sel; return 0 ;;
        esac
    done
    REPLY_INDEX=0
}

platform_label() {
    case "$1" in
        chatgpt) echo "ChatGPT" ;;
        grok) echo "Grok" ;;
        *) echo "$1" ;;
    esac
}

# ---------- 1. 任务列表（新建 + 各未完成账号）----------
labels=("➕ 新建导出任务")
kinds=("new"); plats=(""); accts=("")
for csv in "$ROOT"/out/*/*/titles.csv; do
    [ -f "$csv" ] || continue
    acct="$(basename "$(dirname "$csv")")"
    plat="$(basename "$(dirname "$(dirname "$csv")")")"
    total=$(($(wc -l < "$csv") - 1))
    fin=$(grep -c ',完成,' "$csv" 2>/dev/null || echo 0)
    if [ "$fin" -lt "$total" ]; then
        labels+=("继续 $(platform_label "$plat"): $acct ($fin/$total)")
        kinds+=("resume"); plats+=("$plat"); accts+=("$acct")
    fi
done

echo "==== 选择任务 ===="
menu "${labels[@]}"
idx=$REPLY_INDEX
echo "→ ${labels[$idx]}"
echo

if [ "${kinds[$idx]}" = "resume" ]; then
    platform="${plats[$idx]}"
    acct="${accts[$idx]}"
else
    # ---------- 2a. 新建：选平台 ----------
    platforms=()
    for f in "$ROOT"/adapters/*.py; do
        b="$(basename "$f" .py)"
        [ "$b" = "__init__" ] && continue
        platforms+=("$b")
    done
    [ ${#platforms[@]} -eq 0 ] && { echo "✗ adapters/ 下没有任何平台"; exit 1; }
    echo "==== 选择平台 ===="
    menu "${platforms[@]}"
    platform="${platforms[$REPLY_INDEX]}"
    echo "→ 平台: $platform"
    echo

    # ---------- 2b. 新建：选账号 ----------
    echo "==== 选择账号 ===="
    menu "使用当前登录的账号（自动检测）" "手动输入账号"
    if [ "$REPLY_INDEX" -eq 0 ]; then
        acct=""
        echo "→ 账号: 自动检测当前登录"
    else
        read -rp "请输入账号（邮箱等）: " acct
        echo "→ 账号: $acct"
    fi
fi
echo

# ---------- 3. 开始 ----------
echo "==== 开始：$platform / ${acct:-当前登录} ===="
export EXPORT_PLATFORM="$platform"
export EXPORT_ACCOUNT="$acct"
export EXPORT_MODE="fetch"
exec browser-harness -c "import sys; sys.stdout.reconfigure(line_buffering=True); exec(open('$ROOT/export.py').read())"
