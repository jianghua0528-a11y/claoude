"""
审核后台 (FastAPI)
当前: 上线状态页 + 健康检查 (确认部署+DB通)
下一步: 登录 / 待审队列 / 确认入库 / 看板
"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from ..db.session import engine, get_session, init_db
from ..db.models import Mama, Artist, Venue, Order, ReviewItem

app = FastAPI(title="C组审核后台")


@app.on_event("startup")
def _startup():
    init_db()        # 首次部署自动建表


def _counts():
    s = get_session()
    try:
        return dict(妈咪=s.query(Mama).count(), 艺人=s.query(Artist).count(),
                    场所=s.query(Venue).count(), 报单=s.query(Order).count(),
                    待审=s.query(ReviewItem).filter_by(status="待审").count())
    finally:
        s.close()


@app.get("/health")
def health():
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {"ok": True, "db": "connected", "counts": _counts()}
    except Exception as e:
        return {"ok": False, "db": "error", "detail": str(e)}


@app.get("/", response_class=HTMLResponse)
def home():
    try:
        c = _counts()
        db_ok = "✅ 已连接"
        rows = "".join(f"<tr><td>{k}</td><td style='text-align:right'>{v}</td></tr>" for k, v in c.items())
    except Exception as e:
        db_ok, rows = f"❌ {e}", ""
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>C组系统</title></head>
<body style='font-family:-apple-system,sans-serif;background:#FBEAF0;margin:0;padding:40px'>
<div style='max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:28px'>
<h1 style='color:#72243E;margin:0 0 4px'>C组 自动报单系统</h1>
<p style='color:#888;margin:0 0 20px'>🟢 已上线 · 骨架版</p>
<p style='color:#4B1528'>数据库: <b>{db_ok}</b></p>
<table style='width:100%;border-collapse:collapse;color:#4B1528'>
<thead><tr style='background:#72243E;color:#fff'><th style='padding:8px;text-align:left'>表</th><th style='padding:8px;text-align:right'>条数</th></tr></thead>
<tbody>{rows}</tbody></table>
<p style='color:#888;font-size:13px;margin-top:20px'>下一步上线: 解析引擎 · 审核队列 · 机器人</p>
</div></body></html>"""
