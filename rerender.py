#!/usr/bin/env python3
"""用本地已下载的 JSON 重新生成 markdown，不联网、不重爬。

适用场景：改了某个 adapter 的 render_markdown 渲染逻辑后，想让历史导出立即生效。
图片链接按导出时同样的规则（images/{base}/ 下以 {key}. 开头的文件）就地复用，不会退化成「未下载」。

用法：
  python3 rerender.py                # 重渲染 out/ 下所有平台、所有账号
  python3 rerender.py chatgpt        # 只重渲染指定平台
  python3 rerender.py chatgpt 邮箱   # 只重渲染指定平台 + 账号目录
"""
import importlib
import json
import os
import sys

from core import storage

PLATFORMS = ["chatgpt", "gemini", "grok"]


def _title_map(csv_path):
    """titles.csv 的「文件」列(base) → 「标题」，让重渲染标题与原导出一致。"""
    m = {}
    if os.path.exists(csv_path):
        _, rows = storage.load_rows(csv_path)
        for r in rows:
            base = r.get(storage.FILE_COL)
            if base:
                m[base] = r.get("标题") or ""
    return m


def _rebuild_key2rel(adapter, conv, img_dir, base):
    """复刻导出时「已存在则复用」逻辑：在 images/{base}/ 找以 {key}. 开头的文件。"""
    sub = os.path.join(img_dir, base)
    if not os.path.isdir(sub):
        return {}
    files = os.listdir(sub)
    key2rel = {}
    for d in adapter.collect_assets(conv):
        key = d["key"]
        match = [x for x in files if x.startswith(key + ".")]
        if match:
            key2rel[key] = {"rel": f"../images/{base}/{match[0]}",
                            "is_image": d.get("is_image", False), "name": d.get("name", "")}
    return key2rel


def _rerender_account(adapter, acc_dir):
    json_dir = os.path.join(acc_dir, "json")
    md_dir = os.path.join(acc_dir, "markdown")
    img_dir = os.path.join(acc_dir, "images")
    if not os.path.isdir(json_dir):
        return 0
    os.makedirs(md_dir, exist_ok=True)
    tmap = _title_map(os.path.join(acc_dir, "titles.csv"))
    n = 0
    for fn in sorted(os.listdir(json_dir)):
        if not fn.endswith(".json"):
            continue
        base = fn[:-5]
        with open(os.path.join(json_dir, fn), encoding="utf-8") as f:
            conv = json.load(f)
        title = tmap.get(base) or conv.get("title") or base
        key2rel = _rebuild_key2rel(adapter, conv, img_dir, base)
        with open(os.path.join(md_dir, base + ".md"), "w", encoding="utf-8") as f:
            f.write(adapter.render_markdown(conv, title, key2rel))
        n += 1
    return n


def main():
    only_platform = sys.argv[1] if len(sys.argv) > 1 else None
    only_account = sys.argv[2] if len(sys.argv) > 2 else None
    grand = 0
    for platform in PLATFORMS:
        if only_platform and platform != only_platform:
            continue
        pdir = os.path.join(storage.OUT_ROOT, platform)
        if not os.path.isdir(pdir):
            continue
        adapter = importlib.import_module(f"adapters.{platform}").adapter
        for account in sorted(os.listdir(pdir)):
            if only_account and account != only_account:
                continue
            acc_dir = os.path.join(pdir, account)
            if not os.path.isdir(acc_dir):
                continue
            n = _rerender_account(adapter, acc_dir)
            if n:
                print(f"✓ {platform} / {account}: 重渲染 {n} 个")
                grand += n
    print(f"\n==== 完成，共重渲染 {grand} 个 markdown ====")


if __name__ == "__main__":
    main()
