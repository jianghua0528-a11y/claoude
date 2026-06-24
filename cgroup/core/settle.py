"""
C组 结算引擎 (宪法 v1.0)  ·  settle.py
覆盖 Block A 分成 + Block B 流向 + Block C 门票进薪资。

全系统唯一计算入口: 一行工单进 → 各方应得 / 现场代收 / 月底倒扣 / 反水 / 公司净 出。

口径区分 (关键):
  · 经济(闭环)口径: 永远 a*wp + m*wp + c*wp = wp; 门票 O 100% 归艺人, 闭环再加 O。
                    artist_net / mama_net / company_net 三者任何单都成立。
  · 实操口径: 公司实际现场代收 / 月底发薪 / 倒扣 / 反水, 按流向 A/B/D/E 走不同路径,
              但 **公司最终净恒 = c*wp (Block B 铁律)**。

金额约定 (内部统一, 杜绝 5 月"门票双算"坑):
  · K = 挂账工价部分 (不含门票)。
  · M = 现金工价部分 (不含门票)。
  · O = 门票, 永远单列, 100% 归艺人, 不参与分成。
  · wp = 标准工价 (分账基准); 不给则默认 K+M。门票 O 始终在 wp 之外另加。
  → 现场"含门票"的现金 = 该路径的工价现金 + O, 由引擎按流向显式加 O。
"""
from dataclasses import dataclass
from typing import Optional

# ── Block A: 5 个分成预设 (a 艺人 / m 妈咪 / c 公司 / on_books 是否走公司账) ──
PRESETS = {
    "标准":     dict(a=0.70, m=0.20, c=0.10, on_books=True),   # 有妈咪正常单
    "无水单":   dict(a=0.70, m=0.30, c=0.00, on_books=False),  # 妈咪直付艺人, 表外纯记录
    "代收无水": dict(a=0.70, m=0.30, c=0.00, on_books=True),   # 公司 0% 但仍经手收发
    "自单":     dict(a=0.90, m=0.00, c=0.10, on_books=True),   # 艺人自带客
    # "自定义": 调用方给 a/m/c, on_books 恒 True
}

CASH_FLOWS = ("A", "B", "D", "E")   # Block B: D60 已作废


@dataclass
class Order:
    order_type: str                 # 挂账 / 现金 / 混合
    K: float = 0.0                  # 挂账工价 (不含门票)
    M: float = 0.0                  # 现金工价 (不含门票)
    O: float = 0.0                  # 门票 (100% 归艺人)
    wp: Optional[float] = None      # 标准工价; None → K+M
    preset: str = "标准"
    a: Optional[float] = None       # 仅"自定义"档给
    m: Optional[float] = None
    c: Optional[float] = None
    flow: Optional[str] = None      # 现金/混合单必填: A/B/D/E


@dataclass
class Settlement:
    # 解析出的比例 / 基数
    wp: float = 0.0
    a: float = 0.0
    m: float = 0.0
    c: float = 0.0
    on_books: bool = True           # 是否走公司账 (无水单=False, 不进收款/工资/闭环)
    counts_performance: bool = False  # 是否算业绩/分红 (公司有份: c>0 且 on_books)
    O_to_salary: bool = False       # 门票是否进发薪 (Block C: 挂账 + 现金流向A)
    # ── 经济(闭环)口径 (永远成立) ──
    artist_net: float = 0.0         # = a*wp + O
    mama_net: float = 0.0           # = m*wp
    company_net: float = 0.0        # = c*wp (铁律)
    # ── 实操口径 ──
    artist_payroll: float = 0.0     # 月底工资单本单金额 (公司发薪为正; D/E 倒扣为负)
    onsite_artist: float = 0.0      # 艺人现场实拿 (含门票部分)
    onsite_mama: float = 0.0        # 妈咪现场拿 (仅流向E)
    onsite_company: float = 0.0     # 公司现场代收
    clawback: float = 0.0           # 倒扣额 (D/E, 正数; 工资单显负)
    rebate: float = 0.0             # 反水 (发薪时公司付妈咪)
    mama_owes_company: float = 0.0  # 挂账单: 妈咪向公司应结
    # ── 标记 ──
    needs_review: Optional[str] = None

    # ── 兼容别名 (切换期: 旧 settlement.Result 的字段名映射到宪法口径) ──
    @property
    def artist_month_end(self) -> float:      # 旧名: 月底应结 → 工资单本单金额
        return self.artist_payroll

    @property
    def mama_receivable(self) -> float:       # 旧名: 挂账单妈咪应结公司
        return self.mama_owes_company

    @property
    def mama_rebate(self) -> float:           # 旧名: 现金单反水
        return self.rebate

    @property
    def is_direct_settle(self) -> bool:       # 旧名: 直结(妈咪直结不进公司账) = 表外
        return not self.on_books


def _ratios(o: "Order"):
    """取 (a, m, c, on_books); 校验 a+m+c==1。"""
    if o.preset == "自定义":
        a, m, c = o.a, o.m, o.c
        if None in (a, m, c):
            raise ValueError("自定义档必须显式给出 a/m/c")
        on_books = True
    else:
        p = PRESETS.get(o.preset)
        if p is None:
            raise ValueError(f"未知分成预设: {o.preset!r} (合法: {list(PRESETS) + ['自定义']})")
        a, m, c, on_books = p["a"], p["m"], p["c"], p["on_books"]
    if abs(a + m + c - 1.0) > 1e-9:
        raise ValueError(f"分成比例 a+m+c != 1: {a}+{m}+{c}")
    return a, m, c, on_books


