"""
C组 结款引擎 (宪法 v1.0 · Block G)  ·  billing.py
单一真相源: 工单 ID = YYMMDDNN 全系统对齐; 收款按工单 ID 关联(不按行号);
每张工单单笔级 settle_status(待结/已结)。

根除 5 月最乱的坑: 行号/序号混填 + 状态两处记。
"""
from dataclasses import dataclass, field
from typing import Optional

from ..db.models import Order
from .settle import settle_db


# ─────────────────────── 工单 ID (YYMMDDNN) ───────────────────────
def make_order_id(biz_date, seq: int) -> str:
    """YYMMDDNN: 年(2)月(2)日(2)当天序号(2)。跨年跨月不撞。"""
    return f"{biz_date:%y%m%d}{int(seq):02d}"


def next_seq(existing_ids, biz_date) -> int:
    """当天已分配的工单数 + 1 (纯函数, 便于测试)。"""
    prefix = f"{biz_date:%y%m%d}"
    return sum(1 for x in existing_ids if x and x.startswith(prefix)) + 1


def assign_order_id(session, order: "Order") -> str:
    """给工单分配 YYMMDDNN (已有则不变, 幂等)。"""
    if order.order_id:
        return order.order_id
    if not order.biz_date:
        raise ValueError("工单缺 biz_date, 无法生成 order_id")
    ids = [r[0] for r in session.query(Order.order_id).all()]
    order.order_id = make_order_id(order.biz_date, next_seq(ids, order.biz_date))
    # flush 让后续 assign 的 next_seq 能看到本次分配, 防同日撞号 (autoflush=False)
    session.flush()
    return order.order_id


# ─────────────────────── 收款冲账 ───────────────────────
def parse_covers(s) -> list:
    """'26062001,26062002' → ['26062001','26062002']。"""
    return [x for x in (s or "").split(",") if x]


def order_receivable(o: "Order") -> float:
    """该挂账单公司应向妈咪收 = mama_owes_company (经引擎算)。"""
    return settle_db(o).mama_owes_company


@dataclass
class PaymentResult:
    marked: list = field(default_factory=list)    # 成功标记已结的 order_id
    missing: list = field(default_factory=list)   # 不存在的 order_id
    expected: float = 0.0                          # 覆盖工单的应收合计
    paid: float = 0.0                              # 本次收款额
    flag: Optional[str] = None


def apply_payment(session, payment, order_ids) -> PaymentResult:
    """登记一笔收款 payment, 冲 order_ids 指定的工单 → 标记已结 + 记 covers + 校验。
    校验 (Block H): 已结金额不应 > 应收; 工单须存在。部分结款(paid<应收)允许。"""
    res = PaymentResult(paid=float(payment.amount))
    payment.covers = ",".join(order_ids)
    for oid in order_ids:
        o = session.query(Order).filter_by(order_id=oid).first()
        if o is None:
            res.missing.append(oid)
            continue
        o.settle_status = "已结"
        res.expected += order_receivable(o)
        res.marked.append(oid)
    res.expected = round(res.expected, 2)
    session.add(payment)

    if res.missing:
        res.flag = f"工单不存在: {','.join(res.missing)}"
    elif res.paid - res.expected > 1:        # 已结金额 ≤ 应收
        res.flag = f"收款{res.paid:g} > 应收{res.expected:g}, 请核"
    return res
