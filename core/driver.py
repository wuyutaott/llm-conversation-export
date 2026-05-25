"""主循环：把通用编排（站点切换、账号校验、清单生成、断点续传、下载、渲染、进度、熔断）
集中在这里。各平台差异由 adapter 提供，adapter 接口见 adapters/ 下的实现。

Adapter 约定：
  属性: name, domain, home_url, gap, img_gap
  prepare() -> account_id(str)                       # 切站点后做认证，返回当前登录账号标识
  list_conversations() -> [{id,title,create_time,update_time}]
  fetch_conversation(cid) -> conv(dict, 可 JSON 序列化)
  collect_assets(conv) -> [{key,name,mime,is_image}]
  download_asset(desc) -> (bytes, name, mime)
  render_markdown(conv, title, key2rel) -> str       # key2rel: {key:{rel,is_image,name}}
"""
import json
import os
import random
import time

from core import browser, storage, util

CONSEC_FAIL_LIMIT = 3            # 连续失败达此数就进入冷却重试
COOLDOWN_SCHEDULE = [120, 300, 600]   # 自适应冷却梯度：2分 → 5分 → 10分（封顶 10 分）
# 自适应规则：一次冷却换来的成功数（since_ok）太少→升级冷却；很多→降级。
# 实测 ChatGPT 在 2 分钟冷却后只放行 2~3 个，故 SMALL_BATCH 设为 5，会快速升到 10 分钟并保持。
SMALL_BATCH = 5     # 冷却后成功 <= 此数 → CD 不够，升一级
GOOD_BATCH = 15     # 冷却后成功 >= 此数 → CD 充裕，降一级


def _cooldown(seconds, reason="连续失败"):
    """单行倒计时等待。Ctrl-C 可中止整个导出。"""
    print(f"\n⏳ {reason}，冷却 {seconds // 60} 分钟后重试（Ctrl-C 可手动结束）")
    for rem in range(seconds, 0, -1):
        m, s = divmod(rem, 60)
        print(f"\r   重试CD: {m}:{s:02d}    ", end="", flush=True)
        time.sleep(1)
    print("\r   重试CD: 0:00 —— 重新尝试            ")


def _fmt(sec):
    """秒 → 可读时长。"""
    sec = int(round(sec))
    if sec >= 3600:
        return f"{sec // 3600}时{sec % 3600 // 60}分{sec % 60}秒"
    if sec >= 60:
        return f"{sec // 60}分{sec % 60}秒"
    return f"{sec}秒"


def _build_titles(adapter, p):
    existed = os.path.exists(p["csv"])
    known = set()
    old_n = 0
    if existed:
        try:
            _, old_rows = storage.load_rows(p["csv"])
            old_n = len(old_rows)
            known = {r.get("会话ID") for r in old_rows if r.get("会话ID")}
        except Exception:
            pass
        print("→ 同步会话清单（从最新拉取，扫到整页都已在清单就停，增量合并新会话）...")
    else:
        print("→ 未发现 titles.csv，先拉取会话列表 ...")
    # 已有清单时把已知 ID 传给 adapter，使其拉到「整页都已知」即可提前停止，不必每次拉全量
    items = adapter.list_conversations(known_ids=known or None)
    storage.write_titles(p["csv"], items)
    _, rows = storage.load_rows(p["csv"])
    if existed:
        print(f"✓ 清单已同步: 共 {len(rows)} 个（新增 {max(0, len(rows) - old_n)} 个）\n")
    else:
        print(f"✓ 标题清单已生成: {len(rows)} 个\n")


def _process_assets(adapter, conv, p, base):
    """下载该会话所有资源，返回 {key:{rel,is_image,name}}。已存在的跳过。"""
    descs = adapter.collect_assets(conv)
    if not descs:
        return {}
    sub = os.path.join(p["img"], base)
    os.makedirs(sub, exist_ok=True)
    key2rel = {}
    n_img = sum(1 for d in descs if d.get("is_image"))
    print(f"    资源 {len(descs)} 个（图片 {n_img}）")
    for desc in descs:
        key = desc["key"]
        existing = [x for x in os.listdir(sub) if x.startswith(key + ".")]
        if existing:
            key2rel[key] = {"rel": f"../images/{base}/{existing[0]}",
                            "is_image": desc.get("is_image", False), "name": desc.get("name", "")}
            continue
        try:
            data, name, mime = adapter.download_asset(desc)
        except Exception as e:
            print(f"      ⚠ 资源 {key} 失败: {e}")
            continue
        local = key + util.ext_for(name or desc.get("name"), mime or desc.get("mime", ""))
        with open(os.path.join(sub, local), "wb") as f:
            f.write(data)
        key2rel[key] = {"rel": f"../images/{base}/{local}",
                        "is_image": desc.get("is_image", False), "name": name or desc.get("name", "")}
        print(f"      ✓ {local} ({len(data)} bytes)")
        time.sleep(adapter.img_gap)
    return key2rel


