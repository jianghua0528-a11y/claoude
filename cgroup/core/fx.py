"""
C组 汇差引擎 (宪法 v1.0 · Block E)  ·  fx.py
现金流模型 (5 月否决基准法后锁定): 汇差只认换汇流水, 不双算。

  汇差 = Σ_每笔换汇 (实收RMB − 换出外币 × 该笔入账月结算率)

口径要点:
  · 换汇流水 = 公司次月发薪时的实际银行换汇: 换出 X 外币 → 实收 Y RMB。
    真实率不单独存, 藏在 in_rmb / out_amount 里。
  · 减的是该笔钱「入账月份」的结算率 (固定 1.65 则无所谓; 改了按入账月的率)。
  · 收款流水三币种汇总只用于对账(够不够换), **不进汇差公式**。
  · 汇差 100% 公司隐性收入, 不分艺人 / 妈咪。
"""
from dataclasses import dataclass
from typing import Optional

# 结算率配置 (示例: 2026-05)。真实业务由 ExchangeRate 表/effective_from 提供。
RATES_2026_05 = {"MYR": 1.65, "USDT": 6.72}


@dataclass
class FxRow:
    out_ccy: str
    out_amount: float
    in_rmb: float
    fx_date: Optional[object] = None       # date; 用于按入账月取率
    note: Optional[str] = None


def real_rate(row) -> float:
    """该笔实际换汇率 (隐含) = 实收RMB / 换出外币。"""
    return row.in_rmb / row.out_amount


def _resolve_rate(rate_lookup, ccy, when=None) -> float:
    """rate_lookup 可为 dict{ccy:rate} 或 callable(ccy, when)->rate。"""
    if callable(rate_lookup):
        return rate_lookup(ccy, when)
    return rate_lookup[ccy]


def row_spread(row, rate_lookup) -> float:
    """单笔汇差 = 实收RMB − 换出外币 × 结算率(入账月)。
    row 可为 FxRow 或 DB Fx (鸭子类型: out_ccy/out_amount/in_rmb[/fx_date])。"""
    rate = _resolve_rate(rate_lookup, row.out_ccy, getattr(row, "fx_date", None))
    return row.in_rmb - row.out_amount * rate


def total_spread(rows, rate_lookup) -> float:
    """汇差合计 (Block E)。100% 公司隐性收入。"""
    return round(sum(row_spread(r, rate_lookup) for r in rows), 2)


def monthly_spread(session, year: int, month: int, rate_lookup) -> float:
    """某月换汇流水的汇差合计 (从 Fx 表取数)。"""
    from ..db.models import Fx
    rows = [r for r in session.query(Fx).all()
            if r.fx_date and r.fx_date.year == year and r.fx_date.month == month]
    return total_spread(rows, rate_lookup)