def settle(o: "Order") -> "Settlement":
    a, m, c, on_books = _ratios(o)
    K, M, O = o.K or 0.0, o.M or 0.0, o.O or 0.0
    wp = o.wp if o.wp is not None else (K + M)

    s = Settlement(wp=wp, a=a, m=m, c=c, on_books=on_books)
    s.counts_performance = on_books and c > 0

    # ── 经济(闭环)口径, 任何单都成立 ──
    s.artist_net = a * wp + O
    s.mama_net = m * wp
    s.company_net = c * wp

    # ── 无水单(表外): 妈咪直付艺人, 公司不经手 → 实操字段全 0, 仅留经济口径 ──
    if not on_books:
        s.O_to_salary = True            # 门票归艺人(随妈咪直付), 不经公司发薪
        return s

    # ── 挂账单: 妈咪向客人收 (K+O), 自留 m*wp, 余结公司; 公司发薪给艺人 (含门票) ──
    if o.order_type == "挂账":
        s.O_to_salary = True
        s.mama_owes_company = (K + O) - m * wp          # = (a+c)*wp + O (当 K=wp)
        s.artist_payroll = a * wp + O                   # 公司发薪, 门票进薪资
        return _verify(s)

    # ── 现金单: 分成 × 流向参数化 ──
    if o.order_type == "现金":
        flow = (o.flow or "").upper()
        if flow == "D60":
            raise ValueError("流向 D60 已按宪法 v1.0 作废, 请改用 A/B/D/E")
        if flow not in CASH_FLOWS:
            raise ValueError(f"现金单流向必须是 {CASH_FLOWS} 之一, 收到: {o.flow!r}")

        if flow == "A":                       # 公司代收全部现金(含门票) → 门票进薪资
            s.O_to_salary = True
            s.onsite_company = M + O
            s.artist_payroll = a * wp + O
            s.rebate = m * wp
        elif flow == "B":                     # 公司代收三成; 艺人现场拿七成+门票 → 不进薪资
            s.onsite_company = (m + c) * wp
            s.onsite_artist = a * wp + O
            s.artist_payroll = 0.0
            s.rebate = m * wp
        elif flow == "D":                     # 艺人现场全留(含门票); 月底倒扣 (m+c)*wp
            s.onsite_artist = M + O
            s.clawback = (m + c) * wp
            s.artist_payroll = -s.clawback    # 工资单显 -(m+c)*wp
            s.rebate = m * wp
        elif flow == "E":                     # 妈咪现场自留 m*wp; 艺人留其余; 倒扣 c*wp
            s.onsite_mama = m * wp
            s.onsite_artist = (M + O) - m * wp
            s.clawback = c * wp
            s.artist_payroll = -s.clawback    # 工资单显 -c*wp
            s.rebate = 0.0                     # 妈咪现场已拿, 无反水
        return _verify(s)

    # ── 混合单: 宪法 settle() 仅定义纯挂账/纯现金, 实操口径未覆盖 → 不自行假设 ──
    if o.order_type == "混合":
        s.needs_review = ("混合单(挂账+现金)实操口径宪法 v1.0 未定义, "
                          "经济口径已算, 实操路径请业务方(阿豪)确认")
        return s

    raise ValueError(f"未知单类型: {o.order_type!r} (合法: 挂账/现金/混合)")


def _verify(s: "Settlement") -> "Settlement":
    """内部一致性自检 (开发期断言, 防引擎写错):
       ① 公司净恒 = c*wp (Block B 铁律);
       ② 艺人实得 (现场 + 工资单) == 经济口径 artist_net。"""
    assert abs(s.company_net - s.c * s.wp) < 1e-6, "公司净 != c*wp"
    artist_total = s.onsite_artist + s.artist_payroll
    assert abs(artist_total - s.artist_net) < 1e-6, (
        f"艺人实得对不平: 现场{s.onsite_artist}+工资{s.artist_payroll} != 净{s.artist_net}")
    return s


def verify_closure(orders) -> dict:
    """闭环验证 (Block H, 经济/工价口径): Σ(wp+O) == 艺人净+妈咪净+公司净。
    无水单 (on_books=False) 表外, 不进闭环。"""
    total_in = a = m = c = 0.0
    for o in orders:
        s = settle(o)
        if not s.on_books:
            continue
        total_in += s.wp + (o.O or 0.0)
        a += s.artist_net
        m += s.mama_net
        c += s.company_net
    total_out = a + m + c
    diff = round(total_in - total_out, 6)
    return dict(total_in=round(total_in, 6), artist=round(a, 6), mama=round(m, 6),
                company=round(c, 6), total_out=round(total_out, 6),
                diff=diff, ok=abs(diff) < 1e-6)


# ─────────────────────── DB Order 适配 ───────────────────────
def order_from_db(o) -> "Order":
    """DB Order(duck-typed: credit_k/cash_m/ticket_o/preset/cust_a/m/c/flow/wp) → 宪法 settle.Order。
    order_type 由 K/M 推断; preset 直读 (自定义档用 cust_a/m/c)。"""
    K = (o.credit_k or 0.0)
    M = (o.cash_m or 0.0)
    Ot = (o.ticket_o or 0.0)
    order_type = "混合" if (K > 0 and M > 0) else ("现金" if M > 0 else "挂账")
    return Order(order_type=order_type, K=K, M=M, O=Ot, wp=o.wp,
                 preset=getattr(o, "preset", None) or "标准",
                 a=getattr(o, "cust_a", None), m=getattr(o, "cust_m", None),
                 c=getattr(o, "cust_c", None), flow=o.flow)


def settle_db(o) -> "Settlement":
    """DB Order 直接结算 (适配 + settle 一步到位)。"""
    return settle(order_from_db(o))
