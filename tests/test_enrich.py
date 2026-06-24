"""
录单接线回归测试  ·  test_enrich.py
验证 enrich_order 把 Block D(日期)/F(工时)/J(认人) 接进录单, 以及 ingest 合并 warnings。
"""
from datetime import date

import pytest

from cgroup.core.enrich import enrich_order
from cgroup.core.directory import Entry

ENTRIES = [
    Entry("子琳", "主妈咪"),
    Entry("鹿闻", "助理", linked_to="子琳"),
    Entry("丸子", "主妈咪"),
    Entry("雪儿", "助理", linked_to="yuki", disambig="雪儿(yuki助理)"),
    Entry("雪儿", "助理", linked_to="涵涵", disambig="雪儿(涵涵助理)"),
]


def _p(**kw):
    base = {"妈咪": None, "助理": None, "合作模式": "标准", "K": 0, "上班": None, "下班": None}
    base.update(kw)
    return base


# ─────────────────────── Block J 认人 ───────────────────────
def test_assistant_resolved_to_mama():
    out, w = enrich_order(_p(妈咪="鹿闻"), entries=ENTRIES)
    assert out["妈咪"] == "子琳" and out["助理"] == "鹿闻"
    assert w == []


def test_ambiguous_name_flags_with_candidates():
    out, w = enrich_order(_p(妈咪="雪儿"), entries=ENTRIES)
    assert out["_妈咪候选"] == ["雪儿(yuki助理)", "雪儿(涵涵助理)"]
    assert any("弹选" in x for x in w)


def test_unknown_mama_flags():
    out, w = enrich_order(_p(妈咪="查无此人"), entries=ENTRIES)
    assert any("不在字典" in x for x in w)


def test_no_entries_skips_recognition():
    out, w = enrich_order(_p(妈咪="随便"), entries=None)
    assert out["妈咪"] == "随便" and w == []


# ─────────────────────── Block D 日期归属 ───────────────────────
def test_date_prev_day():
    out, w = enrich_order(_p(上班="02:00"), msg_date=date(2026, 6, 23))
    assert out["日期"] == "2026-06-22" and w == []


def test_date_same_day():
    out, w = enrich_order(_p(上班="14:00"), msg_date=date(2026, 6, 23))
    assert out["日期"] == "2026-06-23" and w == []


def test_date_gray_zone_flags():
    out, w = enrich_order(_p(上班="08:45"), msg_date=date(2026, 6, 23))
    assert out["日期"] == "2026-06-23"
    assert any("灰区" in x for x in w)


# ─────────────────────── Block F 工时反推校验 ───────────────────────
def test_credit_matches_base_no_flag():
    out, w = enrich_order(_p(合作模式="标准", K=3000, 上班="20:00", 下班="23:30"))
    assert out["工时"] == 3.5
    assert w == []


def test_credit_mismatch_flags():
    out, w = enrich_order(_p(合作模式="标准", K=2500, 上班="20:00", 下班="23:30"))
    assert any("工时应有底价" in x for x in w)


def test_tier_price_not_flagged():
    # 9000 = 直快标价 → 放行, 不因 ≠ 标准底价而报
    out, w = enrich_order(_p(合作模式="标准", K=9000, 上班="20:00", 下班="23:30"))
    assert w == []


def test_non_standard_mode_skips_reconcile():
    out, w = enrich_order(_p(合作模式="自单", K=2500, 上班="20:00", 下班="23:30"))
    assert w == []   # 自单不反推


# ─────────────────────── entries_from_legacy + ingest 集成 ───────────────────────
def test_ingest_wires_engines(monkeypatch):
    import cgroup.parser.parse as P
    from cgroup.db.session import init_db, get_session
    from cgroup.db.models import Mama, MamaAssistant, Artist, Venue, ReviewItem
    import json

    init_db()
    s = get_session()
    mama = Mama(name="接线妈咪")
    s.add(mama); s.flush()
    s.add_all([MamaAssistant(name="接线助理", mama_id=mama.id),
               Artist(name="接线艺人"), Venue(name="接线场所")])
    s.commit()

    def fake_parse(raw_text, session):
        return {"orders": [{"artist": "接线艺人", "venue": "接线场所", "mama": "接线助理",
                            "mode": "标准", "K": 3000, "M": 0, "O": 0, "flow": None,
                            "start": "02:00", "end": "06:00", "biz_date": "2026-06-23",
                            "warnings": ["现金单流向不明"]}],
                "open_shifts": [], "dropped": 0}
    monkeypatch.setattr(P, "parse_reports", fake_parse)

    items = P.ingest("原始报单文本xxxxxx", "报单群", s, msg_date=date(2026, 6, 23))
    assert len(items) == 1
    rid, payload, warn = items[0]
    # 认人: 助理带出主妈咪
    assert payload["妈咪"] == "接线妈咪" and payload["助理"] == "接线助理"
    # 日期: 02:00 → 前一天
    assert payload["日期"] == "2026-06-22"
    # warnings 合并了 LLM 的
    assert "现金单流向不明" in warn
    # 落库
    ri = s.query(ReviewItem).filter_by(id=rid).first()
    saved = json.loads(ri.parsed_json)
    assert saved["妈咪"] == "接线妈咪"
