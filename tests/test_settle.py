"""
结算引擎回归测试 (宪法 v1.0 · Block A+B+C)  ·  test_settle.py
覆盖: 5 预设 × 四流向 + 公司净铁律 + 艺人对平 + 闭环 + 边界。
跑: pytest tests/test_settle.py   或   python -m pytest tests
"""
import pytest

from cgroup.core.settle import (
    Order, Settlement, settle, verify_closure, PRESETS, CASH_FLOWS,
)

WP = 3000.0          # 统一工价基准
O = 200.0            # 门票


# ─────────────────────── Block A: 预设比例 / 走账 / 业绩 ───────────────────────
def test_preset_ratios():
    assert PRESETS["标准"]   == dict(a=0.70, m=0.20, c=0.10, on_books=True)
    assert PRESETS["无水单"] == dict(a=0.70, m=0.30, c=0.00, on_books=False)
    assert PRESETS["代收无水"] == dict(a=0.70, m=0.30, c=0.00, on_books=True)
    assert PRESETS["自单"]   == dict(a=0.90, m=0.00, c=0.10, on_books=True)


def test_d60_abolished():
    assert "D60" not in CASH_FLOWS


@pytest.mark.parametrize("preset,perf", [
    ("标准", True), ("自单", True),          # 公司有份 → 算业绩
    ("无水单", False), ("代收无水", False),   # 0% 单 → 不算业绩
])
def test_counts_performance(preset, perf):
    s = settle(Order(order_type="挂账", K=WP, preset=preset))
    assert s.counts_performance is perf


def test_on_books_flag():
    assert settle(Order(order_type="挂账", K=WP, preset="无水单")).on_books is False
    assert settle(Order(order_type="挂账", K=WP, preset="代收无水")).on_books is True


# ─────────────────────── Block H: 经济口径恒等 (任何单) ───────────────────────
@pytest.mark.parametrize("preset", ["标准", "代收无水", "自单"])
@pytest.mark.parametrize("otype,flow,K,M", [
    ("挂账", None, WP, 0.0),
    ("现金", "A", 0.0, WP),
    ("现金", "B", 0.0, WP),
    ("现金", "D", 0.0, WP),
    ("现金", "E", 0.0, WP),
])
def test_economic_identity(preset, otype, flow, K, M):
    p = PRESETS[preset]
    s = settle(Order(order_type=otype, K=K, M=M, O=O, wp=WP, preset=preset, flow=flow))
    assert s.artist_net == pytest.approx(p["a"] * WP + O)
    assert s.mama_net == pytest.approx(p["m"] * WP)
    assert s.company_net == pytest.approx(p["c"] * WP)          # 铁律
    # 艺人实得 (现场 + 工资单) == 经济净
    assert s.onsite_artist + s.artist_payroll == pytest.approx(p["a"] * WP + O)


# ─────────────────────── Block B: 各流向逐字段 (标准档 70/20/10) ───────────────────────
def test_flow_A():
    s = settle(Order(order_type="现金", M=WP, O=O, wp=WP, preset="标准", flow="A"))
    assert s.O_to_salary is True
    assert s.onsite_company == pytest.approx(WP + O)            # 代收全部含门票
    assert s.artist_payroll == pytest.approx(0.70 * WP + O)     # 发薪含门票
    assert s.rebate == pytest.approx(0.20 * WP)                 # 反水 m*wp
    assert s.clawback == 0.0
    assert s.company_net == pytest.approx(0.10 * WP)


def test_flow_B():
    s = settle(Order(order_type="现金", M=WP, O=O, wp=WP, preset="标准", flow="B"))
    assert s.O_to_salary is False
    assert s.onsite_company == pytest.approx(0.30 * WP)         # (m+c)*wp
    assert s.onsite_artist == pytest.approx(0.70 * WP + O)      # 现场拿七成+门票
    assert s.artist_payroll == 0.0                              # 现场已结
    assert s.rebate == pytest.approx(0.20 * WP)
    assert s.company_net == pytest.approx(0.10 * WP)


def test_flow_D():
    s = settle(Order(order_type="现金", M=WP, O=O, wp=WP, preset="标准", flow="D"))
    assert s.onsite_artist == pytest.approx(WP + O)            # 全留(含门票)
    assert s.clawback == pytest.approx(0.30 * WP)             # (m+c)*wp
    assert s.artist_payroll == pytest.approx(-0.30 * WP)       # 工资单显负
    assert s.rebate == pytest.approx(0.20 * WP)
    assert s.company_net == pytest.approx(0.10 * WP)


