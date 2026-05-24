"""杂项工具：时间格式化、文件名净化、扩展名推断。"""
import os
import re
from datetime import datetime, timezone

EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/heic": ".heic", "application/pdf": ".pdf",
}
IMG_MIMES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/heic")


def ts(val):
    """把 epoch 秒或 ISO 字符串统一格式化成本地时间字符串。"""
    if not val:
        return ""
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return val
    try:
        return datetime.fromtimestamp(val, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(val)


def safe_name(name, fallback="", limit=80):
    """净化成可作文件名的字符串。"""
    name = (name or fallback).strip() or fallback
    return re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name)[:limit]


def safe_account(name):
    """账号目录名：过滤路径分隔等非法字符（保留 @ 等）。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name or "unknown-account")


def ext_for(name, mime):
    """根据文件名或 MIME 推断扩展名。"""
    _, ext = os.path.splitext(name or "")
    if ext and len(ext) <= 6:
        return ext.lower()
    return EXT_BY_MIME.get(mime, ".bin")
