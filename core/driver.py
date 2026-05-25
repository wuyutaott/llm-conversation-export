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
import time

from core import browser, storage, util

CONSEC_FAIL_LIMIT = 3            # 连续失败达此数就进入冷却重试
COOLDOWN_SCHEDULE = [120, 300, 600]   # 升级式冷却：2分 → 5分 → 10分（封顶 10 分）


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
    print("→ 未发现 titles.csv，先拉取会话列表 ...")
    items = adapter.list_conversations()
    storage.write_titles(p["csv"], items)
    print(f"✓ 标题清单已生成: {len(items)} 个\n")


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
    """mode: 'fetch'=抓正文+图片（缺清单先拉）；'list'=只刷新会话清单。"""
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
    if not os.path.exists(p["csv"]):
        _build_titles(adapter, p)

    fields, rows = storage.load_rows(p["csv"])
    total = len(rows)
    done0 = sum(1 for r in rows if r.get(storage.STATUS_COL) == "完成")
    print(f"→ 共 {total} 个会话，已完成 {done0}，待抓 {total - done0}")

    logp = p["log"]
    storage.log_event(logp, "SESSION", f"开始 平台={adapter.name} 账号={account} 共={total} 已完成={done0}")

    session_start = time.time()
    durations = []
    consec_fail = 0       # 连续失败计数（成功或冷却后重置）
    cooldown_level = 0    # 冷却升级级别（成功则归零）
    interrupted = False
    since_ok = 0          # 自上次限流以来成功导出的会话数
    last_limit_ts = None  # 上次命中限流的时间戳

    def _do_cooldown(reason):
        nonlocal cooldown_level, consec_fail
        wait = COOLDOWN_SCHEDULE[min(cooldown_level, len(COOLDOWN_SCHEDULE) - 1)]
        cooldown_level += 1
        consec_fail = 0
        storage.log_event(logp, "COOLDOWN", f"等待={wait}s({_fmt(wait)}) 级别={cooldown_level} 原因={reason}")
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
                        # 命中限流：记录「自上次限流以来成功几个、间隔多久」——分析限流策略的关键数据
                        now = time.time()
                        gap_info = f" 距上次限流={_fmt(now - last_limit_ts)}" if last_limit_ts else " (本次首个限流)"
                        print(f"    🚫 命中限流：自上次以来成功 {since_ok} 个{gap_info}")
                        storage.log_event(logp, "LIMIT", f"#{idx} 自上次限流成功={since_ok}个{gap_info}")
                        last_limit_ts = now
                        since_ok = 0
                        _do_cooldown("遇到限流")
                        continue
                    except Exception as e:
                        dt = time.time() - t0
                        print(f"    ⚠ 失败（用时 {dt:.1f}s）: {e}")
                        storage.log_event(logp, "FAIL", f"#{idx} {e}")
                        row[storage.STATUS_COL] = "失败"
                        storage.save_rows(p["csv"], fields, rows)
                        consec_fail += 1
                        if consec_fail >= CONSEC_FAIL_LIMIT:
                            _do_cooldown(f"连续失败 {CONSEC_FAIL_LIMIT} 次（掉登录/断网等）")
                            cooled = True
                        break   # 非限流错误：结束本会话
                    else:
                        dt = time.time() - t0
                        consec_fail = 0
                        cooldown_level = 0   # 成功则重置冷却升级，下次限流仍从 2 分钟起
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
                time.sleep(adapter.gap)

            if cooled:
                continue                      # 已冷却，外层重扫重试
            if not progressed:
                _do_cooldown("本轮无任何成功")  # 剩余都失败但没触发连续阈值，也冷却避免空转
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