def test_flow_E():
    s = settle(Order(order_type="现金", M=WP, O=O, wp=WP, preset="标准", flow="E"))
    assert s.onsite_mama == pytest.approx(0.20 * WP)           # 妈咪现场拿 m*wp
    assert s.onsite_artist == pytest.approx(WP + O - 0.20 * WP)
    assert s.clawback == pytest.approx(0.10 * WP)             # c*wp
    assert s.artist_payroll == pytest.approx(-0.10 * WP)
    assert s.rebate == 0.0                                     # 妈咪现场已拿, 无反水
    assert s.company_net == pytest.approx(0.10 * WP)


# ─────────────────────── 挂账单 ───────────────────────
def test_credit_order():
    s = settle(Order(order_type="挂账", K=WP, O=O, wp=WP, preset="标准"))
    assert s.O_to_salary is True
    assert s.mama_owes_company == pytest.approx((WP + O) - 0.20 * WP)   # (a+c)*wp + O
    assert s.artist_payroll == pytest.approx(0.70 * WP + O)
    assert s.company_net == pytest.approx(0.10 * WP)


def test_credit_order_daishou_no_water():
    """代收无水: 公司 0% 但走账, 经手收发。"""
    s = settle(Order(order_type="挂账", K=WP, O=O, wp=WP, preset="代收无水"))
    assert s.company_net == 0.0
    assert s.mama_net == pytest.approx(0.30 * WP)
    assert s.mama_owes_company == pytest.approx((WP + O) - 0.30 * WP)
    assert s.counts_performance is False


# ─────────────────────── 自定义档 ───────────────────────
def test_custom_preset():
    s = settle(Order(order_type="挂账", K=WP, wp=WP, preset="自定义", a=0.8, m=0.15, c=0.05))
    assert (s.a, s.m, s.c) == (0.8, 0.15, 0.05)
    assert s.company_net == pytest.approx(0.05 * WP)


def test_custom_requires_amc():
    with pytest.raises(ValueError, match="自定义"):
        settle(Order(order_type="挂账", K=WP, preset="自定义"))


def test_custom_ratios_must_sum_to_one():
    with pytest.raises(ValueError, match="!= 1"):
        settle(Order(order_type="挂账", K=WP, preset="自定义", a=0.5, m=0.2, c=0.1))


# ─────────────────────── 边界 ───────────────────────
def test_unknown_preset():
    with pytest.raises(ValueError, match="未知分成预设"):
        settle(Order(order_type="挂账", K=WP, preset="乱填"))


def test_d60_rejected():
    with pytest.raises(ValueError, match="D60"):
        settle(Order(order_type="现金", M=WP, wp=WP, flow="D60"))


def test_cash_requires_flow():
    with pytest.raises(ValueError, match="流向"):
        settle(Order(order_type="现金", M=WP, wp=WP, flow=None))


def test_inconsistent_data_flags_not_crash():
    """旧数据口径不一致(现金含门票, M=wp+O)不应崩, 只标 needs_review; 经济口径仍对。"""
    # 现金 D, M 含门票(3150=wp3000+票150) → 实操 onsite 会多算门票, 软校验标记
    s = settle(Order(order_type="现金", M=3150, O=150, wp=3000, preset="标准", flow="D"))
    assert s.needs_review is not None and "对账不平" in s.needs_review
    assert s.company_net == pytest.approx(0.10 * 3000)   # 经济口径不受影响
    assert s.artist_net == pytest.approx(0.70 * 3000 + 150)


def test_mixed_flags_review():
    s = settle(Order(order_type="混合", K=1000, M=2000, O=O, wp=3000, preset="标准", flow="B"))
    assert s.needs_review is not None
    # 经济口径仍算出
    assert s.company_net == pytest.approx(0.10 * 3000)


def test_wp_defaults_to_k_plus_m():
    s = settle(Order(order_type="挂账", K=1200, M=300, preset="标准"))
    assert s.wp == pytest.approx(1500)


# ─────────────────────── Block H: 批量闭环 ───────────────────────
def test_closure_balances():
    orders = [
        Order(order_type="挂账", K=WP, O=O, wp=WP, preset="标准"),
        Order(order_type="现金", M=WP, O=O, wp=WP, preset="标准", flow="B"),
        Order(order_type="现金", M=2000, wp=2000, preset="自单", flow="D"),
        Order(order_type="挂账", K=5000, wp=5000, preset="代收无水"),
    ]
    v = verify_closure(orders)
    assert v["ok"], v
    assert v["diff"] == 0.0


def test_closure_excludes_off_books():
    """无水单(表外)不进闭环。"""
    orders = [
        Order(order_type="挂账", K=WP, O=O, wp=WP, preset="标准"),
        Order(order_type="挂账", K=9999, O=88, wp=9999, preset="无水单"),  # 表外, 应被排除
    ]
    v = verify_closure(orders)
    assert v["ok"], v
    # total_in 只含标准单
    assert v["total_in"] == pytest.approx(WP + O)
