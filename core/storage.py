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
    """把会话列表 items 增量合并进 titles.csv（不整表重写）。

    items: [{'id','title','create_time','update_time'}]。

    合并规则：
    - 已存在的会话：原样保留该行（序号/状态/文件/耗时都不动，刷新清单不丢进度、不重排）。
    - 新会话（csv 里没有的）：按「创建时间」升序追加，分配「当前最大序号+1」起的更大序号。
    - csv 里有、但本次 items 未包含的会话：原样保留——这样允许「增量刷新只拉最新几页」
      （adapter.list_conversations 提前停止）而不会丢掉没拉到的历史行。

    序号规则：越新（创建越晚）序号越大、最旧=001；已分配的序号永久固定（即使会话被重新
    激活、update_time 变化也不动）。"""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    fields = CSV_HEADER + EXTRA_COLS
    existing = {}      # 会话ID -> 原行 dict（保留全部字段）
    max_seq = 0
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                existing[r.get("会话ID")] = r
                seq = r.get("序号", "")
                if str(seq).isdigit():
                    max_seq = max(max_seq, int(seq))

    rows = list(existing.values())                       # 全部已有行原样保留
    new = [c for c in items if c.get("id") not in existing]
    new.sort(key=lambda c: (c.get("create_time") or "", c.get("id") or ""))
    for c in new:                                        # 新会话按创建时间升序追加更大序号
        max_seq += 1
        rows.append({
            "序号": max_seq, "标题": c.get("title") or "(无标题)", "会话ID": c.get("id"),
            "创建时间": c.get("create_time", ""), "更新时间": c.get("update_time", ""),
            STATUS_COL: "", FILE_COL: "", DURATION_COL: "",
        })

    def _seq(r):
        s = r.get("序号", "")
        return int(s) if str(s).isdigit() else 10 ** 9
    rows.sort(key=_seq)                                  # 按序号升序输出

    tmp = csv_path + ".tmp"                               # 原子写，写一半断电不损坏原文件
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            for k in fields:
                r.setdefault(k, "")
            w.writerow(r)
    os.replace(tmp, csv_path)


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
