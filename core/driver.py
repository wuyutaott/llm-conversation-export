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

CONSEC_FAIL_LIMIT = 3   # 连续失败达此数就熔断（多半掉登录/被限流/断网）


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
    todo = [r for r in rows if r.get(storage.STATUS_COL) != "完成"]
    start_done = total - len(todo)
    done = start_done
    print(f"→ 共 {total} 个会话，已完成 {done}，本次待抓 {len(todo)}")

    session_start = time.time()
    durations = []
    consec_fail = 0

    for i, row in enumerate(todo, 1):
        idx = row.get("序号", "")
        cid = row.get("会话ID", "")
        title = row.get("标题", "") or "(无标题)"
        t0 = time.time()
        print(f"[{start_done + i}/{total}] #{idx} {title}")
        try:
            conv = adapter.fetch_conversation(cid)
            base = f"{str(idx).zfill(3)}_{util.safe_name(title, cid)}"
            with open(os.path.join(p["json"], base + ".json"), "w", encoding="utf-8") as f:
                json.dump(conv, f, ensure_ascii=False, indent=2)
            key2rel = _process_assets(adapter, conv, p, base)
            with open(os.path.join(p["md"], base + ".md"), "w", encoding="utf-8") as f:
                f.write(adapter.render_markdown(conv, title, key2rel))
        except Exception as e:
            dt = time.time() - t0
            print(f"    ⚠ 失败（用时 {dt:.1f}s）: {e}")
            row[storage.STATUS_COL] = "失败"
            storage.save_rows(p["csv"], fields, rows)
            consec_fail += 1
            if consec_fail >= CONSEC_FAIL_LIMIT:
                print(f"\n⛔ 连续失败 {consec_fail} 次，已熔断停止——多半是掉登录/被限流/断网。")
                print("   请检查登录状态，稍后重跑会从断点继续（失败的会重试）。")
                break
            time.sleep(adapter.gap)
            continue

        consec_fail = 0
        row[storage.STATUS_COL] = "完成"
        row[storage.FILE_COL] = base
        storage.save_rows(p["csv"], fields, rows)

        dt = time.time() - t0
        durations.append(dt)
        done += 1
        pct = done / total * 100
        filled = int(24 * done / total)
        bar = "█" * filled + "░" * (24 - filled)
        spent = time.time() - session_start
        spent_str = f"{int(spent // 60)}分{int(spent % 60)}秒" if spent >= 60 else f"{spent:.0f}秒"
        print(f"    ✓ {base}（{len(key2rel)} 资源，用时 {dt:.1f}s）")
        print(f"    进度 [{bar}] {done}/{total} {pct:.1f}%  本次已耗时 {spent_str}")
        time.sleep(adapter.gap)

    done = sum(1 for r in rows if r.get(storage.STATUS_COL) == "完成")
    failed = sum(1 for r in rows if r.get(storage.STATUS_COL) == "失败")
    elapsed = time.time() - session_start
    print(f"\n✓ 本次结束。累计完成 {done}/{total}，失败 {failed}，"
          f"本次处理 {len(durations)} 个，用时 {elapsed/60:.1f} 分")