def run(adapter, account_override=None, mode="fetch"):
    """mode: 'fetch'=抓正文+图片（缺清单先拉）；'sync'=先增量刷新清单再抓（断点续传）；'list'=只刷新会话清单。"""
    browser.ensure_origin(adapter.domain, adapter.home_url)
    account = adapter.prepare()
    if account_override and account_override != account:
        raise SystemExit(
            f"✗ 你指定账号 {account_override}，但当前登录的是 {account}。\n"
            f"  只能导出当前登录账号。切换账号后重试，或去掉账号参数。")
    acct_dir = util.safe_account(account)
    p = storage.paths(adapter.name, acct_dir)
    storage.ensure_dirs(p)
    print(f"→ 平台: {adapter.name} | 账号: {account}")
    print(f"→ 导出目录: {p['base']}")

    if mode == "list":
        _build_titles(adapter, p)
        return
    if mode == "sync" or not os.path.exists(p["csv"]):
        _build_titles(adapter, p)

    fields, rows = storage.load_rows(p["csv"])
    total = len(rows)
    done0 = sum(1 for r in rows if r.get(storage.STATUS_COL) == "完成")
    print(f"→ 共 {total} 个会话，已完成 {done0}，待抓 {total - done0}")

    logp = p["log"]
    storage.log_event(logp, "SESSION", f"开始 平台={adapter.name} 账号={account} 共={total} 已完成={done0}")

    session_start = time.time()
    durations = []
    consec_fail = 0       # 连续失败计数（冷却后重置）
    cooldown_level = 0    # 当前冷却级别（不再因零星成功归零，由命中限流时按批量自适应调整）
    interrupted = False
    since_ok = 0          # 自上次冷却以来成功导出的会话数
    last_limit_ts = None  # 上次命中限流的时间戳

    def _bump_level():
        nonlocal cooldown_level
        cooldown_level = min(cooldown_level + 1, len(COOLDOWN_SCHEDULE) - 1)

    def _cool(reason):
        """按当前 cooldown_level 冷却并记录日志。级别由调用方调整。"""
        nonlocal consec_fail
        consec_fail = 0
        wait = COOLDOWN_SCHEDULE[min(cooldown_level, len(COOLDOWN_SCHEDULE) - 1)]
        storage.log_event(logp, "COOLDOWN", f"等待={wait}s({_fmt(wait)}) 级别={cooldown_level + 1} 原因={reason}")
        _cooldown(wait, reason)
        storage.log_event(logp, "RESUME", "冷却结束，重新尝试")

    try:
        # 外层：反复扫未完成的会话，直到全部完成（失败的会被下一轮重试）
        while True:
            todo = [r for r in rows if r.get(storage.STATUS_COL) != "完成"]
            if not todo:
                break
            progressed = False   # 本轮是否有成功
            cooled = False        # 本轮是否已触发冷却
            for row in todo:
                idx = row.get("序号", "")
                cid = row.get("会话ID", "")
                title = row.get("标题", "") or "(无标题)"
                done = sum(1 for r in rows if r.get(storage.STATUS_COL) == "完成")
                print(f"[{done + 1}/{total}] #{idx} {title}")
                storage.log_event(logp, "START", f"#{idx} {title}")
                # 内层：限流就原地长冷却后重试同一会话，直到成功或遇到非限流错误
                while True:
                    t0 = time.time()   # 每次尝试重新计时，CSV 耗时不含冷却等待
                    try:
                        conv = adapter.fetch_conversation(cid)
                        base = f"{str(idx).zfill(3)}_{util.safe_name(title, cid)}"
                        with open(os.path.join(p["json"], base + ".json"), "w", encoding="utf-8") as f:
                            json.dump(conv, f, ensure_ascii=False, indent=2)
                        key2rel = _process_assets(adapter, conv, p, base)
                        with open(os.path.join(p["md"], base + ".md"), "w", encoding="utf-8") as f:
                            f.write(adapter.render_markdown(conv, title, key2rel))
                    except browser.RateLimited:
                        # 命中限流：先记录「上次冷却换来几个成功、间隔多久」——分析+自适应的关键数据
                        now = time.time()
                        gap_info = f" 距上次限流={_fmt(now - last_limit_ts)}" if last_limit_ts else " (本次首个限流)"
                        # 自适应：上次冷却放行太少→升级 CD；很多→降级；中间保持。不再因零星成功归零。
                        if since_ok <= SMALL_BATCH:
                            _bump_level()
                            adj = "↑升级"
                        elif since_ok >= GOOD_BATCH:
                            cooldown_level = max(cooldown_level - 1, 0)
                            adj = "↓降级"
                        else:
                            adj = "保持"
                        print(f"    🚫 命中限流：上次冷却放行 {since_ok} 个{gap_info}，CD {adj}")
                        storage.log_event(logp, "LIMIT",
                                          f"#{idx} 上次冷却放行={since_ok}个{gap_info} CD{adj}→级别{cooldown_level + 1}")
                        last_limit_ts = now
                        since_ok = 0
                        _cool("遇到限流")
                        continue
                    except Exception as e:
                        dt = time.time() - t0
                        print(f"    ⚠ 失败（用时 {dt:.1f}s）: {e}")
                        storage.log_event(logp, "FAIL", f"#{idx} {e}")
                        row[storage.STATUS_COL] = "失败"
                        storage.save_rows(p["csv"], fields, rows)
                        consec_fail += 1
                        if consec_fail >= CONSEC_FAIL_LIMIT:
                            _bump_level()
                            _cool(f"连续失败 {CONSEC_FAIL_LIMIT} 次（掉登录/断网等）")
                            cooled = True
                        break   # 非限流错误：结束本会话
                    else:
                        dt = time.time() - t0
                        consec_fail = 0
                        since_ok += 1
                        row[storage.STATUS_COL] = "完成"
                        row[storage.FILE_COL] = base
                        row[storage.DURATION_COL] = f"{dt:.1f}"   # 该会话导出耗时记入 CSV
                        storage.save_rows(p["csv"], fields, rows)
                        durations.append(dt)
                        progressed = True
                        print(f"    ✓ {base}（{len(key2rel)} 资源，用时 {dt:.1f}s）")
                        storage.log_event(logp, "OK", f"#{idx} 用时={dt:.1f}s 资源={len(key2rel)} 自上次限流第{since_ok}个")
                        break   # 成功：结束本会话
                if cooled:
                    break          # 非限流连续失败已冷却，跳出本轮，外层重扫
                # 会话之间随机停顿，模拟真人节奏、降低触发突发检测的概率
                delay = random.uniform(adapter.gap_min, adapter.gap_max)
                print(f"    休息 {delay:.1f}s")
                time.sleep(delay)

            if cooled:
                continue                      # 已冷却，外层重扫重试
            if not progressed:
                _bump_level()
                _cool("本轮无任何成功")  # 剩余都失败但没触发连续阈值，也冷却避免空转
            # 有成功但仍有失败项：外层 while 再过一轮重试它们
    except KeyboardInterrupt:
        interrupted = True
        print("\n⏹ 已手动停止（Ctrl-C）。进度已保存，重跑会从断点继续。")

    done = sum(1 for r in rows if r.get(storage.STATUS_COL) == "完成")
    failed = sum(1 for r in rows if r.get(storage.STATUS_COL) == "失败")
    elapsed = time.time() - session_start
    net = sum(durations)
    total_recorded = 0.0
    for r in rows:
        try:
            total_recorded += float(r.get(storage.DURATION_COL) or 0)
        except ValueError:
            pass
    head = "已手动停止" if interrupted else ("全部完成 🎉" if done == total else "本次结束")
    print(f"\n==== {head} ====")
    print(f"  完成 {done}/{total}，失败 {failed}")
    print(f"  本次处理 {len(durations)} 个会话")
    print(f"  本次净抓取耗时 {_fmt(net)}（各会话用时之和），墙钟 {_fmt(elapsed)}（含等待退避）")
    if len(durations):
        print(f"  本次平均每会话 {net/len(durations):.1f}s")
    print(f"  全部已完成会话累计抓取耗时 {_fmt(total_recorded)}（来自 CSV 耗时列）")
    print(f"  日志: {logp}")
    storage.log_event(logp, "SESSION", f"结束 完成={done}/{total} 失败={failed} "
                      f"本次={len(durations)}个 墙钟={_fmt(elapsed)} {'(手动停止)' if interrupted else ''}")
