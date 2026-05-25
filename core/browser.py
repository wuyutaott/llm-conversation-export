"""浏览器交互层：所有请求都通过 browser-harness 注入的 js() 在页面内 fetch 完成。

browser-harness 把 js / page_info / new_tab / wait_for_load 注入到 exec 的全局命名空间，
import 进来的模块拿不到，所以入口脚本须先调用 bind(...) 把这些 helper 传进来。
"""
import base64
import json

_H = {}   # 绑定的 browser-harness helper


class RateLimited(RuntimeError):
    """命中 429 限流。底层不再小退避，直接抛出，由上层 driver 做长冷却重试。"""


def bind(**helpers):
    """由入口脚本注入 js/page_info/new_tab/wait_for_load。"""
    _H.update(helpers)


def _js(expr):
    return _H["js"](expr)


def run_js(expr):
    """在页面内执行任意 JS 并返回结果（供 adapter 做 DOM 抓取等）。"""
    return _js(expr)


def ensure_origin(domain, home_url):
    """确保当前标签页在目标站点，否则相对路径/cookie 请求会发到别的站点。"""
    try:
        cur = _H["page_info"]().get("url", "") or ""
    except Exception:
        cur = ""
    if domain not in cur:
        print(f"→ 当前标签页不在 {domain}，正在打开 ...")
        _H["new_tab"](home_url)
        _H["wait_for_load"]()


def cookie(name):
    """读取 document.cookie 中某个 cookie 值。"""
    import re
    ck = _js("document.cookie") or ""
    m = re.search(r'(?:^|;\s*)' + re.escape(name) + r'=([^;]+)', ck)
    return m.group(1) if m else None


def _opts_js(method, headers, body, credentials):
    parts = [f"method:{json.dumps(method)}", f"headers:{json.dumps(headers or {})}"]
    if credentials:
        parts.append('credentials:"include"')
    body_line = ("o.body=JSON.stringify(%s);" % json.dumps(body)) if body is not None else ""
    return "{" + ",".join(parts) + "}", body_line


def fetch(url, method="GET", body=None, headers=None, credentials=False):
    """页面内 fetch，返回 (status, text)；遇 429 直接抛 RateLimited（由 driver 长冷却重试）。"""
    o_js, body_line = _opts_js(method, headers, body, credentials)
    expr = """
    (async () => {
      const o = %s; %s
      const r = await fetch(%s, o);
      const t = await r.text();
      return JSON.stringify({s: r.status, t: t});
    })()
    """ % (o_js, body_line, json.dumps(url))
    res = json.loads(_js(expr))
    if res["s"] == 429:
        raise RateLimited(f"429 限流: {url}")
    return res["s"], res["t"]


def fetch_json(url, method="GET", body=None, headers=None, credentials=False):
    """fetch 并要求 200 + JSON，否则抛错。"""
    s, t = fetch(url, method, body, headers, credentials)
    if s != 200:
        raise RuntimeError(f"HTTP {s}: {t[:200]}")
    return json.loads(t)


def download(url, headers=None, credentials=False):
    """下载二进制资源，返回 (bytes, content_type)；遇 429 直接抛 RateLimited。"""
    o_js, _ = _opts_js("GET", headers, None, credentials)
    expr = """
    (async () => { try {
      const r = await fetch(%s, %s);
      if (r.status !== 200) return JSON.stringify({s: r.status});
      const buf = await r.arrayBuffer(); let bin=""; const a=new Uint8Array(buf), C=0x8000;
      for (let i=0;i<a.length;i+=C) bin += String.fromCharCode.apply(null, a.subarray(i,i+C));
      return JSON.stringify({s:200, ct:r.headers.get("content-type")||"", b64:btoa(bin)});
    } catch(e){ return JSON.stringify({s:-1, err:String(e)}); } })()
    """ % (json.dumps(url), o_js)
    res = json.loads(_js(expr))
    if res["s"] == 200:
        return base64.b64decode(res["b64"]), res.get("ct", "")
    if res["s"] == 429:
        raise RateLimited(f"429 限流（下载）: {url}")
    raise RuntimeError(f"下载 HTTP {res['s']} {res.get('err','')}")
