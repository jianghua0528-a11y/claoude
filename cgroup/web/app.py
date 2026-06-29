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

from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..db.session import get_session, init_db
from ..db.models import Order, Artist, Mama, Venue, ReviewItem, OperationLog
from ..core.settle import settle_db

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
    nav = (lk("/", "看板", "home") + lk("/profit", "利润分红", "profit")
           + lk("/billing", "结款", "billing") + lk("/finance", "财务", "finance")
           + lk("/review", "审核队列", "review")
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
        wages, recv = {}, {}
        for o in orders:
            r = settle_db(o)
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


# ───────────────────────── 利润 + 分红 ─────────────────────────
@app.get("/profit", response_class=HTMLResponse)
def profit_page(user=Depends(auth), year: int = 0, month: int = 0):
    from ..core.profit import profit_summary
    s = get_session()
    try:
        if not (year and month):
            last = (s.query(Order).filter(Order.status == "已审核")
                    .order_by(Order.biz_date.desc()).first())
            d = last.biz_date if last and last.biz_date else date.today()
            year, month = d.year, d.month
        p = profit_summary(s, year, month)
    finally:
        s.close()

    def f2(n):
        return f"{n:,.2f}"
    sign = lambda n: ("neg" if n < 0 else "")

    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    switch = (f"<p class=muted><a href='/profit?year={py}&month={pm}'>← 上月</a> &nbsp; "
              f"<b>{year}年{month}月</b> &nbsp; "
              f"<a href='/profit?year={ny}&month={nm}'>下月 →</a></p>")

    chain = ("<h2>公司利润链（MYR）</h2>" + switch +
             "<div class=kpi>"
             f"<div><div class=n>{fmt(p['gross'])}</div><div class=l>公司毛</div></div>"
             f"<div><div class=n>{fmt(p['lodging'])}</div><div class=l>住宿净</div></div>"
             f"<div><div class='n neg'>-{fmt(p['commission'])}</div><div class=l>经纪人提成</div></div>"
             f"<div><div class='n neg'>-{fmt(p['costs'])}</div><div class=l>运营成本</div></div>"
             f"<div><div class=n>{fmt(p['operating'])}</div><div class=l>经营利润</div></div></div>"
             "<p class=muted style='margin-top:12px'>"
             f"经营利润 {f2(p['operating'])} × 结算率 {p['settle_rate']} "
             f"+ 汇差 {f2(p['spread'])} = <b>总利润 {f2(p['total'])} RMB</b></p>"
             + (f"<p class=muted>坏账 {f2(p['bad_debt'])}（单独列示，不冲利润）</p>"
                if p.get('bad_debt') else ""))

    prows = "".join(
        f"<tr><td>{name}</td><td class=r>{fmt(perf)}</td>"
        f"<td class='r neg'>-{fmt(p['commission_per'].get(name, 0))}</td></tr>"
        for name, perf in sorted(p["perf"].items(), key=lambda x: -x[1]))
    ptab = ("<h2>各经纪人业绩 / 提成</h2><table>"
            "<tr><th>经纪人</th><th class=r>业绩(工价)</th><th class=r>提成</th></tr>"
            f"{prows or '<tr><td colspan=3 class=muted>本月无业绩单</td></tr>'}</table>")

    drows = "".join(
        f"<tr><td>{name}</td><td class='r {sign(v)}'>{f2(v)}</td></tr>"
        for name, v in sorted(p["dividends"].items(), key=lambda x: -x[1]))
    dtab = ("<h2>三股东分红（RMB · 按业绩占比全额分）</h2><table>"
            "<tr><th>股东</th><th class=r>分红(RMB)</th></tr>"
            f"{drows or '<tr><td colspan=2 class=muted>本月无可分利润</td></tr>'}</table>")

    return page("利润分红", card(chain) + card(ptab) + card(dtab), "profit")


# ───────────────────────── 结款 (Block G 单一真相源) ─────────────────────────
@app.get("/billing", response_class=HTMLResponse)
def billing_page(user=Depends(auth), msg: str = ""):
    from ..core.billing import order_receivable
    from ..db.models import Payment
    s = get_session()
    try:
        art = {x.id: x.name for x in s.query(Artist).all()}
        mam = {x.id: x.name for x in s.query(Mama).all()}
        # 待结挂账单
        pend = (s.query(Order).filter(Order.status == "已审核",
                Order.settle_status == "待结", Order.credit_k > 0)
                .order_by(Order.mama_id, Order.biz_date).all())
        rows = ""
        for o in pend:
            recv = order_receivable(o)
            rows += (f"<tr><td><input type=checkbox name=oid value='{o.order_id or ''}'"
                     f"{' disabled' if not o.order_id else ''}></td>"
                     f"<td>{o.order_id or '—'}</td>"
                     f"<td>{o.biz_date.strftime('%m/%d') if o.biz_date else '—'}</td>"
                     f"<td>{mam.get(o.mama_id, '自单')}</td><td>{art.get(o.artist_id, '?')}</td>"
                     f"<td class=r>{fmt(o.credit_k)}</td><td class=r>{fmt(o.ticket_o)}</td>"
                     f"<td class=r>{fmt(recv)}</td></tr>")
        flash = f"<p class=muted>{msg}</p>" if msg else ""
        form = (flash +
                "<h2>待结挂账单（勾选 → 登记收款冲账 → 标记已结）</h2>"
                "<form method='post' action='/billing/pay'>"
                "<table><tr><th></th><th>工单号</th><th>日期</th><th>妈咪</th><th>艺人</th>"
                "<th class=r>挂账</th><th class=r>门票</th><th class=r>应收C组</th></tr>"
                f"{rows or '<tr><td colspan=8 class=muted>没有待结挂账单</td></tr>'}</table>"
                "<div style='margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
                "收款日期 " + _inp("pay_date", date.today().isoformat()) +
                " 妈咪 " + _inp("mama", "") +
                " 金额 " + _inp("amount", "") +
                " <button class='btn g' type='submit'>登记收款</button></div></form>")
        # 近期收款
        pays = s.query(Payment).order_by(Payment.id.desc()).limit(10).all()
        prows = "".join(
            f"<tr><td>{p.pay_date.strftime('%m/%d') if p.pay_date else '—'}</td>"
            f"<td>{mam.get(p.mama_id, '—')}</td><td class=r>{fmt(p.amount)}</td>"
            f"<td class=muted style='font-size:12px'>{p.covers or ''}</td></tr>" for p in pays)
        ptab = ("<h2>近期收款</h2><table><tr><th>日期</th><th>妈咪</th><th class=r>金额</th>"
                f"<th>冲销工单</th></tr>{prows or '<tr><td colspan=4 class=muted>暂无</td></tr>'}</table>")
        return page("结款", card(form) + card(ptab), "billing")
    finally:
        s.close()


@app.post("/billing/pay")
def billing_pay(user=Depends(auth), pay_date: str = Form(""), amount: float = Form(0),
                mama: str = Form(""), oid: list = Form(default=[])):
    from ..core.billing import apply_payment
    from ..db.models import Payment
    s = get_session()
    try:
        oids = [x for x in oid if x]
        if not oids:
            return RedirectResponse("/billing?msg=未勾选工单", status_code=303)
        try:
            pd = _dt.strptime(pay_date, "%Y-%m-%d").date() if pay_date else date.today()
        except Exception:
            pd = date.today()
        m = s.query(Mama).filter_by(name=mama.strip()).first() if mama.strip() else None
        pay = Payment(pay_date=pd, mama_id=m.id if m else None, amount=amount or 0)
        res = apply_payment(s, pay, oids)
        s.add(OperationLog(action="结款", target=",".join(res.marked),
                           detail=f"收{amount:g} 应收{res.expected:g}" + (f" ⚠{res.flag}" if res.flag else "")))
        s.commit()
        msg = res.flag or f"已结 {len(res.marked)} 单, 收款 {amount:g} (应收 {res.expected:g})"
    finally:
        s.close()
    return RedirectResponse(f"/billing?msg={msg}", status_code=303)


# ───────────────────────── 财务录入 (成本/住宿/坏账/换汇) ─────────────────────────
def _today():
    return date.today().isoformat()


@app.get("/finance", response_class=HTMLResponse)
def finance_page(user=Depends(auth), msg: str = ""):
    from ..db.models import Expense, Lodging, BadDebt, Fx
    s = get_session()
    try:
        def form(title, action, fields):
            inner = "".join(f"{lbl} {_inp(nm, val, ph)} " for lbl, nm, val, ph in fields)
            return (f"<h2>{title}</h2><form method='post' action='{action}' "
                    f"style='display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
                    f"{inner}<button class='btn g' type='submit'>添加</button></form>")

        expense = form("运营成本", "/finance/expense", [
            ("日期", "spend_date", _today(), ""), ("类别", "category", "", "场地/伙食/广告"),
            ("金额", "amount", "", "")])
        lodging = form("住宿净收入", "/finance/lodging", [
            ("日期", "record_date", _today(), ""), ("净收入", "net_income", "", ""),
            ("备注", "note", "", "")])
        baddebt = form("坏账", "/finance/baddebt", [
            ("日期", "record_date", _today(), ""), ("金额", "amount", "", ""),
            ("工单号", "order_id", "", "可空"), ("备注", "note", "", "")])
        fx = form("换汇流水", "/finance/fx", [
            ("日期", "fx_date", _today(), ""), ("换出币种", "out_ccy", "MYR", ""),
            ("换出额", "out_amount", "", ""), ("实收RMB", "in_rmb", "", "")])

        def recent(rows):
            return "".join(rows) or "<tr><td class=muted>暂无</td></tr>"
        ex = recent([f"<tr><td>{e.spend_date}</td><td>{e.category}</td><td class=r>{fmt(e.amount)}</td></tr>"
                     for e in s.query(Expense).order_by(Expense.id.desc()).limit(6)])
        lo = recent([f"<tr><td>{x.record_date}</td><td class=r>{fmt(x.net_income)}</td><td>{x.note or ''}</td></tr>"
                     for x in s.query(Lodging).order_by(Lodging.id.desc()).limit(6)])
        bd = recent([f"<tr><td>{x.record_date}</td><td class=r>{fmt(x.amount)}</td><td>{x.order_id or ''} {x.note or ''}</td></tr>"
                     for x in s.query(BadDebt).order_by(BadDebt.id.desc()).limit(6)])
        fr = recent([f"<tr><td>{x.fx_date}</td><td>{x.out_ccy} {fmt(x.out_amount)}→{fmt(x.in_rmb)}RMB</td></tr>"
                     for x in s.query(Fx).order_by(Fx.id.desc()).limit(6)])
        flash = f"<p class=muted>{msg}</p>" if msg else ""
        body = (card(flash + expense + "<table style='margin-top:10px'>" + ex + "</table>")
                + card(lodging + "<table style='margin-top:10px'>" + lo + "</table>")
                + card(baddebt + "<table style='margin-top:10px'>" + bd + "</table>")
                + card(fx + "<table style='margin-top:10px'>" + fr + "</table>"))
        return page("财务", body, "finance")
    finally:
        s.close()


def _date(s_, default=None):
    try:
        return _dt.strptime(s_, "%Y-%m-%d").date() if s_ else (default or date.today())
    except Exception:
        return default or date.today()


def _money(x):
    try:
        return float(x) if str(x).strip() else 0.0
    except ValueError:
        return 0.0


@app.post("/finance/expense")
def finance_expense(user=Depends(auth), spend_date: str = Form(""), category: str = Form(""),
                    amount: str = Form("")):
    from ..db.models import Expense
    s = get_session()
    try:
        s.add(Expense(spend_date=_date(spend_date), category=category.strip() or "其他",
                      amount=_money(amount)))
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/finance?msg=已加运营成本", status_code=303)


@app.post("/finance/lodging")
def finance_lodging(user=Depends(auth), record_date: str = Form(""), net_income: str = Form(""),
                    note: str = Form("")):
    from ..db.models import Lodging
    s = get_session()
    try:
        s.add(Lodging(record_date=_date(record_date), net_income=_money(net_income),
                      note=note.strip() or None))
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/finance?msg=已加住宿净收入", status_code=303)


@app.post("/finance/baddebt")
def finance_baddebt(user=Depends(auth), record_date: str = Form(""), amount: str = Form(""),
                    order_id: str = Form(""), note: str = Form("")):
    from ..db.models import BadDebt
    s = get_session()
    try:
        s.add(BadDebt(record_date=_date(record_date), amount=_money(amount),
                      order_id=order_id.strip() or None, note=note.strip() or None))
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/finance?msg=已加坏账", status_code=303)


@app.post("/finance/fx")
def finance_fx(user=Depends(auth), fx_date: str = Form(""), out_ccy: str = Form("MYR"),
               out_amount: str = Form(""), in_rmb: str = Form("")):
    from ..db.models import Fx
    s = get_session()
    try:
        s.add(Fx(fx_date=_date(fx_date), out_ccy=out_ccy.strip() or "MYR",
                 out_amount=_money(out_amount), in_rmb=_money(in_rmb)))
        s.commit()
    finally:
        s.close()
    return RedirectResponse("/finance?msg=已加换汇流水", status_code=303)


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

MODES = ["标准", "无水单", "代收无水", "自单", "自定义"]
FLOWS = ["", "A", "B", "D", "E"]


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
                    f"<td class=r>{fmt(o.ticket_o)}</td><td>{o.preset}</td>"
                    f"<td>{o.customer or '—'}</td>"
                    f"<td><a class=btn href='/orders/{o.id}/edit'>改</a></td></tr>")
        body = (f"<h2>订单（改单 / 作废）</h2>"
                f"<form method='get' style='margin-bottom:12px'>"
                f"<input name='q' value='{q}' placeholder='搜艺人/妈咪/客人/场所' style='padding:7px;border:1px solid #F4C0D1;border-radius:6px'> "
                f"<button class='btn'>搜</button></form>"
                f"<table><tr><th>#</th><th>日期</th><th>艺人</th><th>场所</th><th>妈咪</th>"
                f"<th class=r>挂账</th><th class=r>现金</th><th class=r>门票</th><th>档</th><th>客人</th><th></th></tr>{trs}</table>")
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
            + row("分成预设", _sel("preset", MODES, o.preset))
            + row("自定义比例", _inp("cust_a", o.cust_a, "艺") + " " + _inp("cust_m", o.cust_m, "妈")
                  + " " + _inp("cust_c", o.cust_c, "公") + " <span style='color:#888;font-size:12px'>(仅自定义档填)</span>")
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
                    room: str = Form(""), mama: str = Form(""), preset: str = Form("标准"),
                    cust_a: str = Form(""), cust_m: str = Form(""), cust_c: str = Form(""),
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
            o.preset = preset if preset in MODES else o.preset
            if o.preset == "自定义":
                def _f(x):
                    try:
                        return float(x) if str(x).strip() else None
                    except ValueError:
                        return None
                o.cust_a, o.cust_m, o.cust_c = _f(cust_a), _f(cust_m), _f(cust_c)
            else:
                o.cust_a = o.cust_m = o.cust_c = None
            o.flow = flow.strip() or None
            o.credit_k = credit_k or 0
            o.cash_m = cash_m or 0
            o.ticket_o = ticket_o or 0
            o.customer = customer.strip() or None
            o.start_time = start_time.strip()[:8]
            o.end_time = end_time.strip()[:8]
            o.remark = remark.strip() or None
            s.add(OperationLog(action="改单", target=f"order#{oid}",
                               detail=f"挂{int(o.credit_k)}/现{int(o.cash_m)}/票{int(o.ticket_o)}/{o.preset}"))
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
