"""
Block I 利润分红 + §13 5月 golden test  ·  test_profit.py
"""
from datetime import date

import pytest

from cgroup.core.profit import (
    broker_commission, operating_profit, total_profit, dividends,
    company_gross_from_orders, performance_by_broker,
)

# ── §13 五月实测基准 (单位 MYR / RMB) ──
PERF = {"阿星": 548150, "阿豪": 184900, "老方": 187000, "老杨": 183400}   # 业绩(工价)
PCT = {"阿星": 0.07, "阿豪": 0.07, "老方": 0.07, "老杨": 0.10}            # 提成率
SHARE = {"阿星": 548150, "阿豪": 184900, "老方": 187000}                  # 三股东
SETTLE_RATE = 1.65
FX_SPREAD = 7129.97


# ─────────────────────── §13 公司利润链 golden ───────────────────────
def test_total_performance():
    assert sum(PERF.values()) == 1103450


def test_commission_golden():
    total, per = broker_commission(PERF, PCT)
    assert total == pytest.approx(82743.5)
    assert per["阿星"] == pytest.approx(38370.5)
    assert per["老杨"] == pytest.approx(18340.0)


def test_operating_profit_golden():
    # 公司毛 110,000 + 住宿 11,400 − 提成 82,743.5 − 成本 15,336 = 23,320.5
    op = operating_profit(company_gross=110000, lodging_net=11400,
                          commission=82743.5, costs=15336, bad_debt=0)
    assert op == pytest.approx(23320.5)


def test_total_profit_golden():
    # 23,320.5 × 1.65 + 7,129.97 = 45,608.79 RMB
    tp = total_profit(23320.5, FX_SPREAD, SETTLE_RATE)
    assert tp == pytest.approx(45608.79, abs=0.01)


def test_dividends_golden():
    tp = total_profit(23320.5, FX_SPREAD, SETTLE_RATE)
    div = dividends(tp, SHARE)
    assert div["阿星"] == pytest.approx(27172.94, abs=0.05)
    assert div["阿豪"] == pytest.approx(9165.88, abs=0.05)
    assert div["老方"] == pytest.approx(9269.98, abs=0.05)
    # 全额分, 不留储备
    assert sum(div.values()) == pytest.approx(tp, abs=0.05)


def test_yang_not_in_dividends():
    # 老杨非股东不分; 但其名下艺人利润已含在总利润里(按三股东业绩比例分掉)
    tp = total_profit(23320.5, FX_SPREAD, SETTLE_RATE)
    div = dividends(tp, SHARE)
    assert "老杨" not in div


# ─────────────────────── 纯函数边界 ───────────────────────
def test_bad_debt_does_not_reduce_profit():
    op_no = operating_profit(100000, 0, 0, 0, bad_debt=0)
    op_bad = operating_profit(100000, 0, 0, 0, bad_debt=5000)
    assert op_no == op_bad == 100000   # 坏账不冲利润


def test_dividends_zero_base():
    assert dividends(1000, {"a": 0, "b": 0}) == {"a": 0.0, "b": 0.0}


# ─────────────────────── DB 聚合 (经引擎) ───────────────────────
@pytest.fixture(scope="module")
def db():
    from cgroup.db.session import init_db, get_session
    from cgroup.db.models import Broker, Artist, Venue, Order
    init_db()
    s = get_session()
    b1 = Broker(name="提成测A", pct=0.07, is_shareholder=True)
    b2 = Broker(name="提成测B", pct=0.10, is_shareholder=False)
    s.add_all([b1, b2]); s.flush()
    a1 = Artist(name="利润艺人1", broker_id=b1.id)
    a2 = Artist(name="利润艺人2", broker_id=b2.id)
    ven = Venue(name="利润场所")
    s.add_all([a1, a2, ven]); s.flush()
    D = date(2026, 7, 10)
    s.add_all([
        # a1: 标准挂账 wp=3000 (业绩, 公司净300); 无水单 wp=5000 (不算业绩/不进公司毛)
        Order(biz_date=D, artist_id=a1.id, venue_id=ven.id, mode="标准",
              credit_k=3000, wp=3000, status="已审核"),
        Order(biz_date=D, artist_id=a1.id, venue_id=ven.id, mode="直结",
              credit_k=5000, wp=5000, status="已审核"),
        # a2: 自单 wp=2000 (业绩, 公司净200)
        Order(biz_date=D, artist_id=a2.id, venue_id=ven.id, mode="自单",
              cash_m=2000, wp=2000, flow="A", status="已审核"),
    ])
    s.commit()
    return dict(s=s, b1=b1.id, b2=b2.id)


def test_company_gross_from_orders(db):
    # 公司净: 标准 300 + 无水单 0 + 自单 200 = 500
    assert company_gross_from_orders(db["s"], 2026, 7) == pytest.approx(500)


def test_performance_by_broker(db):
    perf = performance_by_broker(db["s"], 2026, 7)
    # 无水单不算业绩 → b1 只有标准 3000; b2 自单 2000
    assert perf[db["b1"]] == pytest.approx(3000)
    assert perf[db["b2"]] == pytest.approx(2000)
