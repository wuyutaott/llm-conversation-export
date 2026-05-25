"""存储层：统一输出路径 out/{平台}/{账号}/，titles.csv 读写，以及事件日志。"""
import csv
import os
from datetime import datetime

ROOT = "/Users/stone/Documents/wuyutaott.com/memory-exportor"
OUT_ROOT = os.path.join(ROOT, "out")

STATUS_COL = "状态"
FILE_COL = "文件"
DURATION_COL = "耗时(秒)"
CSV_HEADER = ["序号", "标题", "会话ID", "创建时间", "更新时间"]
EXTRA_COLS = [STATUS_COL, FILE_COL, DURATION_COL]


def paths(platform, account):
    base = os.path.join(OUT_ROOT, platform, account)
    return {
        "base": base,
        "csv": os.path.join(base, "titles.csv"),
        "json": os.path.join(base, "json"),
        "md": os.path.join(base, "markdown"),
        "img": os.path.join(base, "images"),
        "log": os.path.join(base, "export.log"),
    }


def log_event(path, event, msg=""):
    """追加一条结构化日志：时间\t事件\t说明。用于分析限流策略、调 CD。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{event}\t{msg}\n")


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
                prev[r.get("会话ID")] = (r.get(STATUS_COL, ""), r.get(FILE_COL, ""), r.get(DURATION_COL, ""))
    fields = CSV_HEADER + EXTRA_COLS
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for i, c in enumerate(items, 1):
            status, fname, dur = prev.get(c.get("id"), ("", "", ""))
            w.writerow([i, c.get("title") or "(无标题)", c.get("id"),
                        c.get("create_time", ""), c.get("update_time", ""), status, fname, dur])


def load_rows(csv_path):
    """读 titles.csv，确保含「状态」「文件」列。返回 (fieldnames, rows)。"""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames)
        rows = list(reader)
    for col in EXTRA_COLS:
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
