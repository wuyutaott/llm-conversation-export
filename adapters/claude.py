"""Claude (claude.ai) adapter。

认证：cookie（页面内 fetch 自动带），账号用 /api/account 的邮箱。
组织：从 /api/account 的 memberships 里挑 capabilities 含 'chat' 的 org（不可硬编码 uuid，
      同一浏览器可能有多个组织/账号，登录态不同 uuid 也不同）。
列表：/api/organizations/{org}/chat_conversations?limit&offset，按 updated_at 降序，可分页。
详情：.../chat_conversations/{uuid}?tree=True&rendering_mode=messages&render_all_tools=true
      → chat_messages 已是线性主线程顺序，正文在 content[].text（顶层 text 多为空）。
图片：消息的 files/files_v2 中 file_kind=='image' 的带 preview_url（/api/{org}/files/{uuid}/preview）；
      file_kind=='blob'（用户原始上传，如 HEIC）无公开 URL，跳过——通常已有对应 image 版本。
附件：attachments 的 extracted_content 是已提取文本，直接内嵌 Markdown，无需下载。
"""
import json

from core import browser, util


class ClaudeAdapter:
    name = "claude"
    domain = "claude.ai"
    home_url = "https://claude.ai/"
    gap_min = 1.0     # 会话之间随机停顿区间（秒），模拟真人节奏
    gap_max = 8.0
    img_gap = 0.8
    page_size = 50    # chat_conversations 每页大小

    def __init__(self):
        self._org = None
        self._email = None

    # ---- 认证 ----
    def prepare(self):
        acc = browser.fetch_json("/api/account", credentials=True)
        self._email = acc.get("email_address")
        for m in acc.get("memberships", []) or []:
            o = m.get("organization") or {}
            if "chat" in (o.get("capabilities") or []):
                self._org = o.get("uuid")
                break
        if not self._org:
            raise RuntimeError("找不到含 chat 能力的组织，请确认 Chrome 已登录 claude.ai")
        return self._email or "unknown-account"

    def _conv_url(self, cid):
        return (f"/api/organizations/{self._org}/chat_conversations/{cid}"
                "?tree=True&rendering_mode=messages&render_all_tools=true")

    # ---- 列表 ----
    def list_conversations(self, known_ids=None):
        items, seen, offset = [], set(), 0
        while True:
            batch = browser.fetch_json(
                f"/api/organizations/{self._org}/chat_conversations"
                f"?limit={self.page_size}&offset={offset}", credentials=True)
            if not isinstance(batch, list) or not batch:
                break
            new = [c for c in batch if c.get("uuid") not in seen]
            for c in new:
                seen.add(c.get("uuid"))
            items.extend(new)
            print(f"  已取 {len(items)}")
            # 增量提前停止：整页都在已有清单里 → 后面都是更早的会话（列表按 updated 降序），无新增
            if known_ids and all(c.get("uuid") in known_ids for c in batch):
                print("  本页均已在清单中，提前停止")
                break
            if len(batch) < self.page_size:
                break
            offset += len(batch)
        return [{"id": c.get("uuid"), "title": c.get("name") or c.get("summary"),
                 "create_time": util.ts(c.get("create_time") or c.get("created_at")),
                 "update_time": util.ts(c.get("update_time") or c.get("updated_at"))} for c in items]

    # ---- 详情 ----
    def fetch_conversation(self, cid):
        d = browser.fetch_json(self._conv_url(cid), credentials=True)
        d.setdefault("uuid", cid)
        return d

    # ---- 资源 ----
    @staticmethod
    def _iter_files(conv):
        for msg in conv.get("chat_messages", []) or []:
            for f in (msg.get("files") or []) + (msg.get("files_v2") or []):
                yield f

    def collect_assets(self, conv):
        out, seen = [], set()
        for f in self._iter_files(conv):
            if f.get("file_kind") != "image":      # 仅 image 类有公开 preview_url；blob 无 URL，跳过
                continue
            fid = f.get("file_uuid") or f.get("uuid")
            url = f.get("preview_url") or (f.get("thumbnail_asset") or {}).get("url") or f.get("thumbnail_url")
            if not fid or not url or fid in seen:
                continue
            seen.add(fid)
            out.append({"key": fid, "name": f.get("file_name") or "", "mime": "", "is_image": True, "url": url})
        return out

    def download_asset(self, desc):
        data, ct = browser.download(desc["url"], credentials=True)
        # preview 实际多为 image/webp，与原始文件名扩展名（.jpg/.heic）不符 → 用真实 content-type 定扩展名
        name = util.safe_name(desc.get("name") or "", limit=80)
        base = name.rsplit(".", 1)[0] if "." in name else name
        return data, base, ct or "image/webp"

    # ---- 渲染 ----
    @staticmethod
    def _block_text(val):
        """tool_result.content 可能是 str / [{type,text|content}] / dict，统一抽成文本。"""
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            return "\n".join(ClaudeAdapter._block_text(x) for x in val if x).strip()
        if isinstance(val, dict):
            return val.get("text") or ClaudeAdapter._block_text(val.get("content")) or ""
        return ""

    def _render_message(self, msg, key2rel):
        out = []
        for c in msg.get("content", []) or []:
            t = c.get("type")
            if t == "text":
                if c.get("text"):
                    out.append(c["text"])
            elif t == "thinking":
                think = c.get("thinking") or c.get("text") or ""
                if think.strip():
                    out.append(f"<details>\n<summary>💭 思考</summary>\n\n{think}\n\n</details>")
            elif t == "tool_use":
                inp = json.dumps(c.get("input") or {}, ensure_ascii=False)
                if len(inp) > 300:
                    inp = inp[:300] + "…"
                out.append(f"> 🔧 调用工具 **{c.get('name', '')}** `{inp}`")
            elif t == "tool_result":
                body = self._block_text(c.get("content") or c.get("display_content"))
                if body.strip():
                    out.append(f"<details>\n<summary>🔧 {c.get('name', '工具')} 结果</summary>\n\n```\n{body}\n```\n\n</details>")
            elif t in ("image", "image_v2"):
                fid = c.get("file_uuid") or (c.get("source") or {}).get("file_uuid")
                info = key2rel.get(fid)
                out.append(f"![image]({info['rel']})" if info else "[图片(未下载)]")
        if not out and msg.get("text"):           # 老消息：正文可能仅在顶层 text
            out.append(msg["text"])
        # 图片/文件附件
        for f in (msg.get("files") or []) + (msg.get("files_v2") or []):
            fid = f.get("file_uuid") or f.get("uuid")
            info = key2rel.get(fid)
            if info:
                out.append(f"![{f.get('file_name', 'image')}]({info['rel']})")
            elif f.get("file_kind") == "blob":     # 原始上传无公开 URL（通常已有 image 版本覆盖）
                out.append(f"[附件: {f.get('file_name', '文件')}（原图未导出）]")
        # 文本附件：extracted_content 已是提取好的正文，直接内嵌
        for att in msg.get("attachments", []) or []:
            content = att.get("extracted_content") or ""
            fn = att.get("file_name") or "附件"
            if content.strip():
                out.append(f"📎 **{fn}**\n\n```\n{content}\n```")
            else:
                out.append(f"📎 附件: {fn}")
        return "\n\n".join(x for x in out if x).strip()

    def render_markdown(self, conv, title, key2rel):
        lines = [f"# {title}", "",
                 f"- 创建时间: {util.ts(conv.get('created_at') or conv.get('create_time'))}",
                 f"- 更新时间: {util.ts(conv.get('updated_at') or conv.get('update_time'))}",
                 f"- 模型: {conv.get('model') or ''}",
                 f"- 会话 ID: {conv.get('uuid', '')}",
                 "", "---", ""]
        label = {"human": "🧑 用户", "assistant": "🤖 Claude"}
        for msg in conv.get("chat_messages", []) or []:
            sender = (msg.get("sender") or "").lower()
            text = self._render_message(msg, key2rel)
            if not text:
                continue
            lines += [f"## {label.get(sender, sender or '?')}", "", text, ""]
        return "\n".join(lines)


adapter = ClaudeAdapter()
