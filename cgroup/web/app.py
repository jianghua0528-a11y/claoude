"""
C组 审核后台 (FastAPI)
  · 看板: 真实数据, 引擎实时算 (单数/业绩/各方净/艺人应发/妈咪应收)
  · 导入: 上传旧主文件 → 一键把字典+历史导上库
  · 审核队列: 机器解析的报单在这 改/确认/拒 → 入库
登录: HTTP Basic, 用户名 admin, 密码 = 环境变量 ADMIN_PASSWORD
"""
import os
import json
import secrets
import tempfile
from datetime import date

from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..db.session import get_session, init_db
from ..db.models import Order, Artist, Mama, Venue, ReviewItem, OperationLog
from ..core.settlement import Order as EOrder, compute

app = FastAPI(title="C组审核后台")
security = HTTPBasic()
ADMIN_PW = os.getenv("ADMIN_PASSWORD", "cgroup")


@app.on_event("startup")
def _startup():
    init_db()


def auth(cred: HTTPBasicCredentials = Depends(security)):
    if not secrets.compare_digest(cred.password, ADMIN_PW):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误",
                            {"WWW-Authenticate": "Basic"})
    return cred.username


_BASE = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'><title>__TITLE__ · C组</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#FBEAF0;color:#4B1528}
.top{background:#72243E;color:#fff;padding:13px 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.top b{font-size:17px;margin-right:6px}
.top a{color:#F4C0D1;text-decoration:none;font-size:14px}
.top a.on{color:#fff;font-weight:700}
.wrap{max-width:940px;margin:18px auto;padding:0 14px}
.card{background:#fff;border-radius:10px;padding:18px;margin-bottom:16px}
h2{color:#72243E;font-size:15px;margin:0 0 12px}
.kpi{display:flex;gap:10px;flex-wrap:wrap}
.kpi>div{flex:1;min-width:90px;background:#FBEAF0;border-radius:8px;padding:12px 8px;text-align:center}
.kpi .n{font-size:21px;font-weight:700;color:#72243E}
.kpi .l{font-size:12px;color:#888;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#72243E;color:#fff;padding:8px;text-align:left;font-weight:600}
td{padding:7px 8px;border-bottom:1px solid #F4C0D1}
.r{text-align:right}
.neg{color:#C0392B}
.btn{display:inline-block;padding:8px 15px;background:#72243E;color:#fff;border:none;border-radius:6px;text-decoration:none;font-size:13px;cursor:pointer}
.btn.g{background:#2E7D52}
.btn.x{background:#9c9a92}
.warn{display:inline-block;padding:1px 7px;background:#FBEAF0;border:1px solid #993556;border-radius:4px;color:#993556;font-size:11px;margin-left:4px}
.muted{color:#888;font-size:13px}
</style></head><body>
<div class=top><b>C组审核后台</b>__NAV__</div><div class=wrap>__BODY__</div></body></html>"""


def page(title, body, active=""):
    def lk(href, label, key):
        return f"<a class='{'on' if key == active else ''}' href='{href}'>{label}</a>"
    nav = lk("/", "看板", "home") + lk("/review", "审核队列", "review") + lk("/upload", "导入数据", "upload")
    return _BASE.replace("__TITLE__", title).replace("__NAV__", nav).replace("__BODY__", body)


def card(inner):
    return f"<div class=card>{inner}</div>"


def fmt(n):
    return f"{n:,.0f}"


# ───────────────────────── 看板 ─────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard(user=Depends(auth)):
    s = get_session()
    try:
        orders = s.query(Order).filter(Order.status == "已审核").all()
        if not orders:
            return page("看板", card("还没数据。先去 <a href='/upload'>导入数据</a> 上传旧主文件。"), "home")
        SK = SM = SO = a = m = c = 0.0
        wages, recv = {}, {}
        for o in orders:
            r = compute(EOrder(K=o.credit_k, M=o.cash_m, O=o.ticket_o,
                               mode=o.mode, flow=o.flow, wp=o.wp))
            SK += o.credit_k; SM += o.cash_m; SO += o.ticket_o
            a += r.artist_net; m += r.mama_net; c += r.company_net
            if o.artist_id:
                wages[o.artist_id] = wages.get(o.artist_id, 0) + r.artist_month_end
            if o.mama_id:
                recv[o.mama_id] = recv.get(o.mama_id, 0) + r.mama_receivable - r.mama_rebate
        art = {x.id: x.name for x in s.query(Artist).all()}
        mam = {x.id: x.name for x in s.query(Mama).all()}
        pending = s.query(ReviewItem).filter_by(status="待审").count()

        kpi = ("<div class=kpi>"
               f"<div><div class=n>{len(orders)}</div><div class=l>总单数</div></div>"
               f"<div><div class=n>{fmt(SK)}</div><div class=l>挂账</div></div>"
               f"<div><div class=n>{fmt(SM)}</div><div class=l>现金</div></div>"
               f"<div><div class=n>{fmt(SO)}</div><div class=l>门票</div></div></div>"
               "<div class=kpi style='margin-top:10px'>"
               f"<div><div class=n>{fmt(a)}</div><div class=l>艺人净</div></div>"
               f"<div><div class=n>{fmt(m)}</div><div class=l>妈咪净</div></div>"
               f"<div><div class=n>{fmt(c)}</div><div class=l>公司净</div></div></div>")
        head = f"<h2>总览（{len(orders)} 单）</h2>{kpi}"
        if pending:
            head += f"<p class=muted style='margin-top:12px'>🔔 审核队列有 <b>{pending}</b> 单待处理 → <a href='/review'>去审核</a></p>"

        wrows = "".join(
            f"<tr><td>{art.get(k, k)}</td><td class='r {'neg' if v < 0 else ''}'>{fmt(v)}</td></tr>"
            for k, v in sorted(wages.items(), key=lambda x: -x[1])[:12])
        wtab = f"<h2>艺人应发（月底应结，负=倒扣）</h2><table><tr><th>艺人</th><th class=r>应结(MYR)</th></tr>{wrows}</table>"

        rrows = "".join(
            f"<tr><td>{mam.get(k, k)}</td><td class=r>{fmt(v)}</td></tr>"
            for k, v in sorted(recv.items(), key=lambda x: -x[1])[:12] if abs(v) > 0.5)
        rtab = f"<h2>妈咪应结C组 Top</h2><table><tr><th>妈咪</th><th class=r>应结(MYR)</th></tr>{rrows}</table>"

        return page("看板", card(head) + card(wtab) + card(rtab), "home")
    finally:
        s.close()


# ───────────────────────── 导入数据 ─────────────────────────
@app.get("/upload", response_class=HTMLResponse)
def upload_form(user=Depends(auth)):
    s = get_session()
    try:
        n = s.query(Order).count()
    finally:
        s.close()
    note = f"<p class=muted>当前库里已有 {n} 单。重复上传只补新内容（已有的跳过）。</p>" if n else ""
    body = card(
        "<h2>导入旧主文件</h2>"
        "<p class=muted>上传 C组数据工作流 .xlsx，自动把字典 + 历史报单导进库。</p>"
        + note +
        "<form action='/upload' method='post' enctype='multipart/form-data' style='margin-top:14px'>"
        "<input type='file' name='file' accept='.xlsx' required> "
        "<button class='btn' type='submit'>上传并导入</button></form>")
    return page("导入数据", body, "upload")


@app.post("/upload", response_class=HTMLResponse)
def upload_do(user=Depends(auth), file: UploadFile = File(...)):
    from ..db.migrate import migrate
    tmp = os.path.join(tempfile.gettempdir(), "cgroup_master.xlsx")
    with open(tmp, "wb") as f:
        f.write(file.file.read())
    try:
        migrate(tmp)
    except Exception as e:
        return page("导入数据", card(f"<h2>导入出错</h2><p class=muted>{e}</p><a class=btn href='/upload'>返回</a>"), "upload")
    s = get_session()
    try:
        n = s.query(Order).count()
        s.add(OperationLog(action="导入主文件", target=file.filename, detail=f"库内共{n}单"))
        s.commit()
    finally:
        s.close()
    return page("导入数据", card(f"<h2>✅ 导入完成</h2><p class=muted>库内共 {n} 单。</p><a class=btn href='/'>去看板</a>"), "upload")


# ───────────────────────── 审核队列 ─────────────────────────
@app.get("/review", response_class=HTMLResponse)
def review_list(user=Depends(auth)):
    s = get_session()
    try:
        items = s.query(ReviewItem).filter_by(status="待审").order_by(ReviewItem.id).all()
        if not items:
            return page("审核队列", card("<h2>审核队列</h2><p class=muted>暂无待审。机器人解析报单后会出现在这里。</p>"), "review")
        rows = ""
        for it in items:
            try:
                d = json.loads(it.parsed_json or "{}")
            except Exception:
                d = {}
            warn = f"<span class=warn>{it.parse_warnings}</span>" if it.parse_warnings else ""
            summary = (f"{d.get('艺人','?')} · {d.get('场所','?')} · 妈咪{d.get('妈咪','-')} · "
                       f"挂{d.get('K',0)}/现{d.get('M',0)}/票{d.get('O',0)} · {d.get('客人','-')}")
            rows += (f"<tr><td>#{it.id}<br><span class=muted>{it.source_group}</span></td>"
                     f"<td>{summary}{warn}</td>"
                     f"<td><form style='display:inline' action='/review/{it.id}/confirm' method='post'>"
                     f"<button class='btn g'>确认入库</button></form> "
                     f"<form style='display:inline' action='/review/{it.id}/reject' method='post'>"
                     f"<button class='btn x'>拒</button></form></td></tr>")
        body = f"<h2>审核队列（{len(items)} 单待处理）</h2><table><tr><th>来源</th><th>解析结果</th><th>操作</th></tr>{rows}</table>"
        return page("审核队列", card(body), "review")
    finally:
        s.close()


@app.post("/review/{item_id}/confirm")
def review_confirm(item_id: int, user=Depends(auth)):
    s = get_session()
    try:
        it = s.get(ReviewItem, item_id)
        if it:
            d = json.loads(it.parsed_json or "{}")
            art = s.query(Artist).filter_by(name=d.get("艺人", "")).first()
            mama = s.query(Mama).filter_by(name=d.get("妈咪", "")).first()
            ven = s.query(Venue).filter_by(name=d.get("场所", "")).first()
            s.add(Order(
                biz_date=date.today(), artist_id=art.id if art else None,
                venue_id=ven.id if ven else None, mama_id=mama.id if mama else None,
                mode=d.get("合作模式", "标准" if mama else "自单"), flow=d.get("流向"),
                credit_k=float(d.get("K", 0) or 0), cash_m=float(d.get("M", 0) or 0),
                ticket_o=float(d.get("O", 0) or 0), customer=d.get("客人"),
                remark=d.get("备注"), status="已审核", source_msg_id=it.tg_msg_id))
            it.status = "已确认"
            s.add(OperationLog(action="审核确认", target=f"review#{item_id}"))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/review", status_code=303)


@app.post("/review/{item_id}/reject")
def review_reject(item_id: int, user=Depends(auth)):
    s = get_session()
    try:
        it = s.get(ReviewItem, item_id)
        if it:
            it.status = "已拒"
            s.add(OperationLog(action="审核拒绝", target=f"review#{item_id}"))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/review", status_code=303)


@app.get("/health")
def health():
    s = get_session()
    try:
        return {"ok": True, "orders": s.query(Order).count(),
                "pending": s.query(ReviewItem).filter_by(status="待审").count()}
    finally:
        s.close()
