"""
业务查询聚合  ·  queries.py
机器人命令 + 网页看板 + 报表共用。全部经 settlement 引擎算, 时间过滤下推到 SQL。
"""
from calendar import monthrange
from datetime import date

from ..db.models import Order, Advance, Expense, MamaSettlement, ReviewItem
from .settlement import Order as EO, compute
from . import status as ST


def month_range(year, month):
    """某月第一天 / 最后一天。"""
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def month_key(year, month):
    return f"{year}-{month:02d}"


def parse_month(s, default=None):
    """'2026-06' → (2026, 6); 不合法返回 default。"""
    try:
        y, m = str(s).split("-")[:2]
        y, m = int(y), int(m)
        if 1 <= m <= 12 and 2000 <= y <= 2100:
            return y, m
    except (ValueError, AttributeError):
        pass
    return default


def calc(o):
    """库里的 Order → 引擎结果。"""
    return compute(EO(K=o.credit_k or 0, M=o.cash_m or 0, O=o.ticket_o or 0,
                      mode=o.mode, flow=o.flow, wp=o.wp))


def orders_q(session, artist_id=None, mama_id=None, start=None, end=None):
    q = session.query(Order).filter(Order.status == "已审核")
    if artist_id:
        q = q.filter(Order.artist_id == artist_id)
    if mama_id:
        q = q.filter(Order.mama_id == mama_id)
    if start:
        q = q.filter(Order.biz_date >= start)
    if end:
        q = q.filter(Order.biz_date <= end)
    return q


def artist_deductions(session, artist_name, year, month):
    """艺人某月待扣的 预支/罚款 (related_month = 该月)。"""
    return (session.query(Advance)
            .filter(Advance.subject_type == "艺人", Advance.subject_name == artist_name,
                    Advance.status == "待扣", Advance.related_month == month_key(year, month))
            .order_by(Advance.id).all())


def artist_summary(session, artist_id, year, month, artist_name=None):
    """某艺人某月: 单数 / 业绩(K+M) / 门票 / 应发(月底应结) / 待扣 / 实发。"""
    perf = tickets = wage = 0.0
    n = 0
    start, end = month_range(year, month)
    for o in orders_q(session, artist_id=artist_id, start=start, end=end):
        r = calc(o)
        perf += (o.credit_k or 0) + (o.cash_m or 0)
        tickets += o.ticket_o or 0
        wage += r.artist_month_end
        n += 1
    deduct = 0.0
    if artist_name:
        deduct = sum(a.amount for a in artist_deductions(session, artist_name, year, month))
    return dict(n=n, perf=perf, tickets=tickets, wage=wage, deduct=deduct, net=wage - deduct)


def mama_summary(session, mama_id, start=None, end=None):
    """某妈咪团队名下(可限时段): 单明细 + 挂账/门票/应结C组。"""
    K = O = recv = 0.0
    rows = []
    for o in orders_q(session, mama_id=mama_id, start=start, end=end).order_by(Order.biz_date):
        r = calc(o)
        K += o.credit_k or 0
        O += o.ticket_o or 0
        recv += r.mama_receivable - r.mama_rebate
        rows.append((o, r))
    return dict(n=len(rows), K=K, O=O, recv=recv, rows=rows)


def day_summary(session, day):
    """某天所有单: 单数 / 挂账 / 现金 / 门票。"""
    rows = orders_q(session, start=day, end=day).order_by(Order.id).all()
    return dict(n=len(rows), K=sum(o.credit_k or 0 for o in rows),
                M=sum(o.cash_m or 0 for o in rows), O=sum(o.ticket_o or 0 for o in rows),
                rows=rows)


def dashboard_stats(session, year=None, month=None):
    """看板数据。给 year+month 则限当月(业绩/工资); 待清三轨始终看全部(欠款不分月)。"""
    start = end = None
    if year and month:
        start, end = month_range(year, month)
    period = orders_q(session, start=start, end=end).all()
    st = dict(n=len(period), SK=0.0, SM=0.0, SO=0.0, artist=0.0, mama=0.0, company=0.0,
              wages={}, due_mama={}, col_cnt=0, col_sum=0.0, sk_cnt=0, sk_sum=0.0)
    for o in period:
        r = calc(o)
        st["SK"] += o.credit_k or 0; st["SM"] += o.cash_m or 0; st["SO"] += o.ticket_o or 0
        st["artist"] += r.artist_net; st["mama"] += r.mama_net; st["company"] += r.company_net
        if o.artist_id:
            st["wages"][o.artist_id] = st["wages"].get(o.artist_id, 0) + r.artist_month_end
    for o in orders_q(session).all():
        r = calc(o)
        if o.mama_id and ST.is_due_from_mama(o.credit_status):
            st["due_mama"][o.mama_id] = st["due_mama"].get(o.mama_id, 0) + r.mama_receivable
        if ST.is_due_to_artist(o.cash_status, o.flow):
            st["col_cnt"] += 1; st["col_sum"] += o.cash_m or 0
        if ST.is_due_clawback(o.cash_status, o.flow):
            st["sk_cnt"] += 1; st["sk_sum"] += o.cash_m or 0
    # 财务流水 (限当月)
    eq = session.query(Expense)
    sq = session.query(MamaSettlement)
    if start:
        eq = eq.filter(Expense.spend_date >= start, Expense.spend_date <= end)
        sq = sq.filter(MamaSettlement.settled_at >= start, MamaSettlement.settled_at <= end)
    st["expense"] = sum(e.amount for e in eq.all())
    st["mama_paid"] = sum(x.amount for x in sq.all())
    aq = session.query(Advance).filter(Advance.status == "待扣")
    st["adv_due"] = sum(a.amount for a in aq.all())
    st["pending"] = session.query(ReviewItem).filter_by(status="待审").count()
    st["profit"] = st["company"] - st["expense"]
    return st
