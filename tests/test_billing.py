"""
Block G 结款回归测试  ·  test_billing.py
覆盖: order_id 格式/跨日序号/幂等 (纯函数) + apply_payment 冲账 (DB 集成)。
DB 用 conftest 的共享临时库; 本模块用独立日期 2026-06-20 与艺人名避免与其他测试串数据。
"""
from datetime import date

import pytest

from cgroup.db.session import init_db, get_session
from cgroup.db.models import Artist, Mama, Venue, Order, Payment
from cgroup.core.billing import (
    make_order_id, next_seq, assign_order_id, parse_covers,
    order_receivable, apply_payment,
)

D = date(2026, 6, 20)


# ─────────────────────── 纯函数: order_id ───────────────────────
def test_make_order_id():
    assert make_order_id(date(2026, 6, 25), 1) == "26062501"
    assert make_order_id(date(2026, 12, 2), 5) == "26120205"   # 跨月不撞


def test_next_seq_counts_same_day():
    ids = ["26062001", "26062002", "26061903"]   # 06-20 有 2 张, 06-19 一张
    assert next_seq(ids, date(2026, 6, 20)) == 3
    assert next_seq(ids, date(2026, 6, 19)) == 2
    assert next_seq(ids, date(2026, 6, 21)) == 1   # 新的一天从 01


def test_parse_covers():
    assert parse_covers("26062001,26062002") == ["26062001", "26062002"]
    assert parse_covers("") == []
    assert parse_covers(None) == []


# ─────────────────────── DB 集成 ───────────────────────
@pytest.fixture(scope="module")
def setup():
    init_db()
    s = get_session()
    art = Artist(name="BlockG艺人")
    mama = Mama(name="BlockG妈咪")
    ven = Venue(name="BlockG场所")
    s.add_all([art, mama, ven]); s.flush()
    # 两张标准挂账单: K=3000,O=200,wp=3000 → 每张妈咪应收 (3200-600)=2600
    o1 = Order(biz_date=D, artist_id=art.id, venue_id=ven.id, mama_id=mama.id,
               mode="标准", credit_k=3000, ticket_o=200, wp=3000, status="已审核")
    o2 = Order(biz_date=D, artist_id=art.id, venue_id=ven.id, mama_id=mama.id,
               mode="标准", credit_k=3000, ticket_o=200, wp=3000, status="已审核")
    s.add_all([o1, o2]); s.flush()
    assign_order_id(s, o1)
    assign_order_id(s, o2)
    s.commit()
    return dict(s=s, mama=mama.id, o1=o1.order_id, o2=o2.order_id)


def test_assign_order_id_format_and_sequence(setup):
    assert setup["o1"] == "26062001"
    assert setup["o2"] == "26062002"


def test_assign_order_id_idempotent(setup):
    s = setup["s"]
    o = s.query(Order).filter_by(order_id="26062001").first()
    before = o.order_id
    assert assign_order_id(s, o) == before   # 再调不变


def test_default_settle_status_pending(setup):
    s = setup["s"]
    o = s.query(Order).filter_by(order_id="26062001").first()
    assert o.settle_status == "待结"


def test_order_receivable(setup):
    s = setup["s"]
    o = s.query(Order).filter_by(order_id="26062001").first()
    assert order_receivable(o) == pytest.approx(2600)


def test_apply_payment_marks_settled(setup):
    s = setup["s"]
    pay = Payment(pay_date=D, mama_id=setup["mama"], amount=5200, currency="MYR")
    res = apply_payment(s, pay, [setup["o1"], setup["o2"]])
    s.commit()
    assert res.marked == ["26062001", "26062002"]
    assert res.expected == pytest.approx(5200)   # 2*2600
    assert res.flag is None
    assert pay.covers == "26062001,26062002"
    for oid in (setup["o1"], setup["o2"]):
        assert s.query(Order).filter_by(order_id=oid).first().settle_status == "已结"


def test_apply_payment_missing_order_flags(setup):
    s = setup["s"]
    pay = Payment(pay_date=D, mama_id=setup["mama"], amount=100, currency="MYR")
    res = apply_payment(s, pay, ["26069999"])
    assert res.missing == ["26069999"]
    assert res.flag is not None and "不存在" in res.flag


def test_apply_payment_overpay_flags(setup):
    s = setup["s"]
    pay = Payment(pay_date=D, mama_id=setup["mama"], amount=9000, currency="MYR")
    res = apply_payment(s, pay, [setup["o1"]])   # 应收 2600, 收 9000
    assert res.flag is not None and "应收" in res.flag
