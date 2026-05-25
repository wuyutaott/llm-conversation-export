"""Gemini adapter（gemini.google.com）。

Gemini 没有干净 REST，用 Google 的 batchexecute RPC + SPA DOM 抓取：
- 认证：Google cookie + 页面 WIZ_global_data 里的 at/sid/bl token
- 列表：RPC MaZiqc，首页 payload [60]，之后 [60, cursor] 分页
- 正文：正文 RPC 脱离 SPA 复现不出，改为在已热加载的 SPA 内用 history.pushState
  打开 /app/{id} 触发 Angular 路由，等渲染后抓 user-query / model-response 的 DOM 文本
- 账号：从页面 DOM 提取登录邮箱
- 图片：会话中的 googleusercontent 图（用户上传/生成），经 CDP loadNetworkResource 下载
  （带 cookie、绕开页面 CORS），并嵌入对应消息的 Markdown
"""
import hashlib
import json
import re
import time
import urllib.parse

from core import browser, util

RPC_LIST = "MaZiqc"
PAGE = 60
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

_SCRAPE_JS = r'''
(() => {
  const turns = [];
  // 只收用户内容图 / 生成图（googleusercontent），排除图标等
  const imgsOf = el => [...el.querySelectorAll("img")].map(i => i.src)
      .filter(s => s && s.indexOf("googleusercontent.com") !== -1);
  document.querySelectorAll("user-query, model-response").forEach(el => {
    const tag = el.tagName.toLowerCase();
    if (tag === "user-query") {
      const q = el.querySelector(".query-text") || el;
      let t = (q.innerText || "").trim();
      t = t.replace(/^(你说|您说|You said|Du:|あなた)\s*\n+/, "").trim();  // 去掉 UI 的"你说"标签
      turns.push({role: "user", text: t, images: imgsOf(el)});
    } else {
      const m = el.querySelector(".markdown, .model-response-text") || el;
      turns.push({role: "model", text: (m.innerText || "").trim(), images: imgsOf(el)});
    }
  });
  return JSON.stringify(turns);
})()
'''


