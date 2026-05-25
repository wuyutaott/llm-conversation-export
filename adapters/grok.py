"""Grok adapter。

认证：cookie（页面内 fetch 自动带上），账号用 x-userid cookie 标识。
详情：response-node（消息树）+ load-responses（正文）。
图片/文件：assets.grok.com/{fileUri}，需 credentials。
"""
import re
import time

from core import browser, util

ASSETS = "https://assets.grok.com/"
ID_CHUNK = 100   # load-responses 一次最多请求多少个 responseId
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


class GrokAdapter:
    name = "grok"
    domain = "grok.com"
    home_url = "https://grok.com/"
    gap_min = 1.0     # 会话之间随机停顿区间（秒）
    gap_max = 5.0
    img_gap = 0.5

    def __init__(self):
        self._userid = None
        self._email = None

    def _detect_email(self):
        """Grok 无邮箱接口，从侧边栏账号区的 DOM 文本里提取（等渲染后重试几次）。"""
        for _ in range(8):
            html = browser.run_js("document.documentElement.innerHTML") or ""
            m = EMAIL_RE.search(html)
            if m:
                return m.group(0)
            time.sleep(1)
        return None

    def prepare(self):
        self._userid = browser.cookie("x-userid")
        if not self._userid:
            raise RuntimeError("拿不到 x-userid cookie，请确认 Chrome 已登录 grok.com")
        self._email = self._detect_email()
        # 优先用邮箱作账号目录，取不到才回退到 x-userid
        return self._email or self._userid

    # ---- 列表 ----
    def list_conversations(self, known_ids=None):
        items, seen, token = [], set(), None
        while True:
            path = "/rest/app-chat/conversations" + (f"?pageToken={token}" if token else "")
            d = browser.fetch_json(path)
            batch = d.get("conversations", [])
            new = [c for c in batch if c.get("conversationId") not in seen]
            for c in new:
                seen.add(c.get("conversationId"))
            items.extend(new)
            print(f"  已取 {len(items)}")
            token = d.get("nextPageToken")
            if not batch or not new or not token:
                break
            # 增量提前停止：整页都在已有清单里 → 后面都是更早的会话，无新增
            if known_ids and all(c.get("conversationId") in known_ids for c in batch):
                print("  本页均已在清单中，提前停止")
                break
        return [{"id": c.get("conversationId"), "title": c.get("title"),
                 "create_time": util.ts(c.get("createTime")),
                 "update_time": util.ts(c.get("modifyTime"))} for c in items]

    # ---- 详情 ----
    def fetch_conversation(self, cid):
        rn = browser.fetch_json(f"/rest/app-chat/conversations/{cid}/response-node")
        nodes = rn.get("responseNodes", [])
        ids = [n["responseId"] for n in nodes if n.get("responseId")]
        by_id = {}
        for i in range(0, len(ids), ID_CHUNK):
            chunk = ids[i:i + ID_CHUNK]
            rr = browser.fetch_json(f"/rest/app-chat/conversations/{cid}/load-responses",
                                    "POST", {"responseIds": chunk})
            for resp in rr.get("responses", []):
                by_id[resp.get("responseId")] = resp
        ordered = [by_id[i] for i in ids if i in by_id]
        return {"conversationId": cid, "responseNodes": nodes, "responses": ordered}

    # ---- 资源 ----
    @staticmethod
    def _img_key(url):
        return url.rstrip("/").split("/")[-2] if "/" in url else url

    def collect_assets(self, conv):
        out, seen = [], set()

        def add(key, name, mime, is_img, url):
            if key and key not in seen:
                seen.add(key)
                out.append({"key": key, "name": name, "mime": mime, "is_image": is_img, "url": url})

        for resp in conv.get("responses", []):
            for meta in resp.get("fileAttachmentsMetadata", []) or []:
                uri = meta.get("fileUri")
                if not uri:
                    continue
                mime = meta.get("fileMimeType") or ""
                add(meta.get("fileMetadataId") or uri, meta.get("fileName") or "",
                    mime, mime in util.IMG_MIMES, ASSETS + uri.lstrip("/"))
            for u in resp.get("generatedImageUrls", []) or []:
                if not u:
                    continue
                url = u if u.startswith("http") else ASSETS + u.lstrip("/")
                add(self._img_key(u), "", "image/jpeg", True, url)
        return out

    def download_asset(self, desc):
        data, ct = browser.download(desc["url"], credentials=True)
        return data, desc.get("name") or desc["key"], desc.get("mime") or ct

    # ---- 渲染 ----
    def render_markdown(self, conv, title, key2rel):
        lines = [f"# {title}", "",
                 f"- 会话 ID: {conv.get('conversationId', '')}",
                 "", "---", ""]
        label = {"human": "🧑 用户", "assistant": "🤖 Grok"}
        for resp in conv.get("responses", []):
            sender = (resp.get("sender") or "").lower()
            out = []
            text = (resp.get("message") or "").strip()
            if text:
                out.append(text)
            keys = [m.get("fileMetadataId") or m.get("fileUri")
                    for m in resp.get("fileAttachmentsMetadata", []) or []]
            keys += [self._img_key(u) for u in resp.get("generatedImageUrls", []) or []]
            for k in keys:
                info = key2rel.get(k)
                if not info:
                    continue
                if info["is_image"]:
                    out.append(f"![image]({info['rel']})")
                else:
                    out.append(f"[{info.get('name') or '附件'}]({info['rel']})")
            body = "\n\n".join(x for x in out if x).strip()
            if not body:
                continue
            ts = util.ts(resp.get("createTime"))
            lines += [f"## {label.get(sender, sender or '?')}" + (f"  ·  {ts}" if ts else ""), "", body, ""]
        return "\n".join(lines)


adapter = GrokAdapter()
