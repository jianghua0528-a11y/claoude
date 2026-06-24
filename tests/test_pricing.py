"""
Block F 工时/挂账识别回归测试  ·  test_pricing.py
覆盖: 工时(跨夜) · 底价分档 · 加时(含宪法 worked example) · 档位标价 · reconcile 各分支。
"""
import pytest

from cgroup.core.pricing import (
    work_hours, standard_base, overtime_fee, tier_price, reconcile_credit,
    TIER_PRICE, BASE_SHORT, BASE_FLAT,
)


# ─────────────────────── 工时 ───────────────────────
def test_work_hours_same_day():
    assert work_hours("20:00", "23:30") == pytest.approx(3.5)


def test_work_hours_overnight():
    # 宪法例: 22:00 → 04:20 = 6h20m
    assert work_hours("22:00", "04:20") == pytest.approx(6 + 20 / 60)


def test_work_hours_midnight_boundary():
    assert work_hours("23:00", "01:00") == pytest.approx(2.0)


def test_work_hours_bad_input():
    assert work_hours("", "04:20") is None
    assert work_hours("乱", "x") is None


# ─────────────────────── 底价分档 ───────────────────────
@pytest.mark.parametrize("h,base", [
    (2.0, 2500), (3.49, 2500),      # < 3.5
    (3.5, 3000), (5.0, 3000), (5.5, 3000),   # 平 3000 (含 5.0–5.5 不加时)
])
def test_standard_base_flat(h, base):
    assert standard_base(h) == base


def test_standard_base_overtime_worked_example():
    # 6h20m: 触发; 加时基数 1h20m → 向上取整 2h × 150 = 300; 底价 3000+300=3300
    h = 6 + 20 / 60
    assert overtime_fee(h) == 300
    assert standard_base(h) == 3300


# ─────────────────────── 加时 ───────────────────────
@pytest.mark.parametrize("h,fee", [
    (5.5, 0),        # 门槛, 不触发
    (5.6, 150),      # 基数 0.6h → 向上取整 1h
    (6.0, 150),      # 基数 1.0h → 1h
    (6 + 20 / 60, 300),   # 基数 1h20m → 2h
    (7.0, 300),      # 基数 2.0h → 2h
    (7.5, 450),      # 基数 2.5h → 3h
])
def test_overtime_fee(h, fee):
    assert overtime_fee(h) == fee


# ─────────────────────── 档位标价 ───────────────────────
def test_tier_price_fixed():
    assert tier_price("直快") == 9000
    assert tier_price("平快") == 10000
    assert tier_price("职业") == 11000
    assert tier_price("平夜") == 12000


def test_tier_price_standard_needs_hours():
    assert tier_price("标准") is None
    assert tier_price("标准", hours=4.0) == 3000


def test_tier_price_unknown():
    assert tier_price("乱档") is None


# ─────────────────────── reconcile 标准平单 ───────────────────────
def test_reconcile_no_ticket():
    # 报的 = 工时应有底价 → 无门票
    r = reconcile_credit(3000, hours=4.0)
    assert (r.K, r.O) == (3000, 0.0)
    assert r.flag is None


def test_reconcile_split_ticket_ok():
    # 报的 = 底价3000 + 200, 门票行=200 → 拆 K=3000, O=200, 无 flag
    r = reconcile_credit(3200, hours=4.0, ticket_hint=200)
    assert (r.K, r.O) == (3000, 200)
    assert r.flag is None


def test_reconcile_split_ticket_mismatch_flags():
    # 拆出 200 但门票行写 150 → flag
    r = reconcile_credit(3200, hours=4.0, ticket_hint=150)
    assert (r.K, r.O) == (3000, 200)
    assert r.flag is not None and "不符" in r.flag


def test_reconcile_below_base_flags():
    r = reconcile_credit(2500, hours=4.0)   # 底价应 3000, 报 2500 偏低
    assert r.flag is not None and "对不上" in r.flag


def test_reconcile_missing_hours_flags():
    r = reconcile_credit(3000, hours=None)
    assert r.flag is not None and "工时" in r.flag


def test_reconcile_overtime_base_with_ticket():
    # 6h20m 底价 3300; 报 3500 → 拆 O=200
    r = reconcile_credit(3500, hours=6 + 20 / 60, ticket_hint=200)
    assert (r.K, r.O) == (3300, 200)
    assert r.flag is None


# ─────────────────────── reconcile 非标准档 ───────────────────────
def test_reconcile_tier_match():
    r = reconcile_credit(9000, tier="直快")
    assert (r.K, r.O) == (9000, 0.0)
    assert r.flag is None


def test_reconcile_tier_mismatch_flags():
    r = reconcile_credit(8000, tier="直快")
    assert r.flag is not None and "不符" in r.flag


def test_reconcile_tier_no_ticket_split():
    # 非标准档不拆门票, 即便金额含门票也不拆
    r = reconcile_credit(9200, tier="直快")
    assert r.O == 0.0
