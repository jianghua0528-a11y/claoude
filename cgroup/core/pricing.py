"""
C组 挂账识别 / 工时定价 (宪法 v1.0 · Block F)  ·  pricing.py
从上下班算工时 → 应有底价(标准平单) / 档位标价 → 反推挂账 K 与门票 O 的拆分。

口径要点:
  · 工时反推 / 门票拆分 **只对「标准平单」做**; 其他档位按报备价匹配标价, 不反推、不拆门票。
  · 门票永远单列(O), 100% 归艺人; K = 工价部分(底价 + 加时), 不含门票。
  · 反推对不上 → flag 让业务方核, 绝不瞎猜。
"""
import math
from dataclasses import dataclass
from typing import Optional

# ── 档位标价表 (配置化, 便于改价/加工类) ──
# 标准 = None: 走工时反推; 其余为固定标价。
TIER_PRICE = {
    "标准": None,
    "直快": 9000.0,
    "平快": 10000.0,
    "职业": 11000.0,
    "平夜": 12000.0,
}

# ── 标准平单工时反推参数 ──
OT_THRESHOLD = 5.5        # 加时触发门槛 (h); 5.0–5.5 仍平 3000
OT_BASE_FROM = 5.0        # 加时基数 = 工时 − 5h
OT_RATE = 150.0           # 每小时加时费
BASE_SHORT = 2500.0       # < 3.5h
BASE_FLAT = 3000.0        # 3.5–5.5h (及加时单底价)


def work_hours(start: str, end: str) -> Optional[float]:
    """从 'HH:MM' 上下班算工时; 下班 ≤ 上班视为跨夜(+24h)。无法解析返回 None。"""
    def _m(t):
        try:
            h, m = str(t).strip().split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return None
    t0, t1 = _m(start), _m(end)
    if t0 is None or t1 is None:
        return None
    if t1 <= t0:
        t1 += 24 * 60
    return (t1 - t0) / 60.0


def overtime_fee(hours: float) -> float:
    """加时费: 仅 > 5.5h 触发; 基数 = 工时 − 5h, 每小时 150, 向上取整到整小时。
    宪法例: 6h20m(=6.333h) → 基数 1h20m → 向上取整 2h × 150 = 300。"""
    if hours <= OT_THRESHOLD:
        return 0.0
    ot_hours = math.ceil(hours - OT_BASE_FROM - 1e-9)   # 向上取整; epsilon 防浮点误进位
    return ot_hours * OT_RATE


def standard_base(hours: float) -> float:
    """标准平单底价 (按工时反推)。"""
    if hours < 3.5:
        return BASE_SHORT
    if hours <= OT_THRESHOLD:
        return BASE_FLAT
    return BASE_FLAT + overtime_fee(hours)


def tier_price(tier: str, hours: Optional[float] = None) -> Optional[float]:
    """档位应有标价。标准档需给工时(走反推); 未知档返回 None。"""
    if tier not in TIER_PRICE:
        return None
    p = TIER_PRICE[tier]
    if p is not None:
        return p
    return standard_base(hours) if hours is not None else None


@dataclass
class CreditCheck:
    K: float                       # 挂账工价 (不含门票)
    O: float                       # 拆出的门票
    expected: Optional[float]      # 应有底价/标价
    hours: Optional[float]
    flag: Optional[str] = None     # 命中必问/对不上时的提示


def reconcile_credit(reported, hours: Optional[float] = None,
                     tier: str = "标准", ticket_hint: Optional[float] = None) -> CreditCheck:
    """报备挂账 reported 反推 (K, O) + 校验 flag。
    · 标准平单: 工时反推应有底价; 报的=底价→无门票; 报的=底价+某数→某数疑为门票(对门票行核);
                报的<底价→对不上 flag。
    · 其他档位: 报备价匹配标价, 不反推不拆门票。"""
    reported = float(reported)

    # ── 非标准档: 匹配标价 ──
    if tier != "标准":
        std = TIER_PRICE.get(tier)
        if std is None:
            return CreditCheck(reported, 0.0, None, hours, f"未知档位: {tier}")
        flag = None if abs(reported - std) < 1 else f"{tier}档报备价{reported:g}与标价{std:g}不符,请核"
        return CreditCheck(reported, 0.0, std, hours, flag)

    # ── 标准平单 ──
    if hours is None:
        return CreditCheck(reported, 0.0, None, None, "标准单缺工时,无法反推底价,请核")
    base = standard_base(hours)
    diff = round(reported - base, 2)

    if abs(diff) < 1:                       # 报的 = 底价 → 无门票
        return CreditCheck(reported, 0.0, base, hours, None)
    if diff > 0:                            # 报的 = 底价 + 门票
        flag = None
        if ticket_hint is not None and abs(diff - float(ticket_hint)) >= 1:
            flag = f"拆出门票{diff:g}与门票行{float(ticket_hint):g}不符,请核"
        return CreditCheck(base, diff, base, hours, flag)
    # 报的 < 底价 → 对不上
    return CreditCheck(reported, 0.0, base, hours,
                       f"报挂账{reported:g}<工时应有底价{base:g},对不上,请核")
