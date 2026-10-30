from datetime import date

import pytest

from cgroup.core.settlement import Order as EO, compute, verify_closure
from cgroup.core.intake import create_order_from_payload, IntakeError
from cgroup.core.names import find_artist, find_mama, find_venue
from cgroup.core.queries import dashboard_stats, artist_summary, day_summary
from cgroup.db.models import Order, Advance, Expense


def test_standard_credit_order():
    r = compute(EO(K=3000, O=150, mode="标准"))
    assert r.artist_due == 3000 * 0.7 + 150
    assert r.artist_month_end == r.artist_due          # 挂账单现场没拿钱
    assert r.mama_receivable == 3000 * 0.8 + 150
    assert r.company_cut == 300


def test_selfkeep_cash_is_negative_month_end():
    r = compute(EO(M=1000, O=100, mode="标准", flow="D"))
    assert r.onsite_taken == 1100
    assert r.artist_month_end == pytest.approx(700 + 100 - 1100)
    assert r.mama_rebate == 200


def test_direct_settle_not_in_payroll():
    r = compute(EO(K=2000, mode="直结"))
    assert r.is_direct_settle and r.artist_month_end == 0 and r.mama_receivable == 0


def test_closure_holds_for_all_modes():
    orders = [EO(K=3000, O=150, mode="标准"), EO(M=800, mode="自单", flow="B"),
              EO(K=1000, M=500, mode="直结", flow="E"), EO(K=600, mode="全归艺人")]
    assert verify_closure(orders)["ok"]


def test_name_resolution(seeded):
    s = seeded
    assert find_artist(s, "Peach").name == "桃子"
    assert find_artist(s, "桃子C组").name == "桃子"
    assert find_mama(s, "宝姐").name == "小宝"
    assert find_mama(s, "阿美").name == "小宝"          # 助理认回主妈咪
    assert find_venue(s, "88会所").name == "88"
    assert find_artist(s, "不存在") is None


def test_intake_resolves_aliases_and_assistant(seeded):
    s = seeded
    o = create_order_from_payload({"艺人": "peach", "妈咪": "阿美", "场所": "88会所", "K": "3000",
                                   "O": 150, "日期": "2026-06-16"}, s)
    s.commit()
    assert o.artist_id == find_artist(s, "桃子").id
    assert o.mama_id and o.mode == "标准" and o.booker == "阿美"
    assert o.credit_status == "待结" and o.cash_status == "无M"
    assert o.biz_date == date(2026, 6, 16)


@pytest.mark.parametrize("payload,msg", [
    ({"艺人": "谁"}, "不在字典"),
    ({"艺人": "桃子", "妈咪": "陌生人"}, "妈咪"),
    ({"艺人": "桃子", "合作模式": "乱写"}, "未知分成档"),
    ({"艺人": "桃子", "合作模式": "标准"}, "必须有妈咪"),
    ({"艺人": "桃子", "M": 100, "流向": "Z"}, "流向"),
    ({"艺人": "桃子", "K": "三千"}, "数字"),
])
def test_intake_rejects_bad_payload(seeded, payload, msg):
    with pytest.raises(IntakeError, match=msg):
        create_order_from_payload(payload, seeded)


def _order(s, day, **kw):
    o = create_order_from_payload({"艺人": "桃子", "妈咪": "小宝", "日期": day.isoformat(), **kw}, s)
    s.commit()
    return o


def test_dashboard_month_filter(seeded):
    s = seeded
    _order(s, date(2026, 5, 20), K=1000)
    _order(s, date(2026, 6, 1), K=2000, O=150)
    s.add(Expense(spend_date=date(2026, 6, 3), category="伙食", amount=50))
    s.commit()
    june = dashboard_stats(s, 2026, 6)
    assert june["n"] == 1 and june["SK"] == 2000 and june["expense"] == 50
    assert june["profit"] == pytest.approx(200 - 50)
    # 欠款不分月: 两单都还待结
    mama_id = s.query(Order).first().mama_id
    assert june["due_mama"][mama_id] == pytest.approx(800 + 1600 + 150)
    assert dashboard_stats(s)["n"] == 2


def test_artist_summary_deducts_advances(seeded):
    s = seeded
    o = _order(s, date(2026, 6, 2), K=1000)
    s.add(Advance(subject_type="艺人", subject_name="桃子", kind="预支", amount=300, related_month="2026-06"))
    s.add(Advance(subject_type="艺人", subject_name="桃子", kind="罚款", amount=50, related_month="2026-07"))
    s.commit()
    d = artist_summary(s, o.artist_id, 2026, 6, artist_name="桃子")
    assert d["wage"] == 700 and d["deduct"] == 300 and d["net"] == 400


def test_void_excluded_from_day_summary(seeded):
    s = seeded
    o = _order(s, date(2026, 6, 5), K=500)
    _order(s, date(2026, 6, 5), K=700)
    o.status = "作废"
    s.commit()
    d = day_summary(s, date(2026, 6, 5))
    assert d["n"] == 1 and d["K"] == 700