def _sniff_mime(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


class GeminiAdapter:
    name = "gemini"
    domain = "gemini.google.com"
    home_url = "https://gemini.google.com/app"
    gap_min = 1.0
    gap_max = 3.0
    img_gap = 0.5

    def __init__(self):
        self._wiz = None
        self._email = None

    # ---- WIZ token ----
    def _load_wiz(self):
        raw = browser.run_js(
            'JSON.stringify({at:(window.WIZ_global_data||{})["SNlM0e"]||"",'
            ' sid:(window.WIZ_global_data||{})["FdrFJe"]||"",'
            ' bl:(window.WIZ_global_data||{})["cfb2h"]||""})')
        self._wiz = json.loads(raw)
        if not self._wiz.get("at"):
            raise RuntimeError("拿不到 Gemini 的 WIZ token，请确认已登录 gemini.google.com")
        return self._wiz

    # ---- batchexecute ----
    def _parse_be(self, text, rpcid):
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("[["):
                continue
            try:
                arr = json.loads(line)
            except ValueError:
                continue
            for entry in arr:
                if isinstance(entry, list) and len(entry) >= 3 and entry[0] == "wrb.fr" and entry[1] == rpcid:
                    return json.loads(entry[2]) if entry[2] else None
        return None

    def _rpc(self, rpcid, inner):
        if not self._wiz:
            self._load_wiz()
        freq = json.dumps([[[rpcid, json.dumps(inner, separators=(",", ":")), None, "generic"]]],
                          separators=(",", ":"))
        body = "f.req=" + urllib.parse.quote(freq, safe="") + "&at=" + urllib.parse.quote(self._wiz["at"], safe="")
        qs = (f"rpcids={rpcid}&source-path=%2Fapp&bl={urllib.parse.quote(self._wiz['bl'])}"
              f"&f.sid={urllib.parse.quote(self._wiz['sid'])}&hl=en&_reqid={int(time.time() * 1000) % 1000000}&rt=c")
        status, text = browser.fetch("/_/BardChatUi/data/batchexecute?" + qs, "POST", body=body,
                                     headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"})
        if status != 200:
            raise RuntimeError(f"batchexecute HTTP {status}")
        return self._parse_be(text, rpcid)

    # ---- 账号 ----
    def _detect_email(self):
        for _ in range(8):
            html = browser.run_js("document.documentElement.innerHTML") or ""
            m = EMAIL_RE.search(html)
            if m:
                return m.group(0)
            time.sleep(1)
        return None

    def prepare(self):
        time.sleep(3)              # 等 SPA 热加载，后续 pushState 路由才生效
        self._load_wiz()
        self._email = self._detect_email()
        return self._email or "gemini-account"

    # ---- 列表 ----
    def list_conversations(self):
        items, seen, cursor = [], set(), None
        while True:
            inner = [PAGE] if cursor is None else [PAGE, cursor]
            data = self._rpc(RPC_LIST, inner)
            if not data:
                break
            cursor = data[1] if len(data) > 1 else None
            convs = data[2] if len(data) > 2 and data[2] else []
            new = 0
            for c in convs:
                cid = c[0] if c else None
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                ts = ""
                if len(c) > 5 and isinstance(c[5], list) and c[5]:
                    ts = util.ts(c[5][0])
                items.append({"id": cid, "title": (c[1] if len(c) > 1 else "") or "(无标题)",
                              "create_time": ts, "update_time": ""})
                new += 1
            print(f"  已取 {len(items)}")
            if not convs or not new or not cursor:
                break
        return items

    # ---- 正文（SPA 导航 + DOM 抓取）----
    @staticmethod
    def _count():
        return int(browser.run_js('document.querySelectorAll("user-query, model-response").length') or 0)

    @staticmethod
    def _nav(path):
        browser.run_js("(()=>{history.pushState(null,'','%s');"
                       "window.dispatchEvent(new PopStateEvent('popstate'));return 1;})()" % path)

    def fetch_conversation(self, cid):
        short = cid[2:] if cid.startswith("c_") else cid
        # 同路由换参数 pushState 不触发 Angular 重渲染，须先整页重载回 /app 再 pushState 进会话
        browser.navigate(self.home_url, wait=4)
        self._nav("/app/" + short)
        turns = self._wait_and_scrape()
        if not turns:
            raise RuntimeError("会话未渲染出消息（可能加载超时或会话为空）")
        return {"id": cid, "turns": turns}

    def _wait_and_scrape(self):
        prev, stable = -1, 0
        for _ in range(60):   # 最多约 30s 等渲染稳定
            n = self._count()
            if n > 0 and n == prev:
                stable += 1
            else:
                stable = 0
            prev = n
            if stable >= 4:
                break
            time.sleep(0.5)
        return json.loads(browser.run_js(_SCRAPE_JS) or "[]")

    # ---- 资源（会话图片，经 CDP 下载绕开 CORS）----
    @staticmethod
    def _img_key(url):
        return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]

    def collect_assets(self, conv):
        out, seen = [], set()
        for t in conv.get("turns", []):
            for url in t.get("images", []) or []:
                k = self._img_key(url)
                if k in seen:
                    continue
                seen.add(k)
                out.append({"key": k, "url": url, "name": "", "mime": "", "is_image": True})
        return out

    def download_asset(self, desc):
        # 页面 img 的 src 是缩略图（且原 src 可能 403）；去掉尺寸参数加 =s0 取原图
        url = desc["url"].split("=")[0] + "=s0"
        data = browser.download_cdp(url)
        return data, desc.get("name") or desc["key"], _sniff_mime(data)

    # ---- 渲染 ----
    def render_markdown(self, conv, title, key2rel):
        lines = [f"# {title}", "",
                 f"- 会话 ID: {conv.get('id', '')}",
                 f"- 导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 "", "---", ""]
        label = {"user": "🧑 用户", "model": "🤖 Gemini"}
        for t in conv.get("turns", []):
            text = (t.get("text") or "").strip()
            imgs = []
            for url in t.get("images", []) or []:
                info = key2rel.get(self._img_key(url))
                if info:
                    imgs.append(f"![image]({info['rel']})")
            body = "\n\n".join(x for x in [text] + imgs if x).strip()
            if not body:
                continue
            lines += [f"## {label.get(t['role'], t['role'])}", "", body, ""]
        return "\n".join(lines)


adapter = GeminiAdapter()
