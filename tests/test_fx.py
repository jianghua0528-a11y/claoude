"""
Block E 汇差回归测试  ·  test_fx.py
覆盖: 单笔汇差(宪法验证例) · 隐含真实率 · 多笔合计 · callable 取率 · DB 月汇总。
"""
from datetime import date

import pytest

from cgroup.core.fx import (
    FxRow, real_rate, row_spread, total_spread, monthly_spread, RATES_2026_05,
)


# ─────────────────────── 宪法验证例 ───────────────────────
def test_constitution_example():
    # 收 10,000 MYR 全换 @1.705 = 17,050; 结算率 1.65 → 汇差 = 17050 − 16500 = 550
    row = FxRow(out_ccy="MYR", out_amount=10000, in_rmb=17050)
    assert real_rate(row) == pytest.approx(1.705)
    assert row_spread(row, {"MYR": 1.65}) == pytest.approx(550)


def test_total_spread_multi():
    rows = [
        FxRow(out_ccy="MYR", out_amount=10000, in_rmb=17050),   # +550
        FxRow(out_ccy="USDT", out_amount=1000, in_rmb=6800),    # 6800 − 1000*6.72 = +80
    ]
    assert total_spread(rows, RATES_2026_05) == pytest.approx(630)


def test_spread_can_be_negative():
    # 真实率低于结算率 → 汇差为负(公司亏)
    row = FxRow(out_ccy="MYR", out_amount=10000, in_rmb=16000)
    assert row_spread(row, {"MYR": 1.65}) == pytest.approx(-500)


# ─────────────────────── callable 取率 (按入账月) ───────────────────────
def test_callable_rate_by_month():
    def lookup(ccy, when):
        # 6 月起 MYR 结算率调到 1.70
        if ccy == "MYR" and when and when.month >= 6:
            return 1.70
        return 1.65
    r_may = FxRow(out_ccy="MYR", out_amount=10000, in_rmb=17050, fx_date=date(2026, 5, 10))
    r_jun = FxRow(out_ccy="MYR", out_amount=10000, in_rmb=17050, fx_date=date(2026, 6, 10))
    assert row_spread(r_may, lookup) == pytest.approx(550)    # 17050 − 16500
    assert row_spread(r_jun, lookup) == pytest.approx(50)     # 17050 − 17000


# ─────────────────────── DB 月汇总 ───────────────────────
def test_monthly_spread_db():
    from cgroup.db.session import init_db, get_session
    from cgroup.db.models import Fx
    init_db()
    s = get_session()
    s.add_all([
        Fx(fx_date=date(2026, 5, 3), out_ccy="MYR", out_amount=10000, in_rmb=17050),   # +550
        Fx(fx_date=date(2026, 5, 20), out_ccy="USDT", out_amount=1000, in_rmb=6800),   # +80
        Fx(fx_date=date(2026, 6, 1), out_ccy="MYR", out_amount=5000, in_rmb=8600),     # 6月, 不计入5月
    ])
    s.commit()
    assert monthly_spread(s, 2026, 5, RATES_2026_05) == pytest.approx(630)
