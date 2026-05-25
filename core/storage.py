"""存储层：统一输出路径 out/{平台}/{账号}/，titles.csv 读写，以及事件日志。"""
import csv
import os
from datetime import datetime

# ROOT 优先取入口（run.py / run.sh）注入的 EXPORT_ROOT；直接 import 时回退到本文件所在仓库根。
# 不再硬编码绝对路径，换机器 / 换平台（含 Windows）都能正确定位 out/ 目录。
ROOT = os.environ.get("EXPORT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

    序号规则：越新（创建越晚）的会话序号越大，便于增量导出——新会话只在末尾追加
    更大的序号，已有会话的序号永久固定不变（即使被重新激活、update_time 变化也不动）。

    若已存在 titles.csv：按「会话ID」保留原有「序号/状态/文件」（刷新清单不丢进度、不重排）；
    新出现的会话按「创建时间」升序接在当前最大序号之后。"""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    prev = {}          # 会话ID -> (序号, 状态, 文件, 耗时)
    max_seq = 0
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                seq = r.get("序号", "")
                prev[r.get("会话ID")] = (seq, r.get(STATUS_COL, ""), r.get(FILE_COL, ""), r.get(DURATION_COL, ""))
                if str(seq).isdigit():
                    max_seq = max(max_seq, int(seq))

    seq_of = {}
    for c in items:
        cid = c.get("id")
        old = prev.get(cid)
        if old and str(old[0]).isdigit():
            seq_of[cid] = int(old[0])
    # 新会话：按创建时间升序（越新排越后），依次分配 max_seq+1, +2, ...
    new = [c for c in items if c.get("id") not in seq_of]
    new.sort(key=lambda c: (c.get("create_time") or "", c.get("id") or ""))
    for c in new:
        max_seq += 1
        seq_of[c.get("id")] = max_seq

    rows = []
    for c in items:
        cid = c.get("id")
        _, status, fname, dur = prev.get(cid, ("", "", "", ""))
        rows.append((seq_of[cid], c, status, fname, dur))
    rows.sort(key=lambda x: x[0])   # 按序号升序输出

    fields = CSV_HEADER + EXTRA_COLS
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for seq, c, status, fname, dur in rows:
            w.writerow([seq, c.get("title") or "(无标题)", c.get("id"),
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
