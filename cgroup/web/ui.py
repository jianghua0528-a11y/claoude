"""
后台公共件  ·  web/ui.py
登录 / 页面骨架 / 表单控件 / 转义。所有拼进 HTML 的外部文字(群消息、客人名、备注…)一律过 esc()。
"""
import html
import logging
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

log = logging.getLogger("cgroup-web")
security = HTTPBasic()
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PW = os.getenv("ADMIN_PASSWORD", "cgroup")
if ADMIN_PW == "cgroup":
    log.warning("ADMIN_PASSWORD 未设置, 正在用默认密码, 上线前务必改掉")


def auth(cred: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(cred.username.encode(), ADMIN_USER.encode())
    ok_pw = secrets.compare_digest(cred.password.encode(), ADMIN_PW.encode())
    if not (ok_user and ok_pw):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误",
                            {"WWW-Authenticate": "Basic"})
    return cred.username


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def fmt(n):
    return f"{(n or 0):,.0f}"


_BASE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'><title>__TITLE__ · C组</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#FBEAF0;color:#4B1528}
.top{background:#72243E;color:#fff;padding:13px 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.top b{font-size:17px;margin-right:6px}
.top a{color:#F4C0D1;text-decoration:none;font-size:14px}
.top a.on{color:#fff;font-weight:700}
.wrap{max-width:1040px;margin:18px auto;padding:0 14px}
.card{background:#fff;border-radius:10px;padding:18px;margin-bottom:16px;overflow-x:auto}
h2{color:#72243E;font-size:15px;margin:0 0 12px}
h3{color:#72243E;font-size:14px;margin:16px 0 8px}
.kpi{display:flex;gap:10px;flex-wrap:wrap}
.kpi>div{flex:1;min-width:90px;background:#FBEAF0;border-radius:8px;padding:12px 8px;text-align:center}
.kpi .n{font-size:21px;font-weight:700;color:#72243E}
.kpi .l{font-size:12px;color:#888;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#72243E;color:#fff;padding:8px;text-align:left;font-weight:600;white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid #F4C0D1;vertical-align:top}
.r{text-align:right}
.neg{color:#C0392B}
.btn{display:inline-block;padding:8px 15px;background:#72243E;color:#fff;border:none;border-radius:6px;text-decoration:none;font-size:13px;cursor:pointer}
.btn.g{background:#2E7D52}
.btn.x{background:#9c9a92}
.btn.s{padding:4px 9px;font-size:12px}
.warn{display:inline-block;padding:1px 7px;background:#FBEAF0;border:1px solid #993556;border-radius:4px;color:#993556;font-size:11px;margin-left:4px}
.err{background:#FDECEA;border:1px solid #C0392B;color:#C0392B;border-radius:6px;padding:10px 12px;margin-bottom:12px;font-size:13px}
.ok{background:#E8F6EF;border:1px solid #2E7D52;color:#2E7D52;border-radius:6px;padding:10px 12px;margin-bottom:12px;font-size:13px}
.muted{color:#888;font-size:13px}
.raw{white-space:pre-wrap;background:#FBEAF0;border-radius:6px;padding:10px;font-size:12px;max-height:260px;overflow:auto}
.bd{display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap}
.bd.due{background:#FDECEA;color:#C0392B}
.bd.ok{background:#E8F6EF;color:#2E7D52}
.bd.dir{background:#EEE;color:#777}
.tg{padding:3px 7px;font-size:11px;border:none;border-radius:5px;background:#993556;color:#fff;cursor:pointer}
.tg.u{background:#bbb}
input,select,textarea{padding:7px;border:1px solid #F4C0D1;border-radius:6px;font-size:13px;box-sizing:border-box}
.inline input,.inline select{width:auto}
.pager{margin-top:12px;display:flex;gap:8px;align-items:center}
</style></head><body>
<div class=top><b>C组审核后台</b>__NAV__</div><div class=wrap>__BODY__</div></body></html>"""

NAV = [("/", "看板", "home"), ("/review", "审核队列", "review"), ("/orders", "订单", "orders"),
       ("/finance", "财务流水", "finance"), ("/dicts", "字典", "dicts"),
       ("/logs", "日志", "logs"), ("/upload", "导入数据", "upload")]


def page(title, body, active=""):
    nav = "".join(f"<a class='{'on' if key == active else ''}' href='{href}'>{label}</a>"
                  for href, label, key in NAV)
    return _BASE.replace("__TITLE__", esc(title)).replace("__NAV__", nav).replace("__BODY__", body)


def card(inner):
    return f"<div class=card>{inner}</div>"


def msg_box(ok=None, err=None):
    out = ""
    if ok:
        out += f"<div class=ok>{esc(ok)}</div>"
    if err:
        out += f"<div class=err>{esc(err)}</div>"
    return out


def sel(name, options, current):
    """options: 值列表 或 (值, 显示) 列表。"""
    opts = ""
    for o in options:
        val, label = o if isinstance(o, tuple) else (o, o)
        on = " selected" if str(val) == str(current if current is not None else "") else ""
        opts += f"<option value='{esc(val)}'{on}>{esc(label)}</option>"
    return f"<select name='{name}'>{opts}</select>"


def inp(name, value, ph="", width="90%", typ="text", extra=""):
    return (f"<input type='{typ}' name='{name}' value='{esc(value)}' placeholder='{esc(ph)}' "
            f"style='width:{width}' {extra}>")


def datalist(list_id, names):
    return f"<datalist id='{list_id}'>" + "".join(f"<option value='{esc(n)}'>" for n in names) + "</datalist>"


def form_table(rows):
    """rows: [(标签, 控件html)] → 两列表单表。"""
    trs = "".join(f"<tr><td style='padding:6px;color:#888;white-space:nowrap'>{esc(l)}</td>"
                  f"<td style='padding:6px'>{f}</td></tr>" for l, f in rows)
    return f"<table>{trs}</table>"


def month_picker(action, ym, extra_hidden=""):
    return (f"<form method=get action='{action}' class=inline style='margin-bottom:12px'>"
            f"<input type=month name=m value='{esc(ym)}'> {extra_hidden}"
            f"<button class='btn s'>切换月份</button> "
            f"<a class='btn s x' href='{action}?m=all'>全部</a></form>")
