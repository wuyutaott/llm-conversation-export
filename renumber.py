#!/usr/bin/env python3
"""把已导出数据按新命名规则重新编号：越新（创建越晚）的会话序号越大。

为什么：旧规则 001 = 最新，增量导出新会话时 001 已被占用、会引发整体重排。
新规则下最旧 = 001、最新 = 最大号，新增会话只在末尾追加更大的号，旧号永久不变。

这是一次性迁移：按「创建时间」升序重排 → 重新编号 1..N → 重命名 json/markdown/images
+ 改写 titles.csv 的「序号」「文件」两列。和 storage.write_titles 的新逻辑保持一致。

安全：默认只预览（dry-run）；加 --apply 才真正改名。改名用两阶段（先全改临时名，
再改最终名），避免序号对调时互相覆盖。

用法：
  python3 renumber.py                  # 预览所有平台/账号的改名计划
  python3 renumber.py --apply          # 执行（全部）
  python3 renumber.py chatgpt --apply  # 只迁移指定平台
  python3 renumber.py chatgpt 你的邮箱 --apply
"""
import csv
import os
import sys

from core import storage

PLATFORMS = ["chatgpt", "gemini", "grok"]
TMP = ".__migrating__"


def _suffix(base):
    """从 base(如 012_标题) 取下划线后的标题部分，保留原文件名后缀不变。"""
    return base.split("_", 1)[1] if "_" in base else base


def _plan_account(acc_dir):
    """读 csv，按创建时间升序重排并重新编号，返回 (csv_path, fields, plan)。
    plan: [(row, old_base, new_base, new_idx)]，按新序号升序。"""
    csv_path = os.path.join(acc_dir, "titles.csv")
    if not os.path.exists(csv_path):
        return None
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames)
        rows = list(reader)
    rows.sort(key=lambda r: (r.get("创建时间") or "", r.get("会话ID") or ""))
    plan = []
    for i, r in enumerate(rows, 1):
        old_base = r.get(storage.FILE_COL, "") or ""
        new_base = f"{i:03d}_{_suffix(old_base)}" if old_base else ""
        plan.append((r, old_base, new_base, i))
    return csv_path, fields, plan


def _renames(acc_dir, plan):
    """所有有文件的行的 (old_path, new_path) 列表。

    纳入全部（含 old==new）而非仅序号变化的行：两阶段重命名时先把所有现存文件
    改成临时名，再归位，最终目标名一定是空的——可彻底避免「同名标题导致 A 的新名
    撞上 B 的旧名」这类覆盖。"""
    jdir = os.path.join(acc_dir, "json")
    mdir = os.path.join(acc_dir, "markdown")
    idir = os.path.join(acc_dir, "images")
    out = []
    for r, old_base, new_base, idx in plan:
        if not old_base:
            continue
        for d, ext in ((jdir, ".json"), (mdir, ".md")):
            o = os.path.join(d, old_base + ext)
            if os.path.exists(o):
                out.append((o, os.path.join(d, new_base + ext)))
        oi = os.path.join(idir, old_base)
        if os.path.isdir(oi):
            out.append((oi, os.path.join(idir, new_base)))
    return out


def _apply_renames(renames):
    """两阶段：先全部 old -> old+TMP，再 old+TMP -> new。
    归位前检查最终目标不存在（此时所有受管文件都是临时名，目标若存在必是外部残留）。"""
    for o, _ in renames:
        os.rename(o, o + TMP)
    for o, n in renames:
        if os.path.exists(n):
            raise RuntimeError(f"目标已存在（疑似外部残留文件），拒绝覆盖: {n}")
        os.rename(o + TMP, n)


def _write_csv(csv_path, fields, plan):
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r, old_base, new_base, idx in plan:
            r["序号"] = idx
            if old_base:
                r[storage.FILE_COL] = new_base
            w.writerow(r)
    os.replace(tmp, csv_path)


def _do_account(platform, account, acc_dir, apply):
    res = _plan_account(acc_dir)
    if not res:
        return
    csv_path, fields, plan = res
    renames = _renames(acc_dir, plan)
    changed = [(r, ob, nb, i) for (r, ob, nb, i) in plan if ob and ob != nb]
    print(f"\n■ {platform} / {account}: {len(plan)} 个会话，序号变化 {len(changed)} 个，文件改名 {len(renames)} 项")
    for r, ob, nb, i in changed[:5]:
        print(f"    {ob}  →  {nb}")
    if len(changed) > 5:
        print(f"    … 其余 {len(changed) - 5} 个")
    if apply:
        _apply_renames(renames)
        _write_csv(csv_path, fields, plan)
        print("    ✓ 已应用")
    else:
        print("    (预览，未改动；加 --apply 执行)")


def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv[1:]
    only_platform = args[0] if len(args) > 0 else None
    only_account = args[1] if len(args) > 1 else None
    for platform in PLATFORMS:
        if only_platform and platform != only_platform:
            continue
        pdir = os.path.join(storage.OUT_ROOT, platform)
        if not os.path.isdir(pdir):
            continue
        for account in sorted(os.listdir(pdir)):
            if only_account and account != only_account:
                continue
            acc_dir = os.path.join(pdir, account)
            if os.path.isdir(acc_dir):
                _do_account(platform, account, acc_dir, apply)
    if not apply:
        print("\n以上为预览。确认无误后加 --apply 执行。")


if __name__ == "__main__":
    main()
