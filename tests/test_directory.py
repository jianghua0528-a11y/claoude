"""
Block J 同名消歧回归测试  ·  test_directory.py
逐条覆盖宪法 §11 的「5 月血泪」同名坑。
"""
import pytest

from cgroup.core.directory import Entry, resolve, resolve_mama, load_entries

# §11 同名坑字典
ENTRIES = [
    Entry("雪儿", "助理", linked_to="yuki", disambig="雪儿(yuki助理)"),
    Entry("雪儿", "助理", linked_to="涵涵", disambig="雪儿(涵涵助理)"),
    Entry("安心", "主妈咪", disambig="安心(A团队)"),
    Entry("安心", "主妈咪", disambig="安心(B团队)"),
    Entry("娜娜", "主妈咪"),
    Entry("NANA", "助理", linked_to="玲姐"),
    Entry("coco", "助理", linked_to="丸子"),
    Entry("星橙", "主妈咪", linked_to=None),          # 林然妈咪; "星澄" 是错名, 不登记为别名
    Entry("小辣椒", "主妈咪"),
    Entry("小辣椒B", "主妈咪"),
    Entry("鹿闻", "助理", linked_to="子琳"),
    Entry("紫荆城", "场所", aliases=("紫禁城",)),
    Entry("旧妈咪", "主妈咪", status="停用"),
]


# ─────────────────────── 雪儿 ×2 → 弹选 ───────────────────────
def test_xueer_ambiguous():
    r = resolve(ENTRIES, "雪儿")
    assert r.status == "ambiguous"
    assert len(r.candidates) == 2


def test_xueer_resolve_mama_candidates():
    m = resolve_mama(ENTRIES, "雪儿")
    assert m.flag and "弹选" in m.flag
    assert set(m.candidates) == {"雪儿(yuki助理)", "雪儿(涵涵助理)"}


# ─────────────────────── 星澄 错名不收 ───────────────────────
def test_xingcheng_typo_rejected():
    assert resolve(ENTRIES, "星澄").status == "not_found"   # 错名
    assert resolve(ENTRIES, "星橙").status == "matched"     # 正名


def test_xingcheng_resolve_mama_flags():
    m = resolve_mama(ENTRIES, "星澄")
    assert m.mama is None and m.flag is not None


# ─────────────────────── coco 是助理不是主妈咪 ───────────────────────
def test_coco_is_assistant():
    m = resolve_mama(ENTRIES, "coco")
    assert m.assistant == "coco"
    assert m.mama == "丸子"        # 带出所属主妈咪, 不当主妈咪


# ─────────────────────── 娜娜 ≠ NANA ───────────────────────
def test_nana_distinct():
    assert resolve(ENTRIES, "娜娜").match.type == "主妈咪"
    m = resolve_mama(ENTRIES, "NANA")
    assert m.assistant == "NANA" and m.mama == "玲姐"


# ─────────────────────── 助理带出主妈咪 ───────────────────────
def test_assistant_links_to_mama():
    m = resolve_mama(ENTRIES, "鹿闻")
    assert m.mama == "子琳" and m.assistant == "鹿闻"


# ─────────────────────── 主妈咪直接命中 ───────────────────────
def test_mama_direct():
    m = resolve_mama(ENTRIES, "小辣椒")
    assert m.mama == "小辣椒" and m.assistant is None


def test_xiaolajiao_b_distinct():
    assert resolve(ENTRIES, "小辣椒B").match.name == "小辣椒B"
    assert resolve(ENTRIES, "小辣椒").status == "matched"   # 不与 B 混


# ─────────────────────── 别名命中 / 停用排除 ───────────────────────
def test_alias_match():
    r = resolve(ENTRIES, "紫禁城")
    assert r.status == "matched" and r.match.name == "紫荆城"


def test_inactive_excluded():
    assert resolve(ENTRIES, "旧妈咪").status == "not_found"


def test_unknown_name_flags():
    m = resolve_mama(ENTRIES, "查无此人")
    assert m.flag and "不在字典" in m.flag


def test_type_filter():
    # 限定类型: 安心 是主妈咪, 查艺人应查无
    assert resolve(ENTRIES, "安心", type="艺人").status == "not_found"
    assert resolve(ENTRIES, "安心", type="主妈咪").status == "ambiguous"


# ─────────────────────── DB load ───────────────────────
def test_load_entries_db():
    from cgroup.db.session import init_db, get_session
    from cgroup.db.models import MasterData
    init_db()
    s = get_session()
    yuki = MasterData(type="主妈咪", name="J测妈咪")
    s.add(yuki); s.flush()
    asst = MasterData(type="助理", name="J测助理", linked_to=yuki.id,
                      disambig="J测助理(J测妈咪)")
    s.add(asst); s.commit()
    entries = load_entries(s)
    m = resolve_mama(entries, "J测助理")
    assert m.mama == "J测妈咪" and m.assistant == "J测助理"
