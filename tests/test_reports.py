"""报表 PNG: 需要 Noto CJK 字体 (Dockerfile 里装了), 本机没有就跳过。"""
import glob
from datetime import date

import pytest

from cgroup.core.intake import create_order_from_payload
from cgroup.db.models import Advance, ExchangeRate, Mama

pytestmark = pytest.mark.skipif(not glob.glob("/usr/share/fonts/**/NotoSansCJK*.ttc", recursive=True),
                                reason="没装 Noto CJK 字体")


def _orders(s):
    for p in [{"艺人": "桃子", "妈咪": "小宝", "K": 3000, "O": 150, "日期": "2026-06-01", "客人": "王总"},
              {"艺人": "桃子", "妈咪": "小宝", "M": 1000, "流向": "D", "日期": "2026-06-02"},
              {"艺人": "桃子", "妈咪": "小宝", "合作模式": "直结", "K": 800, "日期": "2026-06-03"}]:
        create_order_from_payload(p, s)
    s.commit()


def test_payslip_with_deductions_and_rate(seeded):
    from cgroup.reports.build import artist_payslip_png
    s = seeded
    _orders(s)
    s.add(Advance(subject_type="艺人", subject_name="桃子", kind="预支", amount=500, related_month="2026-06"))
    s.add(ExchangeRate(rate_date=date(2026, 6, 1), currency="RMB", real_rate=1.6, settle_rate=1.55))
    s.commit()
    from cgroup.core.names import find_artist
    png, net = artist_payslip_png(s, find_artist(s, "桃子").id, 2026, 6)
    assert png[:4] == b"\x89PNG"
    # 挂账单 2250 + 自留现金单 (700-1000=-300) = 1950; 直结不计; 扣预支 500
    assert net == pytest.approx(1950 - 500)


def test_mama_statement_excludes_direct(seeded):
    from cgroup.reports.build import mama_statement_png
    s = seeded
    _orders(s)
    png = mama_statement_png(s, s.query(Mama).one().id, period_label="测试")
    assert png[:4] == b"\x89PNG"


def test_daily_report(seeded):
    from cgroup.reports.build import daily_report_png
    s = seeded
    _orders(s)
    assert daily_report_png(s, date(2026, 6, 1))[:4] == b"\x89PNG"
    assert daily_report_png(s, date(2026, 1, 1)) is None
