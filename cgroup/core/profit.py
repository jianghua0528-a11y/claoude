"""
C组 利润 + 分红 (宪法 v1.0 · Block I)  ·  profit.py

公司利润链:
  公司毛(Σ每单公司净) + 住宿净收入 − 经纪人提成 − 运营成本 = 经营利润   (MYR)
  经营利润 × 结算率 + 汇差(Block E)                       = 总利润     (RMB)

口径要点:
  · 经纪人提成全计入成本(4 人, 含非股东老杨), 按各自名下艺人业绩(工价)算。
  · 坏账单独列示, **不冲利润**。
  · 汇差 100% 公司隐性收入, 单独加在最后(已是 RMB)。
  · 分红仅三股东(阿星/阿豪/老方), 按各自名下艺人业绩占比**全额分**(非平分);
    老杨(非股东)名下艺人产生的利润也归三股东按业绩比例分。
"""
from typing import Dict, Optional, Tuple


def broker_commission(perf_by_broker: Dict, pct_by_broker: Dict) -> Tuple[float, Dict]:
    """经纪人提成 = Σ 各经纪人(业绩 × 提成率)。返回 (合计, {broker: 提成})。"""
    per = {b: round(perf_by_broker[b] * pct_by_broker[b], 2) for b in perf_by_broker}
    return round(sum(per.values()), 2), per


def operating_profit(company_gross: float, lodging_net: float = 0.0,
                     commission: float = 0.0, costs: float = 0.0,
                     bad_debt: float = 0.0) -> float:
    """经营利润 (MYR)。坏账单独列示不冲利润, 故 bad_debt 不参与扣减。"""
    return round(company_gross + lodging_net - commission - costs, 2)


def total_profit(operating_profit_myr: float, fx_spread_rmb: float,
                 settle_rate: float) -> float:
    """总利润 (RMB) = 经营利润 × 结算率 + 汇差。"""
    return round(operating_profit_myr * settle_rate + fx_spread_rmb, 2)


def dividends(total_profit_rmb: float, perf_by_shareholder: Dict) -> Dict:
    """三股东分红: 总利润按各自业绩占比全额分(非平分)。"""
    base = sum(perf_by_shareholder.values())
    if base <= 0:
        return {b: 0.0 for b in perf_by_shareholder}
    return {b: round(total_profit_rmb * perf_by_shareholder[b] / base, 2)
            for b in perf_by_shareholder}


# ─────────────────────── DB 聚合 (经结算引擎) ───────────────────────
def _month_orders(session, year: int, month: int):
    from ..db.models import Order
    return [o for o in session.query(Order).filter(Order.status == "已审核").all()
            if o.biz_date and o.biz_date.year == year and o.biz_date.month == month]


def company_gross_from_orders(session, year: int, month: int) -> float:
    """公司毛 = Σ 每单公司净 (Σ c×wp), 经引擎算。"""
    from .settle import settle_db
    return round(sum(settle_db(o).company_net for o in _month_orders(session, year, month)), 2)


def performance_by_broker(session, year: int, month: int) -> Dict:
    """{broker_id: 业绩(工价合计)}; 仅算业绩单(标准/自单, 公司有份), 排除表外/无份单。"""
    from .settle import settle_db
    from ..db.models import Artist
    ab = {a.id: a.broker_id for a in session.query(Artist).all()}
    perf: Dict = {}
    for o in _month_orders(session, year, month):
        s = settle_db(o)
        if not s.counts_performance:
            continue
        b = ab.get(o.artist_id)
        perf[b] = perf.get(b, 0.0) + s.wp
    return {b: round(v, 2) for b, v in perf.items()}


def profit_summary(session, year: int, month: int, *,
                   settle_rate: float = 1.65, rates: Optional[dict] = None) -> Dict:
    """某月利润链 + 分红 + 汇差 全聚合 (看板用)。
    住宿净收入 / 坏账暂无数据源, 记 0; 运营成本取 Expense 当月合计。"""
    from ..db.models import Broker, Expense, Lodging, BadDebt
    from .fx import monthly_spread, RATES_2026_05

    def _in_month(d):
        return d and d.year == year and d.month == month

    rates = rates or RATES_2026_05
    brokers = {b.id: b for b in session.query(Broker).all()}

    gross = company_gross_from_orders(session, year, month)
    perf = performance_by_broker(session, year, month)
    perf_named = {brokers[b].name: v for b, v in perf.items() if b in brokers}
    pct = {b: brokers[b].pct for b in perf if b in brokers}
    comm_total, comm_per = broker_commission({b: perf[b] for b in pct}, pct)

    costs = round(sum(e.amount for e in session.query(Expense).all()
                      if _in_month(e.spend_date)), 2)
    lodging = round(sum(x.net_income for x in session.query(Lodging).all()
                        if _in_month(x.record_date)), 2)
    bad_debt = round(sum(x.amount for x in session.query(BadDebt).all()
                         if _in_month(x.record_date)), 2)
    op = operating_profit(gross, lodging, comm_total, costs, bad_debt=bad_debt)
    spread = monthly_spread(session, year, month, rates)
    total = total_profit(op, spread, settle_rate)

    share_perf = {b: perf[b] for b in perf if b in brokers and brokers[b].is_shareholder}
    div = dividends(total, share_perf)

    return dict(
        year=year, month=month,
        gross=gross, lodging=lodging, commission=comm_total, costs=costs,
        bad_debt=bad_debt,
        operating=op, spread=spread, settle_rate=settle_rate, total=total,
        perf=perf_named,
        commission_per={brokers[b].name: v for b, v in comm_per.items()},
        dividends={brokers[b].name: v for b, v in div.items()},
    )
