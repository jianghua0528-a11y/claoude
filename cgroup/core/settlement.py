"""
C组 账务核心引擎  ·  settlement.py
一行报单进 → 各方应得/现场已拿/月底应结/反水/公司抽成 出。
全系统唯一计算入口 (机器人解析后、报表生成时都调它)。

口径区分 (关键, 工资口径 ≠ 闭环口径):
  · 闭环口径 (economic):  整体经济分配, 永远 ΣK+M+O = 艺人净+妈咪净+公司净
  · 实操口径 (settlement): 公司实际付/收 —— 未合作单妈咪直结, 不进公司账
"""
from dataclasses import dataclass
from typing import Optional

# 分成比例: 艺人 / 妈咪 / 公司
RATIOS = {
    "已合作": dict(artist=0.70, mama=0.20, company=0.10),
    "未合作": dict(artist=0.70, mama=0.30, company=0.00),
    "自单":   dict(artist=0.90, mama=0.00, company=0.10),
}

# 现金流向 → 艺人现场已拿的现金比例 (门票O另算)
FLOW_CASH_KEPT = {"A": 0.0, "B": 0.70, "D": 1.00, "E": 0.80, "D60": 0.60}


@dataclass
class Order:
    K: float = 0.0               # 挂账 (MYR)
    M: float = 0.0               # 现金 (MYR)
    O: float = 0.0               # 门票 (MYR)
    mode: str = "已合作"          # 合作模式: 已合作/未合作/自单
    flow: Optional[str] = None   # 现金流向 A/B/D/E/D60; 挂账单留空
    wp: Optional[float] = None   # 工价(反水基数). 留空则=K+M; 小费单可单独给


@dataclass
class Result:
    base: float = 0.0            # 分成基数 K+M
    wp: float = 0.0
    # 闭环口径 (经济分配)
    artist_net: float = 0.0
    mama_net: float = 0.0
    company_net: float = 0.0
    # 实操口径 (公司实际付/收)
    artist_due: float = 0.0          # 艺人总应得
    onsite_taken: float = 0.0        # 艺人现场已拿(现金+门票)
    artist_month_end: float = 0.0    # 月底应结(负=从工资倒扣); 未合作=0(妈咪直结)
    mama_receivable: float = 0.0     # 挂账单: 公司向妈咪应收
    mama_rebate: float = 0.0         # 现金单: 公司付妈咪反水
    company_cut: float = 0.0         # 公司抽成
    is_direct_settle: bool = False   # 未合作单(妈咪直结, 不进工资)


def onsite_cash_plus_ticket(flow, M, O):
    """艺人现场已拿 = 现金部分 + 门票.
    门票规则(2026-06-01): 流向A/挂账 → 门票月底结(现场已拿不含O); B/D/E → 现场已拿含O."""
    f = (flow or "").upper()
    if f in ("", "A"):
        return 0.0                       # 公司代收全 或 挂账单: 现场无现金, 门票月底结
    kept = FLOW_CASH_KEPT.get(f, 0.0)
    return kept * M + O                   # 留存现金 + 门票(现场已拿)


def compute(o: Order) -> Result:
    K, M, O = o.K or 0.0, o.M or 0.0, o.O or 0.0
    mode = o.mode or "已合作"
    if mode not in RATIOS:
        raise ValueError(f"未知合作模式: {mode}")
    r = RATIOS[mode]
    base = K + M
    wp = o.wp if o.wp is not None else base

    res = Result(base=base, wp=wp)

    # ── 闭环口径 (经济分配, 永远成立) ──
    res.artist_net = base * r["artist"] + O          # 门票100%归艺人
    res.mama_net = base * r["mama"]
    res.company_net = base * r["company"]

    # ── 实操口径 ──
    res.artist_due = base * r["artist"] + O
    res.onsite_taken = onsite_cash_plus_ticket(o.flow, M, O)

    if mode == "未合作":
        # 妈咪直结艺人: 不进工资 / 不进公司应收应付
        res.is_direct_settle = True
        return res

    res.artist_month_end = res.artist_due - res.onsite_taken
    res.company_cut = base * r["company"]

    if mode == "自单":
        return res

    # 已合作: 挂账单 → 公司向妈咪应收; 现金单 → 公司付妈咪反水
    if K > 0:    # 挂账(含混合单的挂账部分)
        res.mama_receivable = K * (1 - r["mama"]) + O   # 已合作 = K×0.8 + O
    if M > 0:    # 现金部分: E/D60 妈咪自留不反水
        if (o.flow or "").upper() not in ("E", "D60"):
            res.mama_rebate = wp * r["mama"]            # 反水 = 工价 × 0.2
    return res


def verify_closure(orders) -> dict:
    """闭环验证: ΣK+M+O == 艺人净+妈咪净+公司净 (经济口径)."""
    SK = sum((o.K or 0.0) for o in orders)
    SM = sum((o.M or 0.0) for o in orders)
    SO = sum((o.O or 0.0) for o in orders)
    a = m = c = 0.0
    for o in orders:
        res = compute(o)
        a += res.artist_net; m += res.mama_net; c += res.company_net
    total_in = SK + SM + SO
    total_out = a + m + c
    diff = round(total_in - total_out, 4)
    return dict(SK=SK, SM=SM, SO=SO, total_in=total_in,
                artist=a, mama=m, company=c, total_out=total_out,
                diff=diff, ok=(abs(diff) < 1e-6))
