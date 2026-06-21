"""
结款两轨状态  ·  core/status.py
把单一结款态拆成两条独立轨, 各走各的时钟、各找各的对手方:
  credit_status 挂账状态: 无K / 待结 / 部分 / 已结 / 直结
  cash_status   现金状态: 无M / 待  / 已   / 直结
现金分支(代收 vs 自留)不另存, 由 Order.flow(A/B/D/E/D60) 推导 —— 单一真相源。
"""
from sqlalchemy import inspect, text

# ── 枚举值 ──
CREDIT_NONE = "无K"; CREDIT_DUE = "待结"; CREDIT_PART = "部分"
CREDIT_PAID = "已结"; CREDIT_DIRECT = "直结"
CASH_NONE = "无M"; CASH_DUE = "待"; CASH_DONE = "已"; CASH_DIRECT = "直结"

COLLECT_FLOWS = {"A", "B"}          # 代收: 公司已收, 待发放给艺人/妈咪
SELFKEEP_FLOWS = {"D", "E", "D60"}  # 自留: 艺人已持, 待工资倒扣公司+妈咪份

# 运行时迁移要补的列 (create_all 只建表不改列, 线上库靠这个加列)
NEW_COLUMNS = [
    ("credit_status",    "VARCHAR(10)"),
    ("credit_paid_date", "DATE"),
    ("credit_ref",       "VARCHAR(40)"),
    ("cash_status",      "VARCHAR(10)"),
    ("cash_settle_date", "DATE"),
]


# ── 派生: 录新单 / 回填 初始两轨态 ──
def derive_status(*, credit_k=0, cash_m=0, mode="标准", flow=None,
                  credit_paid=False, cash_settled=False, void=False):
    """由 金额+档+流向 派生 (credit_status, cash_status)。作废单返回 (None, None)。"""
    if void:
        return None, None
    direct = (mode == "直结")          # 妈咪直结艺人, 公司全程不经手
    # 挂账轨
    if not credit_k:
        cs = CREDIT_NONE
    elif direct:
        cs = CREDIT_DIRECT
    else:
        cs = CREDIT_PAID if credit_paid else CREDIT_DUE
    # 现金轨
    if not cash_m:
        ms = CASH_NONE
    elif direct:
        ms = CASH_DIRECT
    else:
        ms = CASH_DONE if cash_settled else CASH_DUE
    return cs, ms


def cash_branch(flow):
    """现金分支(展示用): 代收 / 自留 / —。"""
    f = (flow or "").upper()
    if f in COLLECT_FLOWS:  return "代收"
    if f in SELFKEEP_FLOWS: return "自留"
    return "—"


# ── 看板分桶判定 (三轨) ──
def is_due_from_mama(credit_status):
    return credit_status in (CREDIT_DUE, CREDIT_PART)

def is_due_to_artist(cash_status, flow):
    return cash_status == CASH_DUE and (flow or "").upper() in COLLECT_FLOWS

def is_due_clawback(cash_status, flow):
    return cash_status == CASH_DUE and (flow or "").upper() in SELFKEEP_FLOWS


# ── 运行时迁移: 给已有 orders 表补列 (幂等, SQLite+Postgres 通用) ──
def ensure_columns(engine, table="orders"):
    have = {c["name"] for c in inspect(engine).get_columns(table)}
    added = []
    with engine.begin() as conn:
        for name, typ in NEW_COLUMNS:
            if name not in have:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {typ}'))
                added.append(name)
    return added


# ── 一次性回填: 给状态为空的历史单派生两轨态 (幂等, 只填空的) ──
def backfill(session, cash_done_before=None):
    """cash_done_before: date|None — 此日期前的现金单视为已发/已扣(对应月工资已跑),
    否则一律 待。挂账轨默认 待结(直结单自动识别为 直结)。"""
    from ..db.models import Order
    n = 0
    for o in session.query(Order).all():
        if o.credit_status is not None and o.cash_status is not None:
            continue
        void = (o.status != "已审核")
        done = bool(cash_done_before and o.biz_date and o.biz_date < cash_done_before)
        cs, ms = derive_status(credit_k=o.credit_k or 0, cash_m=o.cash_m or 0,
                               mode=o.mode, flow=o.flow,
                               cash_settled=done, void=void)
        if o.credit_status is None: o.credit_status = cs
        if o.cash_status is None:   o.cash_status = ms
        n += 1
    if n:
        session.commit()
    return n
