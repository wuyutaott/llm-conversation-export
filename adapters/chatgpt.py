"""ChatGPT adapter。

认证：/api/auth/session 取 accessToken（缓存复用，401 刷新），后续请求带 Bearer。
账号：登录邮箱。图片：image_asset_pointer + 附件，经 /files/{id}/download 取签名 URL 再下载。
"""
import json

from core import browser, util


class ChatGPTAdapter:
    name = "chatgpt"
    domain = "chatgpt.com"
    home_url = "https://chatgpt.com/"
    gap_min = 1.0     # 会话之间随机停顿区间（秒），模拟真人节奏
    gap_max = 10.0
    img_gap = 1.0

    def __init__(self):
        self._token = None
        self._email = None

    # ---- 认证 ----
    def _refresh(self):
        o = browser.fetch_json("/api/auth/session")
        self._token = o.get("accessToken")
        self._email = (o.get("user") or {}).get("email")
        if not self._token:
            raise RuntimeError("拿不到 accessToken，请确认 Chrome 仍登录 ChatGPT")

    def prepare(self):
        self._refresh()
        return self._email or "unknown-account"

    def _api(self, path, method="GET", body=None):
        """带 Bearer 的请求；401 刷新 token 重试一次。"""
        for refreshed in (False, True):
            if not self._token:
                self._refresh()
            s, t = browser.fetch(path, method, body, headers={"Authorization": "Bearer " + self._token})
            if s == 401 and not refreshed:
                self._token = None
                continue
            if s != 200:
                raise RuntimeError(f"HTTP {s}: {t[:200]}")
            return json.loads(t)
        raise RuntimeError("鉴权失败")

    # ---- 列表 ----
    def list_conversations(self):
        items, seen, offset = [], set(), 0
        while True:
            d = self._api(f"/backend-api/conversations?offset={offset}&limit=28&order=updated")
            batch = d.get("items", [])
            new = [c for c in batch if c.get("id") not in seen]
            for c in new:
                seen.add(c.get("id"))
            items.extend(new)
            print(f"  已取 {len(items)}")
            if not batch or not new:
                break
            offset += len(batch)
        return [{"id": c.get("id"), "title": c.get("title"),
                 "create_time": util.ts(c.get("create_time")),
                 "update_time": util.ts(c.get("update_time"))} for c in items]

    # ---- 详情 ----
    def fetch_conversation(self, cid):
        d = self._api(f"/backend-api/conversation/{cid}")
        d.setdefault("id", cid)
        return d

    # ---- 资源 ----
    @staticmethod
    def _fid(asset_pointer):
        return asset_pointer.split("://", 1)[-1] if asset_pointer else ""

    def collect_assets(self, conv):
        out, seen = [], set()

        def add(fid, name, mime, is_img):
            if fid and fid not in seen:
                seen.add(fid)
                out.append({"key": fid, "name": name, "mime": mime, "is_image": is_img})

        for node in (conv.get("mapping") or {}).values():
            msg = node.get("message") or {}
            for p in (msg.get("content") or {}).get("parts", []) or []:
                if isinstance(p, dict) and p.get("content_type") == "image_asset_pointer":
                    add(self._fid(p.get("asset_pointer")), "", "", True)
            for att in (msg.get("metadata") or {}).get("attachments", []) or []:
                mime = att.get("mime_type") or ""
                add(att.get("id"), att.get("name") or "", mime, mime.startswith("image/"))
        return out

    def download_asset(self, desc):
        meta = self._api(f"/backend-api/files/{desc['key']}/download")
        url = meta.get("download_url")
        if not url:
            raise RuntimeError("无 download_url")
        data, ct = browser.download(url, headers={"Authorization": "Bearer " + self._token})
        return data, meta.get("file_name") or desc.get("name") or desc["key"], ct or desc.get("mime", "")

    # ---- 渲染 ----
    @staticmethod
    def _linearize(mapping):
        root = next((nid for nid, n in mapping.items() if not n.get("parent")), None)
        ordered, seen, node_id = [], set(), root
        while node_id and node_id not in seen:
            seen.add(node_id)
            node = mapping.get(node_id, {})
            if node.get("message"):
                ordered.append(node["message"])
            ch = node.get("children", [])
            node_id = ch[-1] if ch else None
        return ordered

    def _render_message(self, msg, key2rel):
        content = msg.get("content") or {}
        ctype = content.get("content_type")
        out = []
        if ctype == "text":
            out.append("\n".join(content.get("parts", []) or []))
        elif ctype == "multimodal_text":
            for p in content.get("parts", []) or []:
                if isinstance(p, str):
                    out.append(p)
                elif isinstance(p, dict) and p.get("content_type") == "image_asset_pointer":
                    info = key2rel.get(self._fid(p.get("asset_pointer")))
                    out.append(f"![image]({info['rel']})" if info else "[图片(未下载)]")
                elif isinstance(p, dict) and p.get("text"):
                    out.append(p["text"])  # 语音转录（audio_transcription）等带文字的 part
                elif isinstance(p, dict) and p.get("content_type") in (
                        "audio_transcription", "audio_asset_pointer",
                        "real_time_user_audio_video_asset_pointer"):
                    pass  # 语音相关 part：有文字的已在上一分支取出；空转录(沉默/噪音)与纯资源指针不输出占位
                elif isinstance(p, dict):
                    out.append(f"[{p.get('content_type', 'asset')}]")
        elif ctype == "code":
            out.append("```\n" + (content.get("text", "") or "") + "\n```")
        else:
            for k in ("text", "result"):
                if content.get(k):
                    out.append(str(content[k]))
        for att in (msg.get("metadata") or {}).get("attachments", []) or []:
            info = key2rel.get(att.get("id"))
            if info and f"]({info['rel']})" not in "\n".join(out):
                tag = f"![{att.get('name','image')}]" if info["is_image"] else f"[{att.get('name','附件')}]"
                out.append(f"{tag}({info['rel']})")
        return "\n".join(x for x in out if x).strip()

    def render_markdown(self, conv, title, key2rel):
        lines = [f"# {title}", "",
                 f"- 创建时间: {util.ts(conv.get('create_time'))}",
                 f"- 更新时间: {util.ts(conv.get('update_time'))}",
                 f"- 会话 ID: {conv.get('conversation_id') or conv.get('id', '')}",
                 "", "---", ""]
        label = {"user": "🧑 用户", "assistant": "🤖 ChatGPT", "tool": "🔧 工具"}
        for msg in self._linearize(conv.get("mapping") or {}):
            role = (msg.get("author", {}) or {}).get("role", "")
            if role == "system":
                continue
            if (msg.get("metadata", {}) or {}).get("is_visually_hidden_from_conversation"):
                continue
            text = self._render_message(msg, key2rel)
            if not text:
                continue
            lines += [f"## {label.get(role, role)}", "", text, ""]
        return "\n".join(lines)


adapter = ChatGPTAdapter()
