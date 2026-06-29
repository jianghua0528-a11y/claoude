"""
报表组装  ·  build.py
从库取数 → 过引擎 → 渲染成 PNG bytes。机器人命令 + 定时器调这里。
"""
from datetime import date

from ..db.models import Order, Artist, Venue, Mama
from ..core.settle import settle_db
from . import render

WEEK = "一二三四五六日"


def _maps(session):
    return ({a.id: a.name for a in session.query(Artist).all()},
            {v.id: v.name for v in session.query(Venue).all()},
            {m.id: m.name for m in session.query(Mama).all()})


def daily_report_png(session, day):
    art, ven, mam = _maps(session)
    orders = (session.query(Order).filter(Order.biz_date == day, Order.status == "已审核")
              .order_by(Order.artist_id, Order.id).all())
    if not orders:
        return None
    rows = [dict(艺人=art.get(o.artist_id, "?"), 场所=ven.get(o.venue_id, ""), 包厢=o.room,
                 妈咪=mam.get(o.mama_id, "自单"), 约=o.booker, K=o.credit_k, M=o.cash_m, O=o.ticket_o,
                 客人=o.customer, 上班=o.start_time, 下班=o.end_time, 流向=o.flow, 备注=o.remark)
            for o in orders]
    return render.render_daily_report(f"{day.month}月{day.day}日", f"周{WEEK[day.weekday()]}", rows)


def mama_statement_png(session, mama_id, start=None, end=None, period_label="对账"):
    art, ven, mam = _maps(session)
    k_rows = []; m_rows = []; TK = TO = TR = 0.0
    for o in (session.query(Order).filter(Order.mama_id == mama_id, Order.status == "已审核")
              .order_by(Order.biz_date).all()):
        if start and (not o.biz_date or o.biz_date < start):
            continue
        if end and (not o.biz_date or o.biz_date > end):
            continue
        r = settle_db(o)
        dd = o.biz_date.strftime("%m/%d") if o.biz_date else "?"
        if o.credit_k > 0:
            k_rows.append(dict(日期=dd, 艺人=art.get(o.artist_id, "?"), 场所=ven.get(o.venue_id, ""),
                               包厢=o.room, 客人=o.customer, K=o.credit_k, O=o.ticket_o, 应收=r.mama_owes_company))
            TK += o.credit_k; TO += o.ticket_o
        if o.cash_m > 0:
            m_rows.append(dict(日期=dd, 艺人=art.get(o.artist_id, "?"), 场所=ven.get(o.venue_id, ""),
                               包厢=o.room, 客人=o.customer, wp=o.wp or o.cash_m, 流向=o.flow, 反水=r.rebate))
            TR += r.rebate
    if not k_rows and not m_rows:
        return None
    totals = dict(挂账=TK, 门票=TO, 反水=TR, 应结=TK * 0.8 + TO - TR)
    return render.render_mama_statement(mam.get(mama_id, "?"), period_label, k_rows, m_rows, totals)


def artist_payslip_png(session, artist_id, year, month):
    art, ven, mam = _maps(session)
    rows = []; direct = []; wage = 0.0; seq = 0
    for o in (session.query(Order).filter(Order.artist_id == artist_id, Order.status == "已审核")
              .order_by(Order.biz_date).all()):
        if not (o.biz_date and o.biz_date.year == year and o.biz_date.month == month):
            continue
        r = settle_db(o)
        dd = o.biz_date.strftime("%m/%d")
        if not r.on_books:
            direct.append(dict(日期=dd, 场所=ven.get(o.venue_id, ""), 包厢=o.room,
                               妈咪=mam.get(o.mama_id, ""), K=o.credit_k))
            continue
        seq += 1
        rows.append(dict(序=seq, 日期=dd, 场所=ven.get(o.venue_id, ""), 包厢=o.room,
                         妈咪=mam.get(o.mama_id, "自单"), 客人=o.customer, K=o.credit_k,
                         wp=(o.wp or o.cash_m or o.credit_k), O=o.ticket_o, 分成=r.artist_payroll))
        wage += r.artist_payroll
    if not rows and not direct:
        return None, 0
    png = render.render_artist_payslip(art.get(artist_id, "?"), f"{year}年{month}月", rows, wage, direct_rows=direct)
    return png, wage
