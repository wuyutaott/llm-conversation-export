"""存储层：统一输出路径 out/{平台}/{账号}/，以及 titles.csv 读写。"""
import csv
import os

ROOT = "/Users/stone/Documents/wuyutaott.com/memory-exportor"
OUT_ROOT = os.path.join(ROOT, "out")

STATUS_COL = "状态"
FILE_COL = "文件"
CSV_HEADER = ["序号", "标题", "会话ID", "创建时间", "更新时间"]


def paths(platform, account):
    base = os.path.join(OUT_ROOT, platform, account)
    return {
        "base": base,
        "csv": os.path.join(base, "titles.csv"),
        "json": os.path.join(base, "json"),
        "md": os.path.join(base, "markdown"),
        "img": os.path.join(base, "images"),
    }


def ensure_dirs(p):
    for d in (p["json"], p["md"], p["img"]):
        os.makedirs(d, exist_ok=True)


def write_titles(csv_path, items):
    """items: [{'id','title','create_time','update_time'}]，写成 titles.csv。
    若已存在 titles.csv，按「会话ID」保留原有「状态/文件」标记（刷新清单不丢进度）。"""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    prev = {}
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                prev[r.get("会话ID")] = (r.get(STATUS_COL, ""), r.get(FILE_COL, ""))
    fields = CSV_HEADER + [STATUS_COL, FILE_COL]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for i, c in enumerate(items, 1):
            status, fname = prev.get(c.get("id"), ("", ""))
            w.writerow([i, c.get("title") or "(无标题)", c.get("id"),
                        c.get("create_time", ""), c.get("update_time", ""), status, fname])


def load_rows(csv_path):
    """读 titles.csv，确保含「状态」「文件」列。返回 (fieldnames, rows)。"""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames)
        rows = list(reader)
    for col in (STATUS_COL, FILE_COL):
        if col not in fields:
            fields.append(col)
            for r in rows:
                r[col] = ""
    return fields, rows


def save_rows(csv_path, fields, rows):
    """原子写回，写一半断电也不会损坏原文件。"""
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, csv_path)
