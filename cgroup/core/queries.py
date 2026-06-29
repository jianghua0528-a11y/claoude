"""
业务查询聚合  ·  queries.py
机器人命令 + 网页看板共用。全部经 settle 引擎(宪法 v1.0)算。
"""
from datetime import date

from ..db.models import Order, Artist, Mama
from .settle import settle_db


def _orders(session, **filt):
    q = session.query(Order).filter(Order.status == "已审核")
    if filt.get("artist_id"):
        q = q.filter(Order.artist_id == filt["artist_id"])
    if filt.get("mama_id"):
        q = q.filter(Order.mama_id == filt["mama_id"])
    return q.all()


def artist_summary(session, artist_id, year, month):
    """某艺人某月: 单数 / 业绩(K+M) / 门票 / 应发(月底应结)。"""
    perf = tickets = wage = 0.0
    n = 0
    for o in _orders(session, artist_id=artist_id):
        if not (o.biz_date and o.biz_date.year == year and o.biz_date.month == month):
            continue
        r = settle_db(o)
        perf += o.credit_k + o.cash_m
        tickets += o.ticket_o
        wage += r.artist_payroll
        n += 1
    return dict(n=n, perf=perf, tickets=tickets, wage=wage)


def mama_summary(session, mama_id, start=None, end=None):
    """某妈咪团队名下(可限时段): 单明细 + 挂账/门票/应结C组。"""
    K = O = recv = 0.0
    rows = []
    for o in _orders(session, mama_id=mama_id):
        if start and (not o.biz_date or o.biz_date < start):
            continue
        if end and (not o.biz_date or o.biz_date > end):
            continue
        r = settle_db(o)
        K += o.credit_k
        O += o.ticket_o
        recv += r.mama_owes_company - r.rebate
        rows.append((o, r))
    rows.sort(key=lambda x: (x[0].biz_date or date.min))
    return dict(n=len(rows), K=K, O=O, recv=recv, rows=rows)


def day_summary(session, day):
    """某天所有单: 单数 / 挂账 / 现金 / 门票。"""
    K = M = O = 0.0
    rows = []
    for o in session.query(Order).filter(Order.status == "已审核").all():
        if o.biz_date == day:
            K += o.credit_k
            M += o.cash_m
            O += o.ticket_o
            rows.append(o)
    return dict(n=len(rows), K=K, M=M, O=O, rows=rows)
