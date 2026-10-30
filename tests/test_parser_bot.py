import json
from datetime import date
from types import SimpleNamespace

import pytest

from cgroup.parser import parse as P
from cgroup.bot.main import parse_range
from cgroup.db.models import ReviewItem


def test_parse_range_cross_year():
    assert parse_range("6.15-6.20", date(2026, 7, 1)) == (date(2026, 6, 15), date(2026, 6, 20))
    assert parse_range("12.20-1.5", date(2027, 1, 10)) == (date(2026, 12, 20), date(2027, 1, 5))
    assert parse_range("12.1-12.31", date(2027, 1, 10)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert parse_range("乱写", date(2026, 7, 1)) is None
    assert parse_range("2.30-3.1", date(2026, 7, 1)) is None


class _FakeStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


def _fake_client(monkeypatch, text, stop="end_turn", seen=None):
    def stream(**kw):
        if seen is not None:
            seen.update(kw)
        return _FakeStream(SimpleNamespace(stop_reason=stop,
                                           content=[SimpleNamespace(type="text", text=text)]))
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    monkeypatch.setattr(P.anthropic, "Anthropic", lambda: client)


def test_ingest_prechecks_names(seeded, monkeypatch):
    seen = {}
    out = {"orders": [
        {"artist": "peach", "mama": "阿美", "venue": "88会所", "K": 3000, "O": 150,
         "biz_date": "2026-06-16", "customer": "王总", "warnings": []},
        {"artist": "新人", "mama": "小宝", "venue": "火星", "K": 100, "warnings": ["新场所"]},
    ], "open_shifts": [], "dropped": 0}
    _fake_client(monkeypatch, "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```", seen=seen)
    items = P.ingest("报单原文……", "报单群", seeded, tg_msg_id=1)
    assert len(items) == 2
    _, p1, w1 = items[0]
    assert p1["艺人"] == "桃子" and p1["妈咪"] == "小宝" and p1["助理"] == "阿美" and p1["场所"] == "88"
    assert w1 is None
    assert "不在字典" in items[1][2] and "新场所" in items[1][2]
    assert seeded.query(ReviewItem).count() == 2
    # 规则块缓存, 字典块在缓存点之后; 开了服务端 fallback
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "桃子(peach,桃桃)" in seen["system"][1]["text"]
    assert seen["fallbacks"] == "default"


@pytest.mark.parametrize("text,stop,msg", [
    ("not json", "end_turn", "不是 JSON"),
    ("", "refusal", "拒绝"),
    ('{"orders": [', "max_tokens", "截断"),
])
def test_parse_failures_raise(seeded, monkeypatch, text, stop, msg):
    _fake_client(monkeypatch, text, stop)
    with pytest.raises(P.ParseError, match=msg):
        P.parse_reports("x", seeded)
