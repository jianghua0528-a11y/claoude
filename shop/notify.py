"""后台(网页进程)直接调 Telegram Bot API: 通知顾客 / 取付款截图。
网页和机器人是两个进程, 这里用同步 HTTP, 不依赖机器人进程在线。"""
import os
import json
import logging
import urllib.request
import urllib.parse

log = logging.getLogger("shop-notify")
TOKEN = os.getenv("SHOP_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
_API = "https://api.telegram.org"


def _call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{_API}/bot{TOKEN}/{method}", data=data, timeout=15) as r:
        return json.loads(r.read())


def send_message(chat_id, text) -> bool:
    if not TOKEN:
        log.warning("没设 SHOP_BOT_TOKEN, 不发通知")
        return False
    try:
        return bool(_call("sendMessage", chat_id=chat_id, text=text).get("ok"))
    except Exception:
        log.exception("通知 %s 失败", chat_id)
        return False


def download_file(file_id) -> bytes | None:
    """按 file_id 下载文件(付款截图)。"""
    if not TOKEN or not file_id:
        return None
    try:
        path = _call("getFile", file_id=file_id)["result"]["file_path"]
        with urllib.request.urlopen(f"{_API}/file/bot{TOKEN}/{path}", timeout=30) as r:
            return r.read()
    except Exception:
        log.exception("下载文件失败")
        return None
