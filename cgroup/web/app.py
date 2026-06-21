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
from ..core import status as ST

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
.bd{display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap}
.bd.due{background:#FDECEA;color:#C0392B}
.bd.ok{background:#E8F6EF;color:#2E7D52}
.bd.dir{background:#EEE;color:#777}
.tg{padding:3px 7px;font-size:11px;border:none;border-radius:5px;background:#993556;color:#fff;cursor:pointer}
.tg.u{background:#bbb}
</style></head><body>
<div class=top><b>C组审核后台</b>__NAV__</div><div class=wrap>__BODY__</div></body></html>"""


def page(title, body, active=""):
    def lk(href, label, key):
        return f"<a class='{'on' if key == active else ''}' href='{href}'>{label}</a>"
    nav = (lk("/", "看板", "home") + lk("/review", "审核队列", "review")
           + lk("/orders", "订单", "orders") + lk("/upload", "导入数据", "upload"))
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
        wages, due_mama = {}, {}
        col_cnt = col_sum = sk_cnt = sk_sum = 0          # 代收/自留 两轨待清
        for o in orders:
            r = compute(EOrder(K=o.credit_k, M=o.cash_m, O=o.ticket_o,
                               mode=o.mode, flow=o.flow, wp=o.wp))
            SK += o.credit_k; SM += o.cash_m; SO += o.ticket_o
            a += r.artist_net; m += r.mama_net; c += r.company_net
            if o.artist_id:
                wages[o.artist_id] = wages.get(o.artist_id, 0) + r.artist_month_end
            # ── 三轨待清 ──
            if o.mama_id and ST.is_due_from_mama(o.credit_status):
                due_mama[o.mama_id] = due_mama.get(o.mama_id, 0) + r.mama_receivable
            if ST.is_due_to_artist(o.cash_status, o.flow):
                col_cnt += 1; col_sum += o.cash_m
            if ST.is_due_clawback(o.cash_status, o.flow):
                sk_cnt += 1; sk_sum += o.cash_m
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
            for k, v in sorted(due_mama.items(), key=lambda x: -x[1]) if abs(v) > 0.5)
        rtab = (f"<h2>① 待妈咪结款 · 应收账（{len(due_mama)} 妈咪）</h2>"
                f"<table><tr><th>妈咪</th><th class=r>待结C组(MYR)</th></tr>{rrows or '<tr><td colspan=2 class=muted>全部已结</td></tr>'}</table>")

        cash_tracks = (
            "<h2>现金两轨待清</h2><div class=kpi>"
            f"<div><div class=n>{col_cnt}</div><div class=l>② 公司代收待发<br>{fmt(col_sum)} 现金</div></div>"
            f"<div><div class=n>{sk_cnt}</div><div class=l>③ 艺人自留待倒扣<br>{fmt(sk_sum)} 现金</div></div>"
            "</div><p class=muted style='margin-top:8px'>② 公司已收的现金，待工资发放给艺人/妈咪。"
            "③ 艺人现场已拿，月底从工资倒扣公司+妈咪份。</p>")

        return page("看板", card(head) + card(rtab) + card(cash_tracks) + card(wtab), "home")
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
    from ..core.intake import create_order_from_payload
    s = get_session()
    try:
        it = s.get(ReviewItem, item_id)
        if it:
            d = json.loads(it.parsed_json or "{}")
            create_order_from_payload(d, s, source_msg_id=it.tg_msg_id)
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


# ───────────────────────── 改单 (订单列表 + 编辑 + 作废) ─────────────────────────
from fastapi import Form
from datetime import datetime as _dt

MODES = ["标准", "直结", "自单", "全归艺人"]
FLOWS = ["", "A", "B", "D", "E", "D60"]


def credit_cell(o):
    """挂账态徽章 + 待结↔已结 一键切换。"""
    cs = o.credit_status
    if cs in (None, ST.CREDIT_NONE):  return "<span class=muted>—</span>"
    if cs == ST.CREDIT_DIRECT:        return "<span class='bd dir'>直结</span>"
    f = f"<form method=post action='/orders/{o.id}/credit_toggle' style='display:inline'>"
    if cs == ST.CREDIT_PAID:
        return f"<span class='bd ok'>已结</span> {f}<button class='tg u'>撤</button></form>"
    return f"<span class='bd due'>{cs}</span> {f}<button class='tg'>标已结</button></form>"


def cash_cell(o):
    """现金态徽章(待/已 + 代收/自留分支) + 一键切换。"""
    ms = o.cash_status
    if ms in (None, ST.CASH_NONE):  return "<span class=muted>—</span>"
    if ms == ST.CASH_DIRECT:        return "<span class='bd dir'>直结</span>"
    br = ST.cash_branch(o.flow)
    f = f"<form method=post action='/orders/{o.id}/cash_toggle' style='display:inline'>"
    if ms == ST.CASH_DONE:
        return f"<span class='bd ok'>已·{br}</span> {f}<button class='tg u'>撤</button></form>"
    return f"<span class='bd due'>待·{br}</span> {f}<button class='tg'>标已</button></form>"


@app.get("/orders", response_class=HTMLResponse)
def orders_list(user=Depends(auth), q: str = "", n: int = 60):
    s = get_session()
    try:
        art = {x.id: x.name for x in s.query(Artist).all()}
        mam = {x.id: x.name for x in s.query(Mama).all()}
        ven = {x.id: x.name for x in s.query(Venue).all()}
        query = s.query(Order).order_by(Order.biz_date.desc().nullslast(), Order.id.desc())
        rows_all = query.limit(400).all()
        if q:
            rows_all = [o for o in rows_all if q in (art.get(o.artist_id, "") + mam.get(o.mama_id, "")
                        + (o.customer or "") + ven.get(o.venue_id, ""))]
        rows_all = rows_all[:n]
        trs = ""
        for o in rows_all:
            void = o.status != "已审核"
            style = " style='opacity:.45'" if void else ""
            trs += (f"<tr{style}><td>#{o.id}</td>"
                    f"<td>{o.biz_date.strftime('%m/%d') if o.biz_date else '—'}</td>"
                    f"<td>{art.get(o.artist_id, '?')}</td>"
                    f"<td>{ven.get(o.venue_id, '') } {o.room or ''}</td>"
                    f"<td>{mam.get(o.mama_id, '自单')}</td>"
                    f"<td class=r>{fmt(o.credit_k)}</td><td class=r>{fmt(o.cash_m)}</td>"
                    f"<td class=r>{fmt(o.ticket_o)}</td>"
                    f"<td>{credit_cell(o)}</td><td>{cash_cell(o)}</td>"
                    f"<td>{o.mode}</td>"
                    f"<td>{o.customer or '—'}</td>"
                    f"<td><a class=btn href='/orders/{o.id}/edit'>改</a></td></tr>")
        body = (f"<h2>订单（改单 / 作废）</h2>"
                f"<form method='get' style='margin-bottom:12px'>"
                f"<input name='q' value='{q}' placeholder='搜艺人/妈咪/客人/场所' style='padding:7px;border:1px solid #F4C0D1;border-radius:6px'> "
                f"<button class='btn'>搜</button></form>"
                f"<table><tr><th>#</th><th>日期</th><th>艺人</th><th>场所</th><th>妈咪</th>"
                f"<th class=r>挂账</th><th class=r>现金</th><th class=r>门票</th>"
                f"<th>挂账态</th><th>现金态</th><th>档</th><th>客人</th><th></th></tr>{trs}</table>")
        return page("订单", card(body), "orders")
    finally:
        s.close()


def _sel(name, options, current):
    opts = "".join(f"<option{' selected' if str(o) == str(current) else ''}>{o}</option>" for o in options)
    return f"<select name='{name}' style='padding:7px;border:1px solid #F4C0D1;border-radius:6px'>{opts}</select>"


def _inp(name, value, ph=""):
    return f"<input name='{name}' value='{value if value is not None else ''}' placeholder='{ph}' style='padding:7px;border:1px solid #F4C0D1;border-radius:6px;width:90%'>"


@app.get("/orders/{oid}/edit", response_class=HTMLResponse)
def order_edit_form(oid: int, user=Depends(auth)):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if not o:
            return page("改单", card("没找到这单。<a href='/orders'>返回</a>"), "orders")
        art = {x.id: x.name for x in s.query(Artist).all()}
        mam = {x.id: x.name for x in s.query(Mama).all()}
        ven = {x.id: x.name for x in s.query(Venue).all()}

        def row(lbl, field):
            return f"<tr><td style='padding:6px;color:#888'>{lbl}</td><td style='padding:6px'>{field}</td></tr>"
        form = (
            f"<h2>改 #{o.id}</h2>"
            f"<form method='post' action='/orders/{oid}/edit'><table>"
            + row("日期", _inp("biz_date", o.biz_date.strftime('%Y-%m-%d') if o.biz_date else '', "2026-06-20"))
            + row("艺人", _inp("artist", art.get(o.artist_id, '')))
            + row("场所", _inp("venue", ven.get(o.venue_id, '')))
            + row("包厢", _inp("room", o.room))
            + row("妈咪", _inp("mama", mam.get(o.mama_id, '')) + " <span style='color:#888;font-size:12px'>(留空=自单)</span>")
            + row("分成档", _sel("mode", MODES, o.mode))
            + row("现金流向", _sel("flow", FLOWS, o.flow or ''))
            + row("挂账 K", _inp("credit_k", int(o.credit_k or 0)))
            + row("现金 M", _inp("cash_m", int(o.cash_m or 0)))
            + row("门票 O", _inp("ticket_o", int(o.ticket_o or 0)))
            + row("客人", _inp("customer", o.customer))
            + row("上班", _inp("start_time", o.start_time))
            + row("下班", _inp("end_time", o.end_time))
            + row("备注", _inp("remark", o.remark))
            + "</table><div style='margin-top:14px'>"
            + "<button class='btn g' type='submit'>保存</button> "
            + f"<a class='btn' href='/orders'>取消</a></div></form>"
            + f"<form method='post' action='/orders/{oid}/void' style='margin-top:16px'>"
            + f"<button class='btn x' onclick=\"return confirm('确认作废 #{o.id}? 作废后不计入任何报表')\">⚠️ 作废这单</button></form>")
        return page("改单", card(form), "orders")
    finally:
        s.close()


@app.post("/orders/{oid}/edit")
def order_edit_save(oid: int, user=Depends(auth),
                    biz_date: str = Form(""), artist: str = Form(""), venue: str = Form(""),
                    room: str = Form(""), mama: str = Form(""), mode: str = Form("标准"),
                    flow: str = Form(""), credit_k: float = Form(0), cash_m: float = Form(0),
                    ticket_o: float = Form(0), customer: str = Form(""),
                    start_time: str = Form(""), end_time: str = Form(""), remark: str = Form("")):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o:
            try:
                o.biz_date = _dt.strptime(biz_date, "%Y-%m-%d").date() if biz_date else o.biz_date
            except Exception:
                pass
            a = s.query(Artist).filter_by(name=artist.strip()).first() if artist.strip() else None
            v = s.query(Venue).filter_by(name=venue.strip()).first() if venue.strip() else None
            m = s.query(Mama).filter_by(name=mama.strip()).first() if mama.strip() else None
            if a: o.artist_id = a.id
            if v: o.venue_id = v.id
            o.mama_id = m.id if m else None
            o.room = room.strip() or None
            o.mode = mode if mode in MODES else o.mode
            o.flow = flow.strip() or None
            o.credit_k = credit_k or 0
            o.cash_m = cash_m or 0
            o.ticket_o = ticket_o or 0
            o.customer = customer.strip() or None
            o.start_time = start_time.strip()[:8]
            o.end_time = end_time.strip()[:8]
            o.remark = remark.strip() or None
            # 金额/档/流向改了 → 重派生两轨态, 保留已结/已
            o.credit_status, o.cash_status = ST.derive_status(
                credit_k=o.credit_k, cash_m=o.cash_m, mode=o.mode, flow=o.flow,
                credit_paid=(o.credit_status == ST.CREDIT_PAID),
                cash_settled=(o.cash_status == ST.CASH_DONE),
                void=(o.status != "已审核"))
            s.add(OperationLog(action="改单", target=f"order#{oid}",
                               detail=f"挂{int(o.credit_k)}/现{int(o.cash_m)}/票{int(o.ticket_o)}/{o.mode}"))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/orders", status_code=303)


@app.post("/orders/{oid}/void")
def order_void(oid: int, user=Depends(auth)):
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o:
            o.status = "作废"
            s.add(OperationLog(action="作废单", target=f"order#{oid}"))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/orders", status_code=303)


@app.post("/orders/{oid}/credit_toggle")
def credit_toggle(oid: int, user=Depends(auth)):
    """挂账态 待结/部分 ↔ 已结 切换 (直结/无K 不可切)。"""
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o and o.credit_status in (ST.CREDIT_DUE, ST.CREDIT_PART, ST.CREDIT_PAID):
            if o.credit_status == ST.CREDIT_PAID:
                o.credit_status = ST.CREDIT_DUE; o.credit_paid_date = None
            else:
                o.credit_status = ST.CREDIT_PAID; o.credit_paid_date = date.today()
            s.add(OperationLog(action="挂账结款", target=f"order#{oid}", detail=o.credit_status))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/orders", status_code=303)


@app.post("/orders/{oid}/cash_toggle")
def cash_toggle(oid: int, user=Depends(auth)):
    """现金态 待 ↔ 已 切换 (直结/无M 不可切)。"""
    s = get_session()
    try:
        o = s.get(Order, oid)
        if o and o.cash_status in (ST.CASH_DUE, ST.CASH_DONE):
            if o.cash_status == ST.CASH_DONE:
                o.cash_status = ST.CASH_DUE; o.cash_settle_date = None
            else:
                o.cash_status = ST.CASH_DONE; o.cash_settle_date = date.today()
            s.add(OperationLog(action="现金结算", target=f"order#{oid}", detail=o.cash_status))
            s.commit()
    finally:
        s.close()
    return RedirectResponse("/orders", status_code=303)
